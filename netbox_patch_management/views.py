from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
from django.views import View

from dcim.models import Device
from extras.models import Script
from netbox.ui import layout
from netbox.ui.panels import CommentsPanel, ObjectAttributesPanel, ObjectsTablePanel
from netbox.ui import attrs
from extras.ui.panels import CustomFieldsPanel, TagsPanel
from netbox.object_actions import BulkExport
from netbox.views import generic
from utilities.request import safe_for_redirect
from utilities.views import register_model_view
from virtualization.models import VirtualMachine

from . import filtersets, forms, tables
from .backends import get_backend
from .jobs import StartPatchJob
from .logic import is_locked, plannable_objects, record_patch, update_class_of
from .models import PatchLog, UpdateClass

IMPACT_SCRIPT = 'ImpactAnalyse'          # netbox-abhaengigkeiten; field names are an interface
PARAM_FOR = {Device: 'devices', VirtualMachine: 'vms'}


def impact_base_url():
    script = Script.objects.filter(name=IMPACT_SCRIPT).first()
    return reverse('extras:script', args=[script.pk]) if script else None


def impact_url(objs, base=None):
    """Link to the impact analysis (maintenance scenario) with the objects preselected."""
    base = base or impact_base_url()
    if not base:
        return None
    # ponytail: GET link, ~1000 objects before URL length limits; switch to POST if classes get that big
    params = [('szenario', 'wartung')] + [(PARAM_FOR[type(o)], o.pk) for o in objs]
    return f'{base}?{urlencode(params)}'


def selected_objects(request, action):
    """Devices/VMs from ?devices=..&vms=.., restricted to the user's permission."""
    data = request.POST if request.method == 'POST' else request.GET
    objs = []
    for model, param in PARAM_FOR.items():
        ids = [int(x) for x in data.getlist(param) if x.isdigit()]
        if ids:
            objs += list(model.objects.restrict(request.user, action).filter(pk__in=ids))
    return objs


def return_url(request, default):
    url = request.POST.get('return_url') or request.GET.get('return_url')
    return url if url and safe_for_redirect(url) else default


# ---------------------------------------------------------------------------
# Update classes
# ---------------------------------------------------------------------------

class UpdateClassPanel(ObjectAttributesPanel):
    name = attrs.TextAttr('name')
    interval_days = attrs.NumericAttr('interval_days', label=_l('Interval (days)'))
    maintenance_window = attrs.TextAttr('maintenance_window')
    backend_job_ref = attrs.TextAttr('backend_job_ref', label=_l('Backend job'))
    description = attrs.TextAttr('description')


@register_model_view(UpdateClass, 'list', path='', detail=False)
class UpdateClassListView(generic.ObjectListView):
    queryset = UpdateClass.objects.all()
    filterset = filtersets.UpdateClassFilterSet
    filterset_form = forms.UpdateClassFilterForm
    table = tables.UpdateClassTable


@register_model_view(UpdateClass)
class UpdateClassView(generic.ObjectView):
    queryset = UpdateClass.objects.all()
    template_name = 'netbox_patch_management/updateclass.html'
    layout = layout.SimpleLayout(
        left_panels=[UpdateClassPanel(), TagsPanel()],
        right_panels=[CommentsPanel(), CustomFieldsPanel()],
        bottom_panels=[ObjectsTablePanel('netbox_patch_management.patchlog',
                                         filters={'update_class_id': lambda ctx: ctx['object'].pk},
                                         title=_l('Patch log'))],
    )


@register_model_view(UpdateClass, 'add', detail=False)
@register_model_view(UpdateClass, 'edit')
class UpdateClassEditView(generic.ObjectEditView):
    queryset = UpdateClass.objects.all()
    form = forms.UpdateClassForm


@register_model_view(UpdateClass, 'delete')
class UpdateClassDeleteView(generic.ObjectDeleteView):
    queryset = UpdateClass.objects.all()


@register_model_view(UpdateClass, 'bulk_delete', path='delete', detail=False)
class UpdateClassBulkDeleteView(generic.BulkDeleteView):
    queryset = UpdateClass.objects.all()
    filterset = filtersets.UpdateClassFilterSet
    table = tables.UpdateClassTable


# ---------------------------------------------------------------------------
# Patch log (read-only, exportable)
# ---------------------------------------------------------------------------

@register_model_view(PatchLog, 'list', path='', detail=False)
class PatchLogListView(generic.ObjectListView):
    queryset = PatchLog.objects.select_related('update_class', 'object_type')
    filterset = filtersets.PatchLogFilterSet
    filterset_form = forms.PatchLogFilterForm
    table = tables.PatchLogTable
    actions = (BulkExport,)  # append-only log: export only, no add/edit/delete


# ---------------------------------------------------------------------------
# Planning, record, start
# ---------------------------------------------------------------------------

class PlanningView(PermissionRequiredMixin, View):
    permission_required = 'netbox_patch_management.view_updateclass'

    def get(self, request):
        form = forms.PlanningForm(request.GET or None)
        rows, update_class = [], None
        if form.is_valid():
            update_class = form.cleaned_data['update_class']
            querysets = [m.objects.restrict(request.user, 'view') for m in PARAM_FOR]
            rows = plannable_objects(update_class, form.cleaned_data['day'] or date.today(),
                                     only_due=not form.cleaned_data['show_all'], querysets=querysets)
            backend = get_backend()
            base = impact_base_url()
            last_logs = {}
            for log in PatchLog.objects.filter(object_id__in=[r['obj'].pk for r in rows]).order_by('created'):
                last_logs[(log.object_type_id, log.object_id)] = log
            for r in rows:
                obj = r['obj']
                r['param'] = PARAM_FOR[type(obj)]
                r['last_log'] = last_logs.get((ContentType.objects.get_for_model(obj).pk, obj.pk))
                r['impact_url'] = impact_url([obj], base)
                r['ready'], r['reason'] = backend.can_start(obj) if backend else (False, '')
        backend = get_backend()
        return render(request, 'netbox_patch_management/planning.html', {
            'form': form, 'rows': rows, 'update_class': update_class,
            'impact_base_url': impact_base_url(), 'backend': backend,
            'can_start': backend is not None and request.user.has_perm('netbox_patch_management.start_patch_job'),
            'can_record': request.user.has_perm('netbox_patch_management.add_patchlog'),
            'return_url': request.get_full_path(),
        })


class RecordPatchView(PermissionRequiredMixin, View):
    permission_required = 'netbox_patch_management.add_patchlog'
    template_name = 'netbox_patch_management/record.html'

    def _context(self, request, form, objs):
        return {'form': form, 'objs': [o for o in objs if not is_locked(o)],
                'locked': [o for o in objs if is_locked(o)], 'param_for': PARAM_FOR,
                'return_url': return_url(request, reverse('plugins:netbox_patch_management:planning'))}

    def get(self, request):
        objs = selected_objects(request, 'change')
        return render(request, self.template_name, self._context(request, forms.RecordPatchForm(), objs))

    def post(self, request):
        objs = selected_objects(request, 'change')
        form = forms.RecordPatchForm(request.POST)
        if not form.is_valid() or not objs:
            if not objs:
                messages.error(request, _('No systems selected.'))
            return render(request, self.template_name, self._context(request, form, objs))
        logged, refused = record_patch(objs, form.cleaned_data['day'], form.cleaned_data['comment'], request.user)
        if logged:
            messages.success(request, _('Patch recorded for %(n)d system(s).') % {'n': len(logged)})
        if refused:
            messages.warning(request, _('Refused (patch lock): %(names)s') % {
                'names': ', '.join(str(o) for o in refused)})
        return redirect(return_url(request, reverse('plugins:netbox_patch_management:patchlog_list')))


class StartPatchView(PermissionRequiredMixin, View):
    permission_required = 'netbox_patch_management.start_patch_job'
    template_name = 'netbox_patch_management/start.html'

    def _rows(self, backend, objs):
        rows = []
        for obj in objs:
            update_class = update_class_of(obj)
            ok, reason = backend.can_start(obj)
            if is_locked(obj):
                ok, reason = False, _('Patch lock')
            elif update_class is None or not update_class.backend_job_ref:
                ok, reason = False, _('Update class has no backend job')
            rows.append({'obj': obj, 'update_class': update_class, 'ok': ok, 'reason': reason,
                         'param': PARAM_FOR[type(obj)]})
        return rows

    def dispatch(self, request, *args, **kwargs):
        self.backend = get_backend()
        if self.backend is None and request.user.is_authenticated:
            messages.error(request, _('No patch backend configured.'))
            return redirect('plugins:netbox_patch_management:planning')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        rows = self._rows(self.backend, selected_objects(request, 'change'))
        return render(request, self.template_name, {
            'rows': rows, 'backend': self.backend, 'ready': [r for r in rows if r['ok']],
            'return_url': return_url(request, reverse('plugins:netbox_patch_management:planning')),
        })

    def post(self, request):
        ready = [r['obj'] for r in self._rows(self.backend, selected_objects(request, 'change')) if r['ok']]
        if not ready:
            messages.error(request, _('None of the selected systems can be started.'))
            return redirect(return_url(request, reverse('plugins:netbox_patch_management:planning')))
        job = StartPatchJob.enqueue(
            user=request.user,
            object_keys=[(ContentType.objects.get_for_model(o).pk, o.pk) for o in ready],
        )
        messages.success(request, _('Start of %(n)d patch job(s) enqueued.') % {'n': len(ready)})
        return redirect(job.get_absolute_url())

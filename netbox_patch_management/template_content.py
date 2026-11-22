from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from urllib.parse import urlencode

from netbox.plugins import PluginTemplateExtension

from .backends import BackendError, get_backend
from .logic import due_date, effective_interval, ems_state_of, is_locked, last_patched, update_class_of
from .models import PatchLog
from .views import PARAM_FOR, impact_url


class PatchPanel(PluginTemplateExtension):
    models = ['dcim.device', 'virtualization.virtualmachine']

    def right_page(self):
        obj = self.context['object']
        request = self.context['request']
        update_class = update_class_of(obj)
        interval = effective_interval(obj, update_class)
        last = last_patched(obj)
        locked = is_locked(obj)
        query = urlencode({PARAM_FOR[type(obj)]: obj.pk, 'return_url': obj.get_absolute_url()})

        backend, can_start, reason = get_backend(), False, ''
        if backend and not locked and request.user.has_perm('netbox_patch_management.start_patch_job'):
            try:
                can_start, reason = backend.can_start(obj)
            except BackendError as exc:
                reason = str(exc)
            if can_start and not (update_class and update_class.backend_job_ref):
                can_start = False

        ems = ems_state_of(obj, backend) if backend else None
        ems_managed = bool(ems and ems.last_patched)
        logs = PatchLog.objects.restrict(request.user, 'view').filter(
            object_type=ContentType.objects.get_for_model(obj), object_id=obj.pk)[:5]
        return self.render('netbox_patch_management/object_panel.html', extra_context={
            'update_class': update_class, 'interval': interval, 'last': last,
            'due': due_date(last, interval) if interval else None, 'locked': locked, 'logs': logs,
            'ems': ems, 'ems_managed': ems_managed,
            'can_record': not locked and not ems_managed
                          and request.user.has_perm('netbox_patch_management.add_patchlog'),
            'record_url': f"{reverse('plugins:netbox_patch_management:record')}?{query}",
            'can_start': can_start, 'start_reason': reason, 'backend': backend,
            'start_url': f"{reverse('plugins:netbox_patch_management:start')}?{query}",
            'impact_url': impact_url([obj]) if not locked else None,
            'log_url': f"{reverse('plugins:netbox_patch_management:patchlog_list')}?"
                       f"{urlencode({'object_type': ContentType.objects.get_for_model(obj).pk, 'object_id': obj.pk})}",
        })


template_extensions = [PatchPanel]

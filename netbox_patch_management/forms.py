from datetime import date

from django import forms
from django.utils.translation import gettext_lazy as _

from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm
from utilities.forms.fields import CommentField, DynamicModelChoiceField
from utilities.forms.rendering import FieldSet
from utilities.forms.widgets import DatePicker

from .backends import BackendError, get_backend
from .models import PatchLog, PatchStatusChoices, UpdateClass


class UpdateClassForm(NetBoxModelForm):
    comments = CommentField()

    fieldsets = (
        FieldSet('name', 'interval_days', 'maintenance_window', 'backend_job_ref', 'description', 'tags',
                 name=_('Update class')),
    )

    class Meta:
        model = UpdateClass
        fields = ('name', 'interval_days', 'maintenance_window', 'backend_job_ref', 'description',
                  'comments', 'tags')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.backend_error = None
        backend = get_backend()
        if backend is None:
            del self.fields['backend_job_ref']
            return
        try:
            choices = list(backend.job_choices())
        except BackendError as exc:
            self.backend_error = str(exc)
            return  # keep the plain text field so an existing value stays editable
        current = self.instance.backend_job_ref
        if current and current not in dict(choices):
            choices.append((current, f'{current} ({_("not found in backend")})'))
        self.fields['backend_job_ref'] = forms.ChoiceField(
            label=_('Backend job'), required=False, choices=[('', '---------')] + choices,
        )


class UpdateClassFilterForm(NetBoxModelFilterSetForm):
    model = UpdateClass


class PatchLogFilterForm(forms.Form):
    q = forms.CharField(required=False, label=_('Search'))
    update_class_id = DynamicModelChoiceField(
        queryset=UpdateClass.objects.all(), required=False, label=_('Update class'),
    )
    status = forms.MultipleChoiceField(choices=PatchStatusChoices, required=False, label=_('Status'))
    source = forms.CharField(required=False, label=_('Source'))
    date_after = forms.DateField(required=False, label=_('From'), widget=DatePicker())
    date_before = forms.DateField(required=False, label=_('Until'), widget=DatePicker())

    fieldsets = (
        FieldSet('q', 'update_class_id', 'status', 'source', 'date_after', 'date_before'),
    )


class PlanningForm(forms.Form):
    update_class = forms.ModelChoiceField(queryset=UpdateClass.objects.all(), label=_('Update class'))
    day = forms.DateField(label=_('Reference date'), required=False, initial=date.today, widget=DatePicker())
    show_all = forms.BooleanField(required=False, label=_('Include systems not yet due'))


class RecordPatchForm(forms.Form):
    day = forms.DateField(label=_('Patched on'), initial=date.today, widget=DatePicker())
    comment = forms.CharField(label=_('Comment'), widget=forms.Textarea(attrs={'rows': 3}),
                              help_text=_('What was done (e.g. OS updates, reboot)'))

    def clean_day(self):
        day = self.cleaned_data['day']
        if day > date.today():
            raise forms.ValidationError(_('The date must not be in the future.'))
        return day

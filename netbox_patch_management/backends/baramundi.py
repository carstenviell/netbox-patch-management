"""
baramundi backend - requires the netbox_baramundi plugin, which holds the connection
settings and the endpoint mapping (custom field baramundi_endpoint_id).
"""
from django import forms
from django.utils.translation import gettext_lazy as _

from netbox_baramundi.client import BConnect, BConnectError, job_outcome
from netbox_baramundi.custom_fields import endpoint_id_for

from . import BackendError, EmsReport, PatchBackend, parse_date, parse_datetime
from ..models import PatchStatusChoices

OUTCOME = {'succeeded': PatchStatusChoices.SUCCEEDED, 'failed': PatchStatusChoices.FAILED,
           'cancelled': PatchStatusChoices.CANCELLED, 'running': PatchStatusChoices.STARTED}


class BaramundiConfigForm(forms.Form):
    start_immediately = forms.BooleanField(
        label=_('Start job immediately after assignment'), initial=True, required=False,
        help_text=_('Otherwise the job runs according to its schedule in baramundi'))


class BaramundiBackend(PatchBackend):
    key = 'baramundi'
    label = 'baramundi'
    config_form = BaramundiConfigForm

    def client(self):
        try:
            return BConnect.from_settings()
        except BConnectError as exc:
            raise BackendError(str(exc))

    def _call(self, func, *args):
        try:
            return func(*args)
        except BConnectError as exc:
            raise BackendError(str(exc))

    def test(self):
        server = self._call(self.client().management_server)
        return f"{server.get('name', '?')} {server.get('version', '')}".strip()

    def job_choices(self):
        jobs = self._call(self.client().job_definitions)
        return sorted(((j['id'], j.get('displayName') or j.get('name') or j['id']) for j in jobs if j.get('id')),
                      key=lambda c: str(c[1]).lower())

    def can_start(self, obj):
        return (True, '') if endpoint_id_for(obj) else (False, _('No baramundi endpoint assigned'))

    def start(self, obj, job_ref):
        endpoint_id = endpoint_id_for(obj)
        if not endpoint_id:
            raise BackendError(_('No baramundi endpoint assigned'))
        client = self.client()
        instance = self._call(client.create_job_instance, endpoint_id, job_ref)
        if not instance.get('id'):
            raise BackendError(_('baramundi returned no job instance'))
        if self.config.get('start_immediately', True):
            self._call(client.start_job_instance, instance['id'])
        return instance['id']

    def status(self, external_id):
        instance = self._call(self.client().job_instance, external_id)
        state = instance.get('state') or ''
        raw = f"{state}: {instance.get('stateDescription')}" if instance.get('stateDescription') else state
        return OUTCOME[job_outcome(state)], raw, parse_datetime(instance.get('lastAction'))

    def patch_state(self, objs):
        """Windows update management: last successful update and compliance per endpoint."""
        by_endpoint = {}
        for obj in objs:
            endpoint_id = endpoint_id_for(obj)
            if endpoint_id:
                by_endpoint[endpoint_id.lower()] = obj
        if not by_endpoint:
            return {}
        reports = {}
        for item in self._call(self.client().windows_update_states):
            obj = by_endpoint.get(str(item.get('endpointId', '')).lower())
            if obj is None:
                continue
            state = item.get('updateState')
            compliant = True if state == 'Compliant' else False if state == 'NonCompliant' else None
            missing = [f"{item[k]} {label}" for k, label in (('missingCriticalUpdates', _('critical')),
                                                              ('missingSecurityUpdates', _('security')))
                       if item.get(k)]
            detail = ' – '.join(filter(None, [state or '', ', '.join(missing)]))
            reports[obj] = EmsReport(parse_date(item.get('lastSuccessfulUpdate')), compliant, detail[:300])
        return reports

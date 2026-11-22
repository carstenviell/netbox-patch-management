"""
Generic REST backend for EMS without a dedicated plugin.

URL/body templates are Jinja2 (NetBox sandbox) with the context
  obj, cf (custom field data), job_ref, external_id.
Values are taken from JSON responses via dotted paths ('data.0.id'; empty = whole body).
Templates and URLs come from the admin-only settings page, never from object data.
"""
import json

import requests
from django import forms
from django.utils.translation import gettext_lazy as _

from utilities.jinja2 import render_jinja2

from . import BackendError, EmsReport, PatchBackend, TimeoutField, env_secret, parse_date, parse_datetime
from ..models import PatchStatusChoices

TEMPLATE_HELP = _('Jinja2 template; variables: obj, cf, job_ref, external_id')
PATH_HELP = _('Dotted path in the JSON response, e.g. data.0.id')


class NotFound(BackendError):
    pass


def dig(data, path):
    for part in filter(None, (path or '').split('.')):
        if isinstance(data, list):
            data = data[int(part)] if part.lstrip('-').isdigit() and -len(data) <= int(part) < len(data) else None
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


def parse_status_map(text):
    """'raw=succeeded' lines -> {raw: status}; invalid targets are a ValidationError."""
    mapping = {}
    for line in (text or '').splitlines():
        if not line.strip():
            continue
        raw, _sep, status = line.partition('=')
        if status.strip() not in PatchStatusChoices.values():
            raise forms.ValidationError(_('Invalid status in line "%(line)s"') % {'line': line})
        mapping[raw.strip()] = status.strip()
    return mapping


class RestConfigForm(forms.Form):
    base_url = forms.URLField(label=_('Base URL'))
    auth_header = forms.CharField(label=_('Auth header'), initial='Authorization', required=False)
    auth_prefix = forms.CharField(label=_('Auth prefix'), initial='Bearer ', required=False, strip=False)
    token_env = forms.CharField(label=_('Token environment variable'), required=False,
                                help_text=_('Name of the environment variable holding the token (never the token itself)'))
    verify_tls = forms.BooleanField(label=_('Verify TLS'), initial=True, required=False)
    ca_bundle = forms.CharField(label=_('CA bundle path'), required=False)
    timeout = TimeoutField()

    jobs_url = forms.CharField(label=_('Job list URL'), required=False, help_text=TEMPLATE_HELP)
    jobs_list_path = forms.CharField(label=_('Job list path'), required=False, help_text=PATH_HELP)
    jobs_id_path = forms.CharField(label=_('Job ID path'), initial='id', required=False)
    jobs_label_path = forms.CharField(label=_('Job label path'), initial='name', required=False)

    start_method = forms.ChoiceField(label=_('Start method'), choices=(('POST', 'POST'), ('PUT', 'PUT')), initial='POST')
    start_url = forms.CharField(label=_('Start URL'), required=False, help_text=TEMPLATE_HELP)
    start_body = forms.CharField(label=_('Start body'), required=False, widget=forms.Textarea(attrs={'rows': 3}),
                                 help_text=TEMPLATE_HELP)
    start_id_path = forms.CharField(label=_('Job instance ID path'), initial='id', required=False)

    status_url = forms.CharField(label=_('Status URL'), required=False, help_text=TEMPLATE_HELP)
    status_path = forms.CharField(label=_('Status path'), initial='status', required=False)
    status_finished_path = forms.CharField(label=_('Finished-at path'), required=False, help_text=PATH_HELP)
    status_map = forms.CharField(
        label=_('Status mapping'), required=False, widget=forms.Textarea(attrs={'rows': 4}),
        help_text=_('One "raw=status" per line; status is succeeded, failed, cancelled or started. '
                    'Unmapped values count as started.'))

    state_url = forms.CharField(label=_('Patch state URL (per object)'), required=False, help_text=TEMPLATE_HELP)
    state_date_path = forms.CharField(label=_('Last-patched path'), required=False, help_text=PATH_HELP)
    state_compliant_path = forms.CharField(label=_('Compliance path'), required=False, help_text=PATH_HELP)
    state_compliant_value = forms.CharField(label=_('Compliant value'), initial='true', required=False)
    state_detail_path = forms.CharField(label=_('Detail path'), required=False, help_text=PATH_HELP)

    def clean_status_map(self):
        parse_status_map(self.cleaned_data['status_map'])
        return self.cleaned_data['status_map']


class RestBackend(PatchBackend):
    key = 'rest'
    label = 'REST API'
    config_form = RestConfigForm

    def _render(self, template, **context):
        try:
            return render_jinja2(template, context).strip()
        except Exception as exc:  # template errors must not crash the caller
            raise BackendError(_('Template error: %(err)s') % {'err': exc})

    def _url(self, template, **context):
        url = self._render(template, **context)
        return url if url.startswith(('http://', 'https://')) else self.config['base_url'].rstrip('/') + '/' + url.lstrip('/')

    def _request(self, method, url, body=None):
        headers = {'Accept': 'application/json'}
        if self.config.get('token_env'):
            headers[self.config.get('auth_header') or 'Authorization'] = \
                (self.config.get('auth_prefix') or '') + env_secret(self.config['token_env'])
        if body:
            headers['Content-Type'] = 'application/json'
        verify = self.config.get('ca_bundle') or self.config.get('verify_tls', True)
        try:
            resp = requests.request(method, url, data=body or None, headers=headers, verify=verify,
                                    timeout=self.config.get('timeout') or 30)
        except requests.RequestException as exc:
            raise BackendError(str(exc))
        if resp.status_code == 404:
            raise NotFound(f'HTTP 404: {url}')
        if resp.status_code >= 400:
            raise BackendError(f'HTTP {resp.status_code}: {resp.text[:200]}')
        try:
            return resp.json() if resp.content else {}
        except ValueError:
            raise BackendError(_('Response is not JSON'))

    def test(self):
        if self.config.get('jobs_url'):
            return _('%(n)d jobs found') % {'n': len(self.job_choices())}
        self._request('GET', self.config['base_url'])
        return _('Base URL reachable')

    def job_choices(self):
        if not self.config.get('jobs_url'):
            return []
        items = dig(self._request('GET', self._url(self.config['jobs_url'])), self.config.get('jobs_list_path')) or []
        return [(str(dig(i, self.config.get('jobs_id_path') or 'id')),
                 str(dig(i, self.config.get('jobs_label_path') or 'name'))) for i in items]

    def can_start(self, obj):
        return (True, '') if self.config.get('start_url') else (False, _('No start URL configured'))

    def start(self, obj, job_ref):
        ctx = {'obj': obj, 'cf': obj.custom_field_data, 'job_ref': job_ref, 'external_id': ''}
        body = self._render(self.config['start_body'], **ctx) if self.config.get('start_body') else None
        if body:
            try:
                json.loads(body)
            except ValueError:
                raise BackendError(_('Start body is not valid JSON'))
        data = self._request(self.config.get('start_method') or 'POST', self._url(self.config['start_url'], **ctx), body)
        external_id = dig(data, self.config.get('start_id_path') or 'id')
        if external_id in (None, ''):
            raise BackendError(_('No job instance ID in response'))
        return str(external_id)

    def status(self, external_id):
        if not self.config.get('status_url'):
            raise BackendError(_('No status URL configured'))
        data = self._request('GET', self._url(self.config['status_url'], external_id=external_id))
        raw = str(dig(data, self.config.get('status_path') or 'status'))
        status = parse_status_map(self.config.get('status_map')).get(raw, PatchStatusChoices.STARTED)
        return status, raw, parse_datetime(dig(data, self.config.get('status_finished_path')))

    def patch_state(self, objs):
        if not self.config.get('state_url'):
            return {}
        reports = {}
        for obj in objs:
            try:
                data = self._request('GET', self._url(self.config['state_url'], obj=obj, cf=obj.custom_field_data))
            except NotFound:
                continue  # unknown to the EMS - not reported
            compliant = None
            if self.config.get('state_compliant_path'):
                value = dig(data, self.config['state_compliant_path'])
                if value is not None:
                    compliant = str(value).lower() == str(self.config.get('state_compliant_value') or 'true').lower()
            try:
                last = parse_date(dig(data, self.config.get('state_date_path')))
            except ValueError:
                raise BackendError(_('Invalid date in patch state of %(obj)s') % {'obj': obj})
            reports[obj] = EmsReport(last, compliant,
                                     str(dig(data, self.config.get('state_detail_path')) or '')[:300])
        return reports

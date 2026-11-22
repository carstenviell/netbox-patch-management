"""
PowerShell backends: a script configured by the admin is called with a fixed action
vocabulary. Object data travels as JSON on stdin, never on the command line.

    pwsh -NoProfile -NonInteractive -File <script> -Action <test|jobs|start|status|state>

stdin  {"action": ..., "objects": [{key, type, id, name, custom_fields}], "job_ref": ..., "external_id": ...}
stdout test   -> {"info": "..."}
       jobs   -> {"jobs": [{"id": "...", "label": "..."}]}
       start  -> {"id": "<external id>"}
       status -> {"status": "succeeded|failed|cancelled|started", "raw": "...", "finished_at": "<ISO>"}
       state  -> {"states": {"<key>": {"last_patched": "<ISO date>", "compliant": true|false|null, "detail": ""}}}
Exit code != 0 = error (stderr is shown). See examples/patch-backend.ps1.
"""
import json
import os
import re
import subprocess

from django import forms
from django.utils.translation import gettext_lazy as _

from netbox.plugins import get_plugin_config

from . import BackendError, EmsReport, PatchBackend, TimeoutField, object_key, object_payload, parse_date, parse_datetime
from ..models import PatchStatusChoices

SAFE_REMOTE = re.compile(r'^[\w][\w\-.:\\/]*$')  # remote paths: no spaces/shell metacharacters (no argument injection)
SAFE_HOST = re.compile(r'^[A-Za-z0-9][\w\-.]*$')    # must not start with '-' (ssh option injection)


def script_root():
    return os.path.realpath(get_plugin_config('netbox_patch_management', 'script_root'))


class LocalConfigForm(forms.Form):
    script = forms.CharField(label=_('Script path'),
                             help_text=_('Absolute path below the configured script directory'))
    timeout = TimeoutField()

    def clean_script(self):
        path = os.path.realpath(self.cleaned_data['script'])
        root = script_root()
        if os.path.commonpath([path, root]) != root:
            raise forms.ValidationError(_('The script must be located below %(root)s') % {'root': root})
        return path


class SshConfigForm(forms.Form):
    host = forms.CharField(label=_('Host'))
    port = forms.IntegerField(label=_('Port'), initial=22, min_value=1, max_value=65535)
    user = forms.CharField(label=_('User'))
    key_file = forms.CharField(label=_('Private key file'), help_text=_('Path inside the NetBox container'))
    known_hosts_file = forms.CharField(label=_('known_hosts file'),
                                       help_text=_('Pinned host key; connections to unknown hosts are refused'))
    remote_script = forms.CharField(label=_('Remote script path'))
    pwsh = forms.CharField(label=_('Remote pwsh executable'), initial='pwsh')
    timeout = TimeoutField()

    def clean(self):
        data = super().clean()
        for field, pattern in (('host', SAFE_HOST), ('user', SAFE_HOST), ('remote_script', SAFE_REMOTE),
                               ('pwsh', SAFE_REMOTE)):
            if data.get(field) and not pattern.match(data[field]):
                self.add_error(field, _('Invalid characters'))
        for field in ('key_file', 'known_hosts_file'):
            if data.get(field) and not data[field].startswith('/'):
                self.add_error(field, _('Absolute path required'))
        return data


class PowerShellBackend(PatchBackend):
    def command(self, action):
        raise NotImplementedError

    def call(self, action, **payload):
        try:
            proc = subprocess.run(
                self.command(action), input=json.dumps({'action': action, **payload}, default=str),
                capture_output=True, text=True, timeout=self.config.get('timeout') or 30, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendError(str(exc))
        if proc.returncode != 0:
            raise BackendError((proc.stderr or proc.stdout or f'exit code {proc.returncode}').strip()[:300])
        try:
            return json.loads(proc.stdout or '{}')
        except ValueError:
            raise BackendError(_('Script output is not JSON'))

    def test(self):
        return str(self.call('test').get('info', 'OK'))

    def job_choices(self):
        return [(str(j['id']), str(j.get('label', j['id']))) for j in self.call('jobs').get('jobs', [])]

    def can_start(self, obj):
        return True, ''  # the script decides at start time; no process per planning row

    def start(self, obj, job_ref):
        external_id = self.call('start', objects=[object_payload(obj)], job_ref=job_ref).get('id')
        if not external_id:
            raise BackendError(_('Script returned no job ID'))
        return str(external_id)

    def status(self, external_id):
        data = self.call('status', external_id=external_id)
        status = data.get('status')
        if status not in PatchStatusChoices.values():
            status = PatchStatusChoices.STARTED
        return status, str(data.get('raw', data.get('status', ''))), parse_datetime(data.get('finished_at'))

    def patch_state(self, objs):
        by_key = {object_key(o): o for o in objs}
        if not by_key:
            return {}
        states = self.call('state', objects=[object_payload(o) for o in objs]).get('states') or {}
        reports = {}
        for key, s in states.items():
            if key in by_key:
                try:
                    reports[by_key[key]] = EmsReport(parse_date(s.get('last_patched')), s.get('compliant'),
                                                     str(s.get('detail') or '')[:300])
                except ValueError:
                    raise BackendError(_('Invalid date for %(key)s') % {'key': key})
        return reports


class LocalPowerShellBackend(PowerShellBackend):
    key = 'pwsh_local'
    label = 'PowerShell'
    config_form = LocalConfigForm

    def command(self, action):
        script = os.path.realpath(self.config['script'])
        root = script_root()
        if os.path.commonpath([script, root]) != root:  # re-check: settings could be edited in the DB
            raise BackendError(_('The script must be located below %(root)s') % {'root': root})
        # ponytail: executable comes from PLUGINS_CONFIG only - a UI field would allow running any binary
        return [get_plugin_config('netbox_patch_management', 'pwsh_path'), '-NoProfile', '-NonInteractive', '-File', script, '-Action', action]


class SshPowerShellBackend(PowerShellBackend):
    key = 'pwsh_ssh'
    label = 'PowerShell (SSH)'
    config_form = SshConfigForm

    def command(self, action):
        c = self.config
        for field, pattern in (('host', SAFE_HOST), ('user', SAFE_HOST), ('remote_script', SAFE_REMOTE),
                               ('pwsh', SAFE_REMOTE)):
            if not pattern.match(str(c.get(field) or '')):
                raise BackendError(_('Invalid SSH setting: %(field)s') % {'field': field})
        if not (str(c.get('key_file', '')).startswith('/') and str(c.get('known_hosts_file', '')).startswith('/')):
            raise BackendError(_('Key and known_hosts files must be absolute paths'))
        return ['ssh', '-i', c['key_file'], '-p', str(c.get('port') or 22),
                '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', f"UserKnownHostsFile={c['known_hosts_file']}",
                f"{c['user']}@{c['host']}",
                c.get('pwsh') or 'pwsh', '-NoProfile', '-NonInteractive', '-File', c['remote_script'],
                '-Action', action]

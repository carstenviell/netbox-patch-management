"""
Endpoint-management (EMS) backend interface.

A backend starts patch jobs in an external system and reports job status and the patch
state of objects. The backend is selected on the plugin settings page; its own settings
come from `config_form` and are stored in PatchSettings.backend_config (never secrets -
fields only name environment variables or files).

Built-in: rest, pwsh_local, pwsh_ssh. baramundi is offered when netbox_baramundi is
installed. Third-party backends: PLUGINS_CONFIG['netbox_patch_management']['extra_backends']
= ['package.module.Class', ...].
"""
from dataclasses import dataclass
from datetime import date, datetime

from django import forms
from django.apps import apps
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from netbox.plugins import get_plugin_config

from ..models import PatchStatusChoices  # noqa: F401  (backends return these values)


class BackendError(Exception):
    pass


@dataclass
class EmsReport:
    last_patched: date | None = None
    compliant: bool | None = None   # None = unknown
    detail: str = ''


class PatchBackend:
    key = 'base'                    # stored as PatchLog.source / EmsState.source
    label = 'Backend'               # shown in the UI, e.g. "Start via baramundi"
    config_form = None              # forms.Form subclass with the backend's settings

    def __init__(self, config=None):
        self.config = config or {}

    def test(self):
        """Check the connection; return a short info text. Raise BackendError."""
        return ''

    def job_choices(self):
        """[(job_ref, label)] for the update class form."""
        return []

    def can_start(self, obj):
        """(bool, reason) - cheap, called per row of the planning view."""
        return False, _('Starting jobs is not supported by this backend')

    def start(self, obj, job_ref):
        """Start the job for obj; return the external job/instance id. Raise BackendError."""
        raise BackendError(_('Starting jobs is not supported by this backend'))

    def status(self, external_id):
        """Return (PatchStatusChoices value, raw status, finished_at datetime|None). Raise BackendError."""
        raise BackendError(_('Job status is not supported by this backend'))

    def patch_state(self, objs):
        """{obj: EmsReport} for the objects the EMS knows. Bulk query. Raise BackendError."""
        return {}


def object_key(obj):
    """Stable identifier for scripts/APIs: 'dcim.device:12'."""
    return f'{obj._meta.label_lower}:{obj.pk}'


def object_payload(obj):
    return {'key': object_key(obj), 'type': obj._meta.label_lower, 'id': obj.pk, 'name': str(obj),
            'custom_fields': obj.custom_field_data}


def parse_date(value):
    """ISO date or datetime string -> date (None for empty)."""
    if not value:
        return None
    if isinstance(value, (date, datetime)):
        return value.date() if isinstance(value, datetime) else value
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).date()


def parse_datetime(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')) if value else None


def available_backends():
    """{key: class} of all selectable backends."""
    from .powershell import LocalPowerShellBackend, SshPowerShellBackend
    from .rest import RestBackend

    classes = [RestBackend, LocalPowerShellBackend, SshPowerShellBackend]
    if apps.is_installed('netbox_baramundi'):
        from .baramundi import BaramundiBackend
        classes.append(BaramundiBackend)
    classes += [import_string(path) for path in get_plugin_config('netbox_patch_management', 'extra_backends')]
    return {cls.key: cls for cls in classes}


def get_backend():
    from ..models import PatchSettings
    settings = PatchSettings.objects.filter(pk=1).first()
    if settings is None or not settings.backend:
        return None
    cls = available_backends().get(settings.backend)
    return cls(settings.backend_config) if cls else None


def env_secret(name):
    """Read a secret from the environment variable named in the config (never stored in the DB)."""
    import os
    value = os.environ.get(name or '')
    if not value:
        raise BackendError(_('Environment variable %(name)s is not set') % {'name': name})
    return value


class TimeoutField(forms.IntegerField):
    def __init__(self, **kwargs):
        super().__init__(label=_('Timeout (s)'), min_value=1, max_value=600, initial=30, **kwargs)

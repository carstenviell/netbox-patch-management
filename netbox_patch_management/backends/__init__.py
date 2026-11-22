"""
Endpoint-management backend interface.

A backend starts patch jobs in an external system (baramundi, ...) and reports their
status. Configure it via PLUGINS_CONFIG['netbox_patch_management']['backend'] as a dotted
path to a PatchBackend subclass. Without a backend the plugin works manual-only.
"""
from django.utils.module_loading import import_string

from netbox.plugins import get_plugin_config

from ..models import PatchStatusChoices  # noqa: F401  (backends return these values)


class BackendError(Exception):
    pass


class PatchBackend:
    name = 'backend'          # stored as PatchLog.source
    label = 'Backend'         # shown in the UI, e.g. "Start via baramundi"

    def job_choices(self):
        """[(job_ref, label)] for the update class form."""
        return []

    def can_start(self, obj):
        """(bool, reason) - reason explains why a start is not possible."""
        raise NotImplementedError

    def start(self, obj, job_ref):
        """Start the job for obj; return the external job/instance id. Raise BackendError."""
        raise NotImplementedError

    def status(self, external_id):
        """Return (PatchStatusChoices value, raw status string). Raise BackendError."""
        raise NotImplementedError


def get_backend():
    path = get_plugin_config('netbox_patch_management', 'backend')
    return import_string(path)() if path else None

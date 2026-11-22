from django.db.models.signals import post_migrate
from netbox.plugins import PluginConfig


class PatchManagementConfig(PluginConfig):
    name = 'netbox_patch_management'
    verbose_name = 'Patch Management'
    description = (
        'OS patch planning for devices and VMs: update classes with intervals, patch lock, '
        'due list with impact analysis, patch log, pluggable endpoint-management backends.'
    )
    version = '0.1.0'
    author = 'Carsten Viell'
    base_url = 'patch-management'
    min_version = '4.7.0'
    default_settings = {
        # Dotted path to a PatchBackend subclass, e.g.
        # 'netbox_patch_management.backends.baramundi.BaramundiBackend'. None = manual only.
        'backend': None,
        'status_interval_min': 10,
    }

    def ready(self):
        super().ready()
        from . import jobs  # noqa: F401  (registers the system job)
        from .custom_fields import ensure_custom_fields
        post_migrate.connect(ensure_custom_fields, sender=self)


config = PatchManagementConfig

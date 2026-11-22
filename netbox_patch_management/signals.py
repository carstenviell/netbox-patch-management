"""
Guard: last_patched may only be changed by the plugin (mark_patched) - not via REST API,
bulk edit or CSV import - so every change has a patch log entry.
New objects may bring a past value (e.g. initial import of existing documentation).
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date

from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils.translation import gettext as _

from dcim.models import Device
from utilities.exceptions import AbortRequest
from virtualization.models import VirtualMachine

from .custom_fields import CF_LAST

_plugin_write = ContextVar('netbox_patch_management_write', default=False)


@contextmanager
def plugin_write():
    token = _plugin_write.set(True)
    try:
        yield
    finally:
        _plugin_write.reset(token)


@receiver(pre_save, sender=Device)
@receiver(pre_save, sender=VirtualMachine)
def guard_last_patched(sender, instance, **kwargs):
    if _plugin_write.get():
        return
    new = (instance.custom_field_data or {}).get(CF_LAST)
    if instance.pk is None:
        if new and date.fromisoformat(str(new)) > date.today():
            raise AbortRequest(_('"Last patched" must not be in the future.'))
        return
    old = (sender.objects.filter(pk=instance.pk).values_list('custom_field_data', flat=True).first() or {}).get(CF_LAST)
    if new != old:
        raise AbortRequest(_('"Last patched" can only be set via the patch log (Patch Management).'))

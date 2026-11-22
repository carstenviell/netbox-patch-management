"""
Custom fields on dcim.Device and virtualization.VirtualMachine.

Created idempotently after every migrate run. Existing fields are never modified or
deleted (an admin may have adjusted labels/weights) - only missing object types are added.
"""
from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext_lazy as _

CF_CLASS = 'patch_class'
CF_INTERVAL = 'patch_interval_days'
CF_LOCK = 'patch_lock'
CF_LAST = 'last_patched'

GROUP = 'Patch-Management'
TARGET_MODELS = ('dcim.device', 'virtualization.virtualmachine')


def _definitions(update_class_type):
    return (
        dict(name=CF_CLASS, type='object', related_object_type=update_class_type,
             label=_('Update class'), weight=100),
        dict(name=CF_INTERVAL, type='integer', validation_minimum=1,
             label=_('Patch interval (days)'),
             description=_('Overrides the interval of the update class'), weight=110),
        dict(name=CF_LOCK, type='boolean', default=False, required=False,
             label=_('Patch lock'),
             description=_('Locked systems are hidden from patch planning and cannot be patched'),
             weight=120),
        dict(name=CF_LAST, type='date', ui_editable='no',
             label=_('Last patched'),
             description=_('Set only via the patch log'), weight=130),
    )


def ensure_custom_fields(sender=None, **kwargs):
    from django.apps import apps
    from core.models import ObjectType
    from extras.models import CustomField
    from .models import UpdateClass

    targets = [ObjectType.objects.get_for_model(apps.get_model(label)) for label in TARGET_MODELS]
    # Custom field labels are stored, not translated at render time -> use the instance language.
    with translation.override(settings.LANGUAGE_CODE):
        specs = [{k: (str(v) if k in ('label', 'description') else v) for k, v in spec.items()}
                 for spec in _definitions(ObjectType.objects.get_for_model(UpdateClass))]
    for spec in specs:
        cf, _created = CustomField.objects.get_or_create(
            name=spec.pop('name'), defaults={'group_name': GROUP, **spec},
        )
        cf.object_types.add(*targets)

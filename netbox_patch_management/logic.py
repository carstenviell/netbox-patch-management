"""
Patch planning rules. record_patch()/mark_patched() are the only places that write
last_patched, so every change of that field has a PatchLog entry.
"""
from datetime import date, timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from dcim.models import Device
from virtualization.models import VirtualMachine

from .custom_fields import CF_CLASS, CF_INTERVAL, CF_LAST, CF_LOCK
from .models import PatchLog, PatchStatusChoices, UpdateClass

PATCHABLE_MODELS = (Device, VirtualMachine)


def _date(value):
    return date.fromisoformat(value) if value else None


def is_locked(obj):
    return bool(obj.custom_field_data.get(CF_LOCK))


def last_patched(obj):
    return _date(obj.custom_field_data.get(CF_LAST))


def effective_interval(obj, update_class):
    return obj.custom_field_data.get(CF_INTERVAL) or (update_class.interval_days if update_class else None)


def due_date(last, interval):
    """None = never patched, i.e. due immediately."""
    return last + timedelta(days=interval) if last else None


def plannable_objects(update_class, day, only_due=True, querysets=None):
    """
    Devices and VMs of the class, without locked ones, most overdue first.
    querysets: optional permission-restricted querysets (default: all objects).
    Returns dicts: obj, last, interval, due, overdue_days (None = never patched).
    """
    rows = []
    for qs in querysets or [m.objects.all() for m in PATCHABLE_MODELS]:
        qs = qs.filter(**{f'custom_field_data__{CF_CLASS}': update_class.pk}).exclude(
            **{f'custom_field_data__{CF_LOCK}': True})
        for obj in qs:
            last = last_patched(obj)
            interval = effective_interval(obj, update_class)
            due = due_date(last, interval)
            if only_due and due is not None and due > day:
                continue
            rows.append({'obj': obj, 'last': last, 'interval': interval, 'due': due,
                         'overdue_days': (day - due).days if due else None})
    # never patched first, then by due date; name as tie breaker
    rows.sort(key=lambda r: (r['due'] is not None, r['due'] or date.min, str(r['obj'])))
    return rows


def mark_patched(obj, day):
    """Set last_patched unless the stored value is newer. Returns True if changed."""
    current = last_patched(obj)
    if current and current >= day:
        return False
    if hasattr(obj, 'snapshot'):
        obj.snapshot()  # changelog entry with pre-change data (when change logging is active)
    obj.custom_field_data[CF_LAST] = day.isoformat()
    obj.save()
    return True


def update_class_of(obj):
    pk = obj.custom_field_data.get(CF_CLASS)
    return UpdateClass.objects.filter(pk=pk).first() if pk else None


def new_log(obj, day, user, **fields):
    return PatchLog.objects.create(
        object_type=ContentType.objects.get_for_model(obj), object_id=obj.pk, object_name=str(obj)[:200],
        update_class=update_class_of(obj), date=day,
        user=user if getattr(user, 'pk', None) else None, user_name=getattr(user, 'username', '') or '',
        **fields,
    )


def record_patch(objs, day, comment, user, source='manual'):
    """
    Record a completed patch. Locked objects are refused. An older date than the stored one
    is logged (backfill) but does not move last_patched backwards.
    Returns (logged_objects, refused_locked_objects).
    """
    logged, refused = [], []
    with transaction.atomic():
        for obj in objs:
            if is_locked(obj):
                refused.append(obj)
                continue
            new_log(obj, day, user, source=source, status=PatchStatusChoices.SUCCEEDED, comment=comment)
            mark_patched(obj, day)
            logged.append(obj)
    return logged, refused

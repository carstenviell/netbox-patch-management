"""
Patch planning rules.

last_patched is written only by mark_patched() and always together with a PatchLog entry
(record_patch, apply_ems_report, job status). When an EMS backend reports a date for a
system, the EMS is authoritative: its date wins and manual recording is refused.
"""
from datetime import date, timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from dcim.models import Device
from virtualization.models import VirtualMachine

from .custom_fields import CF_CLASS, CF_INTERVAL, CF_LAST, CF_LOCK
from .models import EmsState, PatchLog, PatchSettings, PatchStatusChoices, UpdateClass

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


def ems_states(objs, backend):
    """{(content_type_id, pk): EmsState} of the active backend for the given objects."""
    if backend is None or not objs:
        return {}
    states = {}
    for model in {type(o) for o in objs}:
        ct = ContentType.objects.get_for_model(model)
        ids = [o.pk for o in objs if type(o) is model]
        for s in EmsState.objects.filter(object_type=ct, object_id__in=ids, source=backend.key):
            states[(ct.pk, s.object_id)] = s
    return states


def ems_state_of(obj, backend):
    return ems_states([obj], backend).get((ContentType.objects.get_for_model(obj).pk, obj.pk))


def is_ems_managed(obj, backend, state=None):
    """The EMS has reported a patch date for obj -> the EMS is authoritative for it."""
    state = state if state is not None else ems_state_of(obj, backend)
    return bool(state and state.last_patched)


def plannable_objects(update_class, day, only_due=True, querysets=None, backend=None):
    """
    Devices and VMs of the class, without locked ones, most overdue first.
    Due = interval elapsed, or (setting on) the EMS reports the system as non-compliant.
    Returns dicts: obj, last, interval, due, overdue_days (None = never patched), ems, non_compliant.
    """
    objs = []
    for qs in querysets or [m.objects.all() for m in PATCHABLE_MODELS]:
        objs += list(qs.filter(**{f'custom_field_data__{CF_CLASS}': update_class.pk}).exclude(
            **{f'custom_field_data__{CF_LOCK}': True}))
    states = ems_states(objs, backend)
    compliance_due = backend is not None and PatchSettings.load().compliance_makes_due
    rows = []
    for obj in objs:
        last = last_patched(obj)
        interval = effective_interval(obj, update_class)
        due = due_date(last, interval)
        ems = states.get((ContentType.objects.get_for_model(obj).pk, obj.pk))
        non_compliant = compliance_due and ems is not None and ems.compliant is False
        if only_due and due is not None and due > day and not non_compliant:
            continue
        rows.append({'obj': obj, 'last': last, 'interval': interval, 'due': due, 'ems': ems,
                     'non_compliant': non_compliant,
                     'overdue_days': (day - due).days if due else None})
    # non-compliant and never patched first, then by due date; name as tie breaker
    rows.sort(key=lambda r: (not r['non_compliant'], r['due'] is not None, r['due'] or date.min, str(r['obj'])))
    return rows


def mark_patched(obj, day, force=False):
    """
    Set last_patched. Without force a newer stored value is kept (manual backfill);
    force (EMS is authoritative) sets the EMS date even if it is older. Returns True if changed.
    """
    current = last_patched(obj)
    if current == day or (not force and current and current > day):
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


def record_patch(objs, day, comment, user, backend=None):
    """
    Record a manual patch. Refused: locked objects, and objects whose patch date is reported
    by the EMS. An older date than the stored one is logged (backfill) without moving
    last_patched backwards. Returns (logged, refused_locked, refused_ems).
    """
    logged, locked, managed = [], [], []
    states = ems_states(list(objs), backend)
    with transaction.atomic():
        for obj in objs:
            if is_locked(obj):
                locked.append(obj)
                continue
            state = states.get((ContentType.objects.get_for_model(obj).pk, obj.pk))
            if state and state.last_patched:  # EMS is authoritative
                managed.append(obj)
                continue
            new_log(obj, day, user, source='manual', status=PatchStatusChoices.SUCCEEDED, comment=comment)
            mark_patched(obj, day)
            logged.append(obj)
    return logged, locked, managed


def apply_ems_report(obj, report, backend):
    """
    Store what the EMS reported. A different patch date is taken over (EMS is authoritative)
    and logged with source = backend. Returns True if last_patched changed.
    """
    ct = ContentType.objects.get_for_model(obj)
    EmsState.objects.update_or_create(
        object_type=ct, object_id=obj.pk,
        defaults={'source': backend.key, 'last_patched': report.last_patched, 'compliant': report.compliant,
                  'detail': report.detail, 'synced_at': timezone.now()},
    )
    if not report.last_patched or last_patched(obj) == report.last_patched:
        return False
    with transaction.atomic():
        new_log(obj, report.last_patched, None, source=backend.key, status=PatchStatusChoices.SUCCEEDED,
                comment=_('Reported by %(ems)s') % {'ems': backend.label}, raw_status=report.detail[:100])
        mark_patched(obj, report.last_patched, force=True)
    return True

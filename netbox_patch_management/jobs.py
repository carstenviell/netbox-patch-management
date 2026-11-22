from datetime import date

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from netbox.jobs import JobRunner, system_job
from netbox.plugins import get_plugin_config

from .backends import BackendError, get_backend
from .custom_fields import CF_CLASS, CF_LOCK
from .logic import PATCHABLE_MODELS, apply_ems_report, is_locked, mark_patched, new_log, update_class_of
from .models import PatchLog, PatchStatusChoices


def load_objects(object_keys):
    """[(content_type_id, pk), ...] -> model instances (missing ones skipped)."""
    objs = []
    for ct_id, pk in object_keys:
        model = ContentType.objects.get_for_id(ct_id).model_class()
        obj = model.objects.filter(pk=pk).first()
        if obj is not None:
            objs.append(obj)
    return objs


class StartPatchJob(JobRunner):
    class Meta:
        name = 'Start patch jobs'

    def run(self, *args, object_keys=(), **kwargs):
        backend = get_backend()
        if backend is None:
            raise BackendError('No patch backend configured.')
        user, today = self.job.user, date.today()
        started = failed = 0
        for obj in load_objects(object_keys):
            # Re-check: state may have changed since the confirmation page.
            update_class = update_class_of(obj)
            ok, reason = backend.can_start(obj)
            if is_locked(obj) or update_class is None or not update_class.backend_job_ref or not ok:
                self.logger.warning(f'{obj}: skipped ({reason or "locked or no job"})')
                continue
            try:
                external_id = backend.start(obj, update_class.backend_job_ref)  # no retry: avoid duplicate jobs
            except BackendError as exc:
                new_log(obj, today, user, source=backend.key, status=PatchStatusChoices.FAILED, comment=str(exc))
                self.logger.error(f'{obj}: {exc}')
                failed += 1
                continue
            new_log(obj, today, user, source=backend.key, status=PatchStatusChoices.STARTED,
                    external_id=str(external_id))
            self.logger.info(f'{obj}: started ({external_id})')
            started += 1
        self.logger.info(f'{started} started, {failed} failed.')


def poll_status(backend, logger):
    """Update running entries. On success the EMS finish time (not the poll time) is the patch date."""
    for log in PatchLog.objects.filter(status=PatchStatusChoices.STARTED, source=backend.key):
        try:
            status, raw, finished_at = backend.status(log.external_id)
        except (BackendError, ValueError) as exc:
            logger.warning(f'{log.object_name}: status query failed: {exc}')
            continue
        if status not in PatchStatusChoices.values():
            logger.warning(f'{log.object_name}: unknown status {raw!r}, keeping "started"')
            status = PatchStatusChoices.STARTED
        if status == log.status and raw == log.raw_status:
            continue
        log.status, log.raw_status = status, raw[:100]
        if status == PatchStatusChoices.SUCCEEDED:
            log.date = timezone.localdate(finished_at) if finished_at and timezone.is_aware(finished_at) \
                else (finished_at.date() if finished_at else date.today())
            if log.object is not None:  # the patch happened - record it even if locked meanwhile
                mark_patched(log.object, log.date, force=True)
        log.save()
        logger.info(f'{log.object_name}: {status} ({raw})')


def sync_states(backend, logger):
    """Take over patch date and compliance reported by the EMS for all plannable objects."""
    objs = []
    for model in PATCHABLE_MODELS:
        objs += list(model.objects.filter(**{f'custom_field_data__{CF_CLASS}__isnull': False})
                     .exclude(**{f'custom_field_data__{CF_CLASS}': None})
                     .exclude(**{f'custom_field_data__{CF_LOCK}': True}))
    try:
        reports = backend.patch_state(objs)
    except BackendError as exc:
        logger.warning(f'Patch state query failed: {exc}')
        return
    changed = sum(apply_ems_report(obj, report, backend) for obj, report in reports.items())
    logger.info(f'Patch state: {len(reports)} of {len(objs)} systems reported, {changed} patch date(s) updated.')


def run_sync(logger):
    backend = get_backend()
    if backend is not None:
        poll_status(backend, logger)
        sync_states(backend, logger)


@system_job(interval=get_plugin_config('netbox_patch_management', 'status_interval_min'))
class PatchSyncJob(JobRunner):
    class Meta:
        name = 'Patch sync'

    def run(self, *args, **kwargs):
        run_sync(self.logger)

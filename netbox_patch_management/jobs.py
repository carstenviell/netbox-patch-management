from datetime import date

from django.contrib.contenttypes.models import ContentType

from netbox.jobs import JobRunner, system_job
from netbox.plugins import get_plugin_config

from .backends import BackendError, get_backend
from .logic import is_locked, mark_patched, new_log, update_class_of
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
                external_id = backend.start(obj, update_class.backend_job_ref)
            except BackendError as exc:
                new_log(obj, today, user, source=backend.name, status=PatchStatusChoices.FAILED, comment=str(exc))
                self.logger.error(f'{obj}: {exc}')
                failed += 1
                continue
            new_log(obj, today, user, source=backend.name, status=PatchStatusChoices.STARTED,
                    external_id=str(external_id))
            self.logger.info(f'{obj}: started ({external_id})')
            started += 1
        self.logger.info(f'{started} started, {failed} failed.')


def poll_status(backend, logger):
    """Update all running entries. On success, last_patched is set via mark_patched()."""
    for log in PatchLog.objects.filter(status=PatchStatusChoices.STARTED, source=backend.name):
        try:
            status, raw = backend.status(log.external_id)
        except BackendError as exc:
            logger.warning(f'{log.object_name}: status query failed: {exc}')
            continue
        if status not in PatchStatusChoices.values():
            logger.warning(f'{log.object_name}: unknown status {raw!r}, keeping "started"')
            status = PatchStatusChoices.STARTED
        if status == log.status and raw == log.raw_status:
            continue
        log.status, log.raw_status = status, raw
        if status == PatchStatusChoices.SUCCEEDED:
            log.date = date.today()
            if log.object is not None:
                mark_patched(log.object, log.date)
        log.save()
        logger.info(f'{log.object_name}: {status} ({raw})')


@system_job(interval=get_plugin_config('netbox_patch_management', 'status_interval_min'))
class PatchStatusJob(JobRunner):
    class Meta:
        name = 'Patch job status'

    def run(self, *args, **kwargs):
        backend = get_backend()
        if backend is not None:
            poll_status(backend, self.logger)

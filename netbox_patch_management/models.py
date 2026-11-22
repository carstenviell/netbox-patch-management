from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from netbox.models import NetBoxModel
from utilities.choices import ChoiceSet
from utilities.querysets import RestrictedQuerySet


class PatchStatusChoices(ChoiceSet):
    STARTED = 'started'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    CANCELLED = 'cancelled'

    CHOICES = (
        (STARTED, _('Started'), 'blue'),
        (SUCCEEDED, _('Succeeded'), 'green'),
        (FAILED, _('Failed'), 'red'),
        (CANCELLED, _('Cancelled'), 'gray'),
    )


class UpdateClass(NetBoxModel):
    name = models.CharField(verbose_name=_('name'), max_length=100, unique=True)
    interval_days = models.PositiveIntegerField(
        verbose_name=_('interval (days)'), validators=[MinValueValidator(1)],
    )
    maintenance_window = models.CharField(verbose_name=_('maintenance window'), max_length=200, blank=True)
    backend_job_ref = models.CharField(
        verbose_name=_('backend job'), max_length=200, blank=True,
        help_text=_('Job reference in the endpoint-management system (e.g. baramundi job definition)'),
    )
    description = models.CharField(verbose_name=_('description'), max_length=200, blank=True)
    comments = models.TextField(verbose_name=_('comments'), blank=True)

    class Meta:
        ordering = ('name',)
        verbose_name = _('update class')
        verbose_name_plural = _('update classes')

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_patch_management:updateclass', args=[self.pk])


class PatchLog(models.Model):
    """
    Append-only audit log: one entry per patch (manual or started via backend).
    object_name is a snapshot so the entry stays readable after the object is deleted.
    """
    created = models.DateTimeField(auto_now_add=True)
    object_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name='+')
    object_id = models.PositiveBigIntegerField()
    object = GenericForeignKey('object_type', 'object_id')
    object_name = models.CharField(max_length=200)
    update_class = models.ForeignKey(
        UpdateClass, on_delete=models.SET_NULL, null=True, blank=True, related_name='patch_logs',
    )
    date = models.DateField(verbose_name=_('date'))
    source = models.CharField(verbose_name=_('source'), max_length=50, default='manual')
    status = models.CharField(verbose_name=_('status'), max_length=20, choices=PatchStatusChoices)
    raw_status = models.CharField(verbose_name=_('raw status'), max_length=100, blank=True)
    external_id = models.CharField(verbose_name=_('external ID'), max_length=200, blank=True)
    comment = models.TextField(verbose_name=_('comment'), blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    user_name = models.CharField(verbose_name=_('user'), max_length=150, blank=True)

    objects = RestrictedQuerySet.as_manager()

    class Meta:
        ordering = ('-created',)
        indexes = (models.Index(fields=('object_type', 'object_id')),)
        verbose_name = _('patch log entry')
        verbose_name_plural = _('patch log')
        permissions = (('start_patch_job', 'Can start patch jobs via the backend'),)

    def __str__(self):
        return f'{self.object_name} {self.date}'

    def get_status_color(self):
        return PatchStatusChoices.colors.get(self.status)

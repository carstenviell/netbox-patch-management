import django_tables2 as tables
from django.utils.translation import gettext_lazy as _

from netbox.tables import NetBoxTable, columns

from .models import PatchLog, UpdateClass


class UpdateClassTable(NetBoxTable):
    name = tables.Column(linkify=True)
    tags = columns.TagColumn(url_name='plugins:netbox_patch_management:updateclass_list')

    class Meta(NetBoxTable.Meta):
        model = UpdateClass
        fields = ('pk', 'id', 'name', 'interval_days', 'maintenance_window', 'backend_job_ref',
                  'description', 'tags', 'created', 'last_updated')
        default_columns = ('name', 'interval_days', 'maintenance_window', 'description')


class PatchLogTable(NetBoxTable):
    object_name = tables.Column(
        verbose_name=_('Object'),
        linkify=lambda record: record.object.get_absolute_url() if record.object else None,
    )
    update_class = tables.Column(linkify=True, verbose_name=_('Update class'))
    status = columns.ChoiceFieldColumn()
    actions = None  # append-only log: no edit/delete row actions

    class Meta(NetBoxTable.Meta):
        model = PatchLog
        fields = ('pk', 'id', 'created', 'date', 'object_name', 'object_type', 'update_class', 'source',
                  'status', 'raw_status', 'external_id', 'user_name', 'comment')
        default_columns = ('date', 'object_name', 'update_class', 'source', 'status', 'user_name', 'comment')

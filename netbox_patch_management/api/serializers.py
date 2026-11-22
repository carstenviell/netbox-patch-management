from rest_framework import serializers

from netbox.api.serializers import NetBoxModelSerializer

from ..models import PatchLog, UpdateClass


class UpdateClassSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_patch_management-api:updateclass-detail')

    class Meta:
        model = UpdateClass
        fields = ('id', 'url', 'display', 'name', 'interval_days', 'maintenance_window', 'backend_job_ref',
                  'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated')
        brief_fields = ('id', 'url', 'display', 'name', 'description')


class PatchLogSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source='__str__', read_only=True)

    class Meta:
        model = PatchLog
        fields = ('id', 'display', 'created', 'date', 'object_type', 'object_id', 'object_name', 'update_class',
                  'source', 'status', 'raw_status', 'external_id', 'comment', 'user_name')
        read_only_fields = fields

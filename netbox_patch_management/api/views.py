from rest_framework.viewsets import ReadOnlyModelViewSet

from netbox.api.viewsets import NetBoxModelViewSet

from .. import filtersets
from ..models import PatchLog, UpdateClass
from .serializers import PatchLogSerializer, UpdateClassSerializer


class UpdateClassViewSet(NetBoxModelViewSet):
    queryset = UpdateClass.objects.all()
    serializer_class = UpdateClassSerializer
    filterset_class = filtersets.UpdateClassFilterSet


class PatchLogViewSet(ReadOnlyModelViewSet):
    """Read-only: log entries are written only by the plugin (record/start/status)."""
    queryset = PatchLog.objects.select_related('update_class')
    serializer_class = PatchLogSerializer
    filterset_class = filtersets.PatchLogFilterSet

    def get_queryset(self):
        return super().get_queryset().restrict(self.request.user, 'view')

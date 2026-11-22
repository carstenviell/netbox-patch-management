import django_filters
from django.db.models import Q

from netbox.filtersets import NetBoxModelFilterSet

from .models import PatchLog, PatchStatusChoices, UpdateClass


class UpdateClassFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = UpdateClass
        fields = ('id', 'name', 'interval_days', 'backend_job_ref')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(name__icontains=value) | Q(description__icontains=value))


class PatchLogFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search')
    update_class_id = django_filters.ModelMultipleChoiceFilter(queryset=UpdateClass.objects.all())
    status = django_filters.MultipleChoiceFilter(choices=PatchStatusChoices)
    date = django_filters.DateFromToRangeFilter()
    date_after = django_filters.DateFilter(field_name='date', lookup_expr='gte')
    date_before = django_filters.DateFilter(field_name='date', lookup_expr='lte')

    class Meta:
        model = PatchLog
        fields = ('id', 'object_type', 'object_id', 'source', 'external_id')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(object_name__icontains=value) | Q(comment__icontains=value) | Q(user_name__icontains=value)
        )

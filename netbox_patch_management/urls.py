from django.urls import include, path

from utilities.urls import get_model_urls

from . import views  # noqa: F401  (registers model views)

app_name = 'netbox_patch_management'

urlpatterns = (
    path('update-classes/', include(get_model_urls('netbox_patch_management', 'updateclass', detail=False))),
    path('update-classes/<int:pk>/', include(get_model_urls('netbox_patch_management', 'updateclass'))),
    path('log/', include(get_model_urls('netbox_patch_management', 'patchlog', detail=False))),
    path('planning/', views.PlanningView.as_view(), name='planning'),
    path('record/', views.RecordPatchView.as_view(), name='record'),
    path('start/', views.StartPatchView.as_view(), name='start'),
    path('settings/', views.SettingsView.as_view(), name='settings'),
    path('settings/test/', views.TestBackendView.as_view(), name='settings_test'),
)

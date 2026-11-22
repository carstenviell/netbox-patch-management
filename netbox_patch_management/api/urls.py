from netbox.api.routers import NetBoxRouter

from . import views

app_name = 'netbox_patch_management-api'

router = NetBoxRouter()
router.register('update-classes', views.UpdateClassViewSet)
router.register('log', views.PatchLogViewSet)
urlpatterns = router.urls

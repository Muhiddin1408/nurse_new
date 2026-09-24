from rest_framework.routers import DefaultRouter

from api.patients.views import PatientViewSet, AddressViewSet

router = DefaultRouter()
router.register("patients", PatientViewSet, basename="patient")
router.register("addresses", AddressViewSet, basename="address")

urlpatterns = router.urls

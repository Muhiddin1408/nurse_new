from django.urls import path

from api.schedule.views import DoctorSlotsView

urlpatterns = [
    path(
        "doctors/<uuid:doctor_id>/slots",
        DoctorSlotsView.as_view(),
        name="doctor-slots",
    ),
]
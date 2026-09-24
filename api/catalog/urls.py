from django.urls import path

from api.booking.review_views import DoctorReviewsView
from api.catalog.favorites import FavoriteDoctorView, FavoriteListView

from api.catalog.views import (
    ClinicListView,
    DoctorDetailView,
    DoctorListView,
    DoctorSearchView,
    DoctorServicesView,
    SpecializationListView,
)

urlpatterns = [
    path("specializations", SpecializationListView.as_view(), name="specialization-list"),
    path("clinics", ClinicListView.as_view(), name="clinic-list"),
    path("doctors", DoctorListView.as_view(), name="doctor-list"),
    path("search", DoctorSearchView.as_view(), name="doctor-search"),
    path("doctors/<uuid:doctor_id>", DoctorDetailView.as_view(), name="doctor-detail"),
    path("doctors/<uuid:doctor_id>/services", DoctorServicesView.as_view(), name="doctor-services"),
    path("doctors/<uuid:doctor_id>/reviews", DoctorReviewsView.as_view(), name="doctor-reviews"),
    path("doctors/<uuid:doctor_id>/favorite", FavoriteDoctorView.as_view(), name="doctor-favorite"),
    path("favorites", FavoriteListView.as_view(), name="favorite-list"),
]
from django.urls import path

from api.booking import review_views

from api.booking.views import (
    AdminCompleteBookingView,
    AdminDisputeListView,
    AdminResolveDisputeView,
    OpenDisputeView,
    AdminNoShowBookingView,
    BookingDetailView,
    CancelBookingView,
    CancellationPreviewView,
    CreateBookingView,
    DoctorPackagesView,
    MyPackagesView,
    PackageEnrollView,
    MyBookingsView,
    PricePreviewView,
    RebookView,
    RescheduleBookingView,
    WaitlistLeaveView,
    WaitlistView,
)

urlpatterns = [
    # doctors/<id>/slots bu yerdan api/schedule/urls.py ga ko'chirildi
    path("bookings", CreateBookingView.as_view(), name="booking-create"),
    path("price-preview", PricePreviewView.as_view(), name="booking-price-preview"),
    path("bookings/mine", MyBookingsView.as_view(), name="booking-list"),
    path("bookings/<uuid:booking_id>", BookingDetailView.as_view(), name="booking-detail"),
    path(
        "bookings/<uuid:booking_id>/cancel",
        CancelBookingView.as_view(),
        name="booking-cancel",
    ),
    path(
        "bookings/<uuid:booking_id>/cancellation-preview",
        CancellationPreviewView.as_view(),
        name="booking-cancellation-preview",
    ),
    path(
        "bookings/<uuid:booking_id>/reschedule",
        RescheduleBookingView.as_view(),
        name="booking-reschedule",
    ),
    path("bookings/<uuid:booking_id>/disputes", OpenDisputeView.as_view(), name="booking-dispute-open"),
    path("bookings/<uuid:booking_id>/rebook", RebookView.as_view(), name="booking-rebook"),
    path("bookings/<uuid:booking_id>/review", review_views.CreateReviewView.as_view(), name="booking-review"),
    path("doctors/<uuid:doctor_id>/packages", DoctorPackagesView.as_view(), name="doctor-packages"),
    path("packages/mine", MyPackagesView.as_view(), name="package-list"),
    path("packages/<uuid:package_id>/enroll", PackageEnrollView.as_view(), name="package-enroll"),
    path("waitlist", WaitlistView.as_view(), name="waitlist"),
    path("waitlist/<uuid:entry_id>", WaitlistLeaveView.as_view(), name="waitlist-leave"),
    path("disputes", AdminDisputeListView.as_view(), name="dispute-list"),
    path("disputes/<uuid:dispute_id>/resolve", AdminResolveDisputeView.as_view(), name="dispute-resolve"),
    path("bookings/<uuid:booking_id>/complete", AdminCompleteBookingView.as_view(), name="booking-complete"),
    path("bookings/<uuid:booking_id>/no-show", AdminNoShowBookingView.as_view(), name="booking-no-show"),
]

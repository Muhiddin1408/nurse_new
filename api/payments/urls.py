from django.urls import path

from api.payments.views import CheckoutView, ClickCompleteView, ClickPrepareView, PaymeWebhookView

urlpatterns = [
    path("payme/webhook", PaymeWebhookView.as_view(), name="payme-webhook"),
    path("click/prepare", ClickPrepareView.as_view(), name="click-prepare"),
    path("click/complete", ClickCompleteView.as_view(), name="click-complete"),
    path("bookings/<uuid:booking_id>/checkout", CheckoutView.as_view(), name="payment-checkout"),
]

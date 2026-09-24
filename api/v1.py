from django.urls import path, include

from api.doctor.urls import clinic_urlpatterns, moderation_urlpatterns, telegram_urlpatterns

urlpatterns = [
    path('accounts/', include('api.account.urls')),
    path('booking/', include('api.booking.urls')),
    path('catalog/', include('api.catalog.urls')),
    path('clinic/', include(clinic_urlpatterns)),
    path('doctor/', include('api.doctor.urls')),
    path('moderation/', include(moderation_urlpatterns)),
    path('patient/', include('api.patients.urls')),
    path('payment/', include('api.payments.urls')),
    path('schedule/', include('api.schedule.urls')),
    path('telegram/', include(telegram_urlpatterns)),
    # observability bu yerda EMAS — faqat /healthz/ (api/observability/urls.py izohi)
]

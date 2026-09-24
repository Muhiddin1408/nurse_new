from django.urls import path

from api.booking import review_views
from api.clinic import views as cv

from api.doctor import earnings_views as e, schedule_views, views, work_views as w

urlpatterns = [
    path("earnings/summary", e.EarningsSummaryView.as_view(), name="doctor-earnings-summary"),
    path("earnings/transactions", e.EarningsTransactionsView.as_view(), name="doctor-earnings-transactions"),
    path("payouts", e.DoctorPayoutListView.as_view(), name="doctor-payouts"),
    path("payouts/<uuid:payout_id>/statement", e.DoctorPayoutStatementView.as_view(), name="doctor-payout-statement"),
    path("payout-account", e.PayoutAccountView.as_view(), name="doctor-payout-account"),
    path("telegram/link", e.TelegramLinkView.as_view(), name="doctor-telegram-link"),
    path("appointments/today", w.TodayAppointmentsView.as_view(), name="doctor-appointments-today"),
    path("appointments", w.AppointmentListView.as_view(), name="doctor-appointments"),
    path("appointments/<uuid:booking_id>", w.AppointmentDetailView.as_view(), name="doctor-appointment"),
    path("appointments/<uuid:booking_id>/start", w.AppointmentStartView.as_view(), name="doctor-appointment-start"),
    path("appointments/<uuid:booking_id>/complete", w.AppointmentCompleteView.as_view(),
         name="doctor-appointment-complete"),
    path("appointments/<uuid:booking_id>/no-show", w.AppointmentNoShowView.as_view(), name="doctor-appointment-no-show"),
    path("appointments/<uuid:booking_id>/cancel", w.AppointmentCancelView.as_view(), name="doctor-appointment-cancel"),
    path("services", w.DoctorServicesView.as_view(), name="doctor-services"),
    path("services/<uuid:service_id>", w.DoctorServiceDetailView.as_view(), name="doctor-service"),
    path("services/<uuid:service_id>/toggle", w.DoctorServiceToggleView.as_view(), name="doctor-service-toggle"),
    path("services/<uuid:service_id>/price-history", w.DoctorServicePriceHistoryView.as_view(),
         name="doctor-service-price-history"),
    path("profile", w.DoctorPublicProfileView.as_view(), name="doctor-public-profile"),
    path("clinic-invites", cv.DoctorInvitesView.as_view(), name="doctor-clinic-invites"),
    path("clinic-invites/<uuid:affiliation_id>/<str:action>", cv.DoctorInviteRespondView.as_view(),
         name="doctor-clinic-invite-respond"),
    path("affiliations/<uuid:affiliation_id>/leave", cv.DoctorLeaveClinicView.as_view(),
         name="doctor-affiliation-leave"),
    path("reviews", review_views.MyDoctorReviewsView.as_view(), name="doctor-reviews-mine"),
    path("reviews/<uuid:review_id>/reply", review_views.ReplyReviewView.as_view(), name="doctor-review-reply"),
    path("working-rules", schedule_views.WorkingRulesView.as_view(), name="doctor-working-rules"),
    path("working-rules/<uuid:rule_id>", schedule_views.WorkingRuleDetailView.as_view(), name="doctor-working-rule"),
    path("time-off", schedule_views.TimeOffListView.as_view(), name="doctor-time-off"),
    path("time-off/<uuid:time_off_id>", schedule_views.TimeOffDetailView.as_view(), name="doctor-time-off-detail"),
    path("slots/<uuid:slot_id>/block", schedule_views.BlockSlotView.as_view(), name="doctor-slot-block"),
    path("slots/<uuid:slot_id>/unblock", schedule_views.UnblockSlotView.as_view(), name="doctor-slot-unblock"),
    path("schedule", schedule_views.CalendarView.as_view(), name="doctor-schedule"),
    path("schedule/regenerate", schedule_views.RegenerateView.as_view(), name="doctor-schedule-regenerate"),

    path("onboarding/start", views.OnboardingStartView.as_view(), name="doctor-onboarding-start"),
    path("onboarding/profile", views.OnboardingProfileView.as_view(), name="doctor-onboarding-profile"),
    path("onboarding/documents", views.OnboardingDocumentsView.as_view(), name="doctor-onboarding-documents"),
    path("onboarding/documents/<uuid:document_id>", views.OnboardingDocumentDeleteView.as_view(),
         name="doctor-onboarding-document-delete"),
    path("onboarding/submit", views.OnboardingSubmitView.as_view(), name="doctor-onboarding-submit"),
    path("onboarding/status", views.OnboardingStatusView.as_view(), name="doctor-onboarding-status"),
    path("documents/download/<str:token>", views.DocumentDownloadView.as_view(), name="doctor-document-download"),
]

clinic_urlpatterns = [
    # D7/D8 — klinika admini paneli (api/clinic/views.py)
    path("profile", cv.ClinicProfileView.as_view(), name="clinic-profile"),
    path("doctors", cv.ClinicDoctorsView.as_view(), name="clinic-doctors"),
    path("doctors/invite", cv.ClinicInviteView.as_view(), name="clinic-doctor-invite"),
    path("doctors/<uuid:affiliation_id>/<str:action>", cv.ClinicAffiliationActionView.as_view(),
         name="clinic-doctor-action"),
    path("schedule", cv.ClinicScheduleView.as_view(), name="clinic-schedule"),
    path("closures", cv.ClinicClosuresView.as_view(), name="clinic-closures"),
    path("closures/<uuid:closure_id>", cv.ClinicClosureDetailView.as_view(), name="clinic-closure"),
    path("appointments", cv.ClinicAppointmentsView.as_view(), name="clinic-appointments"),
    path("reports", cv.ClinicReportView.as_view(), name="clinic-reports"),
    path("rooms", cv.ClinicRoomsView.as_view(), name="clinic-rooms"),
    path("rooms/<uuid:room_id>", cv.ClinicRoomDetailView.as_view(), name="clinic-room"),
    path("rules", cv.ClinicRulesView.as_view(), name="clinic-rules"),
    path("rules/<uuid:rule_id>/room", cv.ClinicRuleRoomView.as_view(), name="clinic-rule-room"),
    path("services", w.ClinicServicesView.as_view(), name="clinic-services"),
    path("services/<uuid:service_id>", w.ClinicServiceDetailView.as_view(), name="clinic-service"),
]

telegram_urlpatterns = [
    path("webhook", e.TelegramWebhookView.as_view(), name="telegram-webhook"),
]

moderation_urlpatterns = [
    path("reviews", review_views.ModerationReviewListView.as_view(), name="moderation-reviews"),
    path("reviews/<uuid:review_id>/approve", review_views.ModerationReviewApproveView.as_view(),
         name="moderation-review-approve"),
    path("reviews/<uuid:review_id>/reject", review_views.ModerationReviewRejectView.as_view(),
         name="moderation-review-reject"),
    path("payouts", e.AdminPayoutListView.as_view(), name="moderation-payouts"),
    path("payouts/<uuid:payout_id>/mark-paid", e.AdminPayoutMarkPaidView.as_view(), name="moderation-payout-mark-paid"),
    path("doctors", views.ModerationDoctorListView.as_view(), name="moderation-doctor-list"),
    path("doctors/<uuid:doctor_id>", views.ModerationDoctorDetailView.as_view(), name="moderation-doctor-detail"),
    path("doctors/<uuid:doctor_id>/approve", views.ModerationApproveView.as_view(), name="moderation-doctor-approve"),
    path("doctors/<uuid:doctor_id>/reject", views.ModerationRejectView.as_view(), name="moderation-doctor-reject"),
    path("doctors/<uuid:doctor_id>/suspend", views.ModerationSuspendView.as_view(), name="moderation-doctor-suspend"),
    path("doctors/<uuid:doctor_id>/reinstate", views.ModerationReinstateView.as_view(),
         name="moderation-doctor-reinstate"),
]

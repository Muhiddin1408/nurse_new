// Package eventbus — event handlerlar. Har handler (ctx, tx, event): biznes o'zgarishi va idempotentlik
// belgisi BITTA tranzaksiyada (consumer o'zi ochadi va commit qiladi).
//
// Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart
// (scripts/check-contracts.py tekshiradi) — shuning uchun ro'yxat ochiq yozilgan.
package eventbus

import (
	"context"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	gcbus "github.com/Muhiddin1408/nurse_new/libs/go-common/eventbus"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/events"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/service"
)

// Handlers — servis bilan.
type Handlers struct{ Svc *service.Service }

type doctorData struct {
	DoctorID          uuid.UUID `json:"doctor_id"`
	UserID            uuid.UUID `json:"user_id"`
	AcceptsHomeVisits *bool     `json:"accepts_home_visits"`
}

type clinicData struct {
	ClinicID uuid.UUID `json:"clinic_id"`
}

type affiliationData struct {
	AffiliationID uuid.UUID `json:"affiliation_id"`
	ClinicID      uuid.UUID `json:"clinic_id"`
	DoctorID      uuid.UUID `json:"doctor_id"`
}

type bookingData struct {
	BookingID string    `json:"booking_id"`
	SlotID    uuid.UUID `json:"slot_id"`
	OldSlotID uuid.UUID `json:"old_slot_id"`
}

func decode[T any](ev gcbus.Envelope) (T, error) {
	var v T
	if err := ev.Decode(&v); err != nil {
		return v, gcbus.Permanent(err)
	}
	return v, nil
}

// SettingsChanged — admin sozlamalari keshi tashlanadi.
func (h Handlers) SettingsChanged(ctx context.Context, _ pgx.Tx, _ gcbus.Envelope) error {
	h.Svc.Settings.Invalidate(ctx)
	return nil
}

func (h Handlers) doctorActive(ctx context.Context, tx pgx.Tx, ev gcbus.Envelope) error {
	d, err := decode[doctorData](ev)
	if err != nil {
		return err
	}
	return h.Svc.DoctorActive(ctx, tx, d.DoctorID, d.UserID, d.AcceptsHomeVisits, ev.OccurredAt)
}

// DoctorApproved — proyeksiya + generatsiya.
func (h Handlers) DoctorApproved(ctx context.Context, tx pgx.Tx, ev gcbus.Envelope) error {
	return h.doctorActive(ctx, tx, ev)
}

// DoctorReinstated — qayta faol.
func (h Handlers) DoctorReinstated(ctx context.Context, tx pgx.Tx, ev gcbus.Envelope) error {
	return h.doctorActive(ctx, tx, ev)
}

// DoctorSuspended — bo'sh slotlar yopiladi.
func (h Handlers) DoctorSuspended(ctx context.Context, tx pgx.Tx, ev gcbus.Envelope) error {
	d, err := decode[doctorData](ev)
	if err != nil {
		return err
	}
	return h.Svc.DoctorSuspended(ctx, tx, d.DoctorID, d.UserID, ev.OccurredAt)
}

// ClinicPublished — klinika faol.
func (h Handlers) ClinicPublished(ctx context.Context, tx pgx.Tx, ev gcbus.Envelope) error {
	d, err := decode[clinicData](ev)
	if err != nil {
		return err
	}
	return h.Svc.ClinicActive(ctx, tx, d.ClinicID, ev.OccurredAt)
}

// ClinicSuspended — klinika to'xtatildi.
func (h Handlers) ClinicSuspended(ctx context.Context, tx pgx.Tx, ev gcbus.Envelope) error {
	d, err := decode[clinicData](ev)
	if err != nil {
		return err
	}
	return h.Svc.ClinicSuspended(ctx, tx, d.ClinicID, ev.OccurredAt)
}

func (h Handlers) affiliation(status string) gcbus.Handler {
	return func(ctx context.Context, tx pgx.Tx, ev gcbus.Envelope) error {
		d, err := decode[affiliationData](ev)
		if err != nil {
			return err
		}
		return h.Svc.AffiliationChanged(ctx, tx, d.AffiliationID, d.DoctorID, d.ClinicID, status, ev.OccurredAt)
	}
}

func (h Handlers) release(reason string, old bool) gcbus.Handler {
	return func(ctx context.Context, tx pgx.Tx, ev gcbus.Envelope) error {
		d, err := decode[bookingData](ev)
		if err != nil {
			return err
		}
		slot := d.SlotID
		if old {
			slot = d.OldSlotID
		}
		return h.Svc.ReleaseForBooking(ctx, tx, slot, d.BookingID, reason)
	}
}

// Register — barcha 13 ta event (contracts/events/catalog.yaml).
func Register(c *gcbus.Consumer, svc *service.Service) {
	h := Handlers{Svc: svc}
	c.MustOn(events.AdminSettingsChanged, h.SettingsChanged)
	c.MustOn(events.DoctorApproved, h.DoctorApproved)
	c.MustOn(events.DoctorSuspended, h.DoctorSuspended)
	c.MustOn(events.DoctorReinstated, h.DoctorReinstated)
	c.MustOn(events.ClinicPublished, h.ClinicPublished)
	c.MustOn(events.ClinicSuspended, h.ClinicSuspended)
	c.MustOn(events.AffiliationAccepted, h.affiliation("active"))
	c.MustOn(events.AffiliationResumed, h.affiliation("active"))
	c.MustOn(events.AffiliationPaused, h.affiliation("paused"))
	c.MustOn(events.AffiliationEnded, h.affiliation("ended"))
	c.MustOn(events.BookingCancelled, h.release("booking_cancelled", false))
	c.MustOn(events.BookingExpired, h.release("booking_expired", false))
	c.MustOn(events.BookingRescheduled, h.release("booking_rescheduled", true))
}

// Handler — testlar uchun: event turi bo'yicha (Register bilan bir xil xarita).
func Handler(svc *service.Service, eventType string) gcbus.Handler {
	h := Handlers{Svc: svc}
	return map[string]gcbus.Handler{
		events.AdminSettingsChanged: h.SettingsChanged,
		events.DoctorApproved:       h.DoctorApproved,
		events.DoctorSuspended:      h.DoctorSuspended,
		events.DoctorReinstated:     h.DoctorReinstated,
		events.ClinicPublished:      h.ClinicPublished,
		events.ClinicSuspended:      h.ClinicSuspended,
		events.AffiliationAccepted:  h.affiliation("active"),
		events.AffiliationResumed:   h.affiliation("active"),
		events.AffiliationPaused:    h.affiliation("paused"),
		events.AffiliationEnded:     h.affiliation("ended"),
		events.BookingCancelled:     h.release("booking_cancelled", false),
		events.BookingExpired:       h.release("booking_expired", false),
		events.BookingRescheduled:   h.release("booking_rescheduled", true),
	}[eventType]
}

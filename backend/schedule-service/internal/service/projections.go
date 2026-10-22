package service

import (
	"context"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/domain"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/store"
)

// Proyeksiya handlerlari consumer tranzaksiyasida chaqiriladi. Har biri tartibsiz/takroriy eventlarga
// chidamli: `changed_at` eskisi qo'llanmaydi; bloklash/ochish o'z sababi bilan cheklangan.

// DoctorActive — approved/reinstated: proyeksiya, `doctor_suspended` bloklari ochiladi, generatsiya.
func (s *Service) DoctorActive(ctx context.Context, tx pgx.Tx, doctorID, userID uuid.UUID, accepts *bool, at time.Time) error {
	return s.InConsumerTx(ctx, tx, func(ch *changes) error {
		applied, err := store.UpsertDoctor(ctx, tx, doctorID, userID, "approved", accepts, at)
		if err != nil || !applied {
			return err
		}
		if _, err := s.unblock(ctx, tx, ch, domain.BlockDoctorSuspended, `doctor_id = $1`, doctorID); err != nil {
			return err
		}
		_, err = s.generateDoctor(ctx, tx, ch, doctorID)
		return err
	})
}

// DoctorSuspended — kelajakdagi bo'sh slotlar yopiladi (faol bronlarni booking o'zi bekor qiladi).
func (s *Service) DoctorSuspended(ctx context.Context, tx pgx.Tx, doctorID, userID uuid.UUID, at time.Time) error {
	return s.InConsumerTx(ctx, tx, func(ch *changes) error {
		applied, err := store.UpsertDoctor(ctx, tx, doctorID, userID, "suspended", nil, at)
		if err != nil || !applied {
			return err
		}
		_, err = s.blockAvailable(ctx, tx, ch, domain.BlockDoctorSuspended, "Shifokor to'xtatilgan", nil,
			`doctor_id = $1 AND start_at > $2`, doctorID, s.Now())
		return err
	})
}

// ClinicActive — clinic.published.
func (s *Service) ClinicActive(ctx context.Context, tx pgx.Tx, clinicID uuid.UUID, at time.Time) error {
	return s.InConsumerTx(ctx, tx, func(ch *changes) error {
		applied, err := store.UpsertClinic(ctx, tx, clinicID, "active", at)
		if err != nil || !applied {
			return err
		}
		if _, err := s.unblock(ctx, tx, ch, domain.BlockClinicSuspended, `clinic_id = $1`, clinicID); err != nil {
			return err
		}
		return s.generateClinicDoctors(ctx, tx, ch, clinicID)
	})
}

// ClinicSuspended — klinikadagi kelajak bo'sh slotlar yopiladi.
func (s *Service) ClinicSuspended(ctx context.Context, tx pgx.Tx, clinicID uuid.UUID, at time.Time) error {
	return s.InConsumerTx(ctx, tx, func(ch *changes) error {
		applied, err := store.UpsertClinic(ctx, tx, clinicID, "suspended", at)
		if err != nil || !applied {
			return err
		}
		_, err = s.blockAvailable(ctx, tx, ch, domain.BlockClinicSuspended, "Klinika to'xtatilgan", nil,
			`clinic_id = $1 AND start_at > $2`, clinicID, s.Now())
		return err
	})
}

// AffiliationChanged — accepted/resumed → active (ochish + generatsiya); paused/ended → yopish.
func (s *Service) AffiliationChanged(ctx context.Context, tx pgx.Tx, affID, doctorID, clinicID uuid.UUID, status string, at time.Time) error {
	return s.InConsumerTx(ctx, tx, func(ch *changes) error {
		applied, err := store.UpsertAffiliation(ctx, tx, affID, doctorID, clinicID, status, at)
		if err != nil || !applied {
			return err
		}
		if status == "active" {
			if _, err := s.unblock(ctx, tx, ch, domain.BlockAffiliationPaused, `doctor_id = $1 AND clinic_id = $2`, doctorID, clinicID); err != nil {
				return err
			}
			_, err = s.generateDoctor(ctx, tx, ch, doctorID)
			return err
		}
		_, err = s.blockAvailable(ctx, tx, ch, domain.BlockAffiliationPaused, "Klinika bilan aloqa to'xtatilgan", nil,
			`doctor_id = $1 AND clinic_id = $2 AND start_at > $3`, doctorID, clinicID, s.Now())
		return err
	})
}

// ReleaseForBooking — booking.cancelled/expired/rescheduled: shu bronning slotini bo'shatadi (idempotent).
func (s *Service) ReleaseForBooking(ctx context.Context, tx pgx.Tx, slotID uuid.UUID, bookingID, reason string) error {
	return s.InConsumerTx(ctx, tx, func(ch *changes) error {
		_, err := s.releaseByRef(ctx, tx, ch, slotID, bookingID, reason)
		return err
	})
}

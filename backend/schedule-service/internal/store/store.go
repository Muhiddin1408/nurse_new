// Package store — Postgres so'rovlari (pgx). Biznes qarorlari internal/service da; bu yerda faqat SQL.
package store

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/domain"
)

// DB — pgxpool.Pool yoki pgx.Tx.
type DB interface {
	Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error)
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
}

// ErrNotFound — qator yo'q.
var ErrNotFound = errors.New("not found")

const slotCols = `id, doctor_id, clinic_id, room_id, start_at, end_at, status, hold_ref, hold_expires_at,
	block_reason, block_note, time_off_id, blocked_by_slot_id, travel_buffer_minutes, ever_held`

func scanSlot(row pgx.Row) (domain.Slot, error) {
	var s domain.Slot
	var buf *int16
	err := row.Scan(&s.ID, &s.DoctorID, &s.ClinicID, &s.RoomID, &s.StartAt, &s.EndAt, &s.Status, &s.HoldRef,
		&s.HoldExpiresAt, &s.BlockReason, &s.BlockNote, &s.TimeOffID, &s.BlockedBySlotID, &buf, &s.EverHeld)
	if errors.Is(err, pgx.ErrNoRows) {
		return s, ErrNotFound
	}
	if buf != nil {
		v := int(*buf)
		s.TravelBufferMinutes = &v
	}
	s.StartAt, s.EndAt = s.StartAt.UTC(), s.EndAt.UTC()
	return s, err
}

func collectSlots(rows pgx.Rows, err error) ([]domain.Slot, error) {
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []domain.Slot
	for rows.Next() {
		s, err := scanSlot(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, rows.Err()
}

// GetSlot — ID bo'yicha (forUpdate — qator qulfi).
func GetSlot(ctx context.Context, db DB, id uuid.UUID, forUpdate bool) (domain.Slot, error) {
	q := `SELECT ` + slotCols + ` FROM slots WHERE id = $1`
	if forUpdate {
		q += ` FOR UPDATE`
	}
	return scanSlot(db.QueryRow(ctx, q, id))
}

// SlotsWhere — erkin filtr (faqat shu paket ichidagi doimiy SQL qismlari bilan chaqiriladi).
func SlotsWhere(ctx context.Context, db DB, where string, args ...any) ([]domain.Slot, error) {
	return collectSlots(db.Query(ctx, `SELECT `+slotCols+` FROM slots WHERE `+where, args...))
}

// DoctorSlots — shifokorning [from, to) dagi slotlari (har qanday holat).
func DoctorSlots(ctx context.Context, db DB, doctorID uuid.UUID, from, to time.Time) ([]domain.Slot, error) {
	return SlotsWhere(ctx, db, `doctor_id = $1 AND start_at < $3 AND end_at > $2 ORDER BY start_at`, doctorID, from, to)
}

// AvailableSlots — bo'sh (yoki muddati o'tgan hold) slotlar.
func AvailableSlots(ctx context.Context, db DB, doctorID uuid.UUID, from, to, now time.Time, limit int) ([]domain.Slot, error) {
	return SlotsWhere(ctx, db, `doctor_id = $1 AND start_at >= $2 AND start_at < $3
		AND (status = 'free' OR (status = 'held' AND hold_expires_at < $4)) ORDER BY start_at LIMIT $5`,
		doctorID, from, to, now, limit)
}

// InsertCandidates — idempotent (UNIQUE(doctor,start) va (room,start) ziddiyatlari o'tkazib yuboriladi).
func InsertCandidates(ctx context.Context, db DB, cands []domain.Candidate) ([]domain.Slot, error) {
	if len(cands) == 0 {
		return nil, nil
	}
	ids := make([]uuid.UUID, len(cands))
	doctors := make([]uuid.UUID, len(cands))
	clinics := make([]*uuid.UUID, len(cands))
	rooms := make([]*uuid.UUID, len(cands))
	starts := make([]time.Time, len(cands))
	ends := make([]time.Time, len(cands))
	for i, c := range cands {
		ids[i], doctors[i], clinics[i], rooms[i], starts[i], ends[i] = uuid.New(), c.DoctorID, c.ClinicID, c.RoomID, c.Start, c.End
	}
	return collectSlots(db.Query(ctx, `INSERT INTO slots (id, doctor_id, clinic_id, room_id, start_at, end_at)
		SELECT * FROM unnest($1::uuid[], $2::uuid[], $3::uuid[], $4::uuid[], $5::timestamptz[], $6::timestamptz[])
		ON CONFLICT DO NOTHING RETURNING `+slotCols, ids, doctors, clinics, rooms, starts, ends))
}

// Exec — erkin UPDATE/DELETE (affected qatorlar soni).
func Exec(ctx context.Context, db DB, sql string, args ...any) (int64, error) {
	tag, err := db.Exec(ctx, sql, args...)
	return tag.RowsAffected(), err
}

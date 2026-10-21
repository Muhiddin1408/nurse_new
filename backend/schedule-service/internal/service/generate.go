package service

import (
	"context"
	"log/slog"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/domain"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/store"
)

// generateDoctor — bitta shifokor uchun qoidalardan yangi slotlar (tranzaksiya ichida). Faqat faol
// (approved) shifokor; klinika qoidalari — faol hamkorlik + faol klinika (ADR-0011 #6). Qaytaradi: yaratildi.
func (s *Service) generateDoctor(ctx context.Context, tx pgx.Tx, ch *changes, doctorID uuid.UUID) (int, error) {
	doc, err := store.GetDoctor(ctx, tx, doctorID)
	if isNotFound(err) || (err == nil && doc.Status != "approved") {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	rules, err := store.Rules(ctx, tx, doctorID)
	if err != nil || len(rules) == 0 {
		return 0, err
	}
	now := s.Now()
	today := domain.Today(now)
	days := s.Settings.Schedule(ctx).GenerationDaysAhead
	horizon := today.AddDate(0, 0, days)
	windowFrom, _ := domain.DayBounds(today)
	_, windowTo := domain.DayBounds(horizon)

	active, err := store.ActiveClinics(ctx, tx, doctorID)
	if err != nil {
		return 0, err
	}
	clinicIDs := map[uuid.UUID]bool{}
	rooms := map[uuid.UUID]bool{}
	for _, r := range rules {
		if r.ClinicID != nil {
			clinicIDs[*r.ClinicID] = true
		}
		if r.RoomID != nil {
			rooms[*r.RoomID] = true
		}
	}
	exceptions, err := store.ExceptionDates(ctx, tx, doctorID, today, horizon)
	if err != nil {
		return 0, err
	}
	closures, err := store.ClosureSet(ctx, tx, sortedIDs(clinicIDs), today, horizon)
	if err != nil {
		return 0, err
	}
	existing, err := store.DoctorSlots(ctx, tx, doctorID, windowFrom, windowTo)
	if err != nil {
		return 0, err
	}
	busy := make([]domain.Interval, 0, len(existing))
	starts := make(map[time.Time]bool, len(existing))
	for _, sl := range existing {
		busy = append(busy, domain.Interval{Start: sl.StartAt, End: sl.EndAt})
		starts[sl.StartAt] = true
	}
	offs, err := store.TimeOffs(ctx, tx, doctorID, windowFrom, windowTo)
	if err != nil {
		return 0, err
	}
	for _, t := range offs {
		busy = append(busy, domain.Interval{Start: t.StartAt, End: t.EndAt})
	}
	roomBusy := map[uuid.UUID][]domain.Interval{}
	if len(rooms) > 0 {
		others, err := store.SlotsWhere(ctx, tx, `room_id = ANY($1) AND doctor_id <> $2 AND start_at < $4 AND end_at > $3`,
			sortedIDs(rooms), doctorID, windowFrom, windowTo)
		if err != nil {
			return 0, err
		}
		for _, o := range others {
			roomBusy[*o.RoomID] = append(roomBusy[*o.RoomID], domain.Interval{Start: o.StartAt, End: o.EndAt})
		}
	}
	cands := domain.Generate(domain.GenInput{
		DoctorID: doctorID, Rules: rules, From: today, Days: days, HomeAllowed: true,
		ClinicAllowed: func(id uuid.UUID) bool { return active[id] },
		Exceptions:    exceptions, Closures: closures, Busy: busy, ExistingStarts: starts, RoomBusy: roomBusy,
	})
	// O'tib ketgan vaqtlar yaratilmaydi (bugungi kunning o'tgan qismi)
	fresh := cands[:0]
	for _, c := range cands {
		if c.Start.After(now) {
			fresh = append(fresh, c)
		}
	}
	created, err := store.InsertCandidates(ctx, tx, fresh)
	if err != nil {
		return 0, err
	}
	ch.touchSlots(created)
	return len(created), nil
}

// Generate — bitta shifokor (o'z tranzaksiyasida).
func (s *Service) Generate(ctx context.Context, doctorID uuid.UUID) (int, error) {
	n := 0
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		var err error
		n, err = s.generateDoctor(ctx, tx, ch, doctorID)
		return err
	})
	return n, err
}

// GenerateAll — tungi job (02:00 Toshkent). Advisory lock: bir nechta nusxadan faqat bittasi ishlaydi.
// Bitta shifokordagi xato qolganlarini to'xtatmaydi.
func (s *Service) GenerateAll(ctx context.Context) (int, error) {
	conn, err := s.Pool.Acquire(ctx)
	if err != nil {
		return 0, err
	}
	defer conn.Release()
	var locked bool
	if err := conn.QueryRow(ctx, `SELECT pg_try_advisory_lock(hashtext('schedule:generate_slots'))`).Scan(&locked); err != nil {
		return 0, err
	}
	if !locked {
		slog.InfoContext(ctx, "generate_slots already running elsewhere; skipped")
		return 0, nil
	}
	defer func() {
		_, _ = conn.Exec(context.WithoutCancel(ctx), `SELECT pg_advisory_unlock(hashtext('schedule:generate_slots'))`)
	}()
	doctors, err := store.DoctorsWithRules(ctx, s.Pool, domain.Today(s.Now()))
	if err != nil {
		return 0, err
	}
	total := 0
	for _, id := range doctors {
		n, err := s.Generate(ctx, id)
		if err != nil {
			slog.ErrorContext(ctx, "slot generation failed", slog.String("doctor_id", id.String()), slog.String("error", err.Error()))
			continue
		}
		total += n
	}
	return total, nil
}

// regenerate — qoidalar o'zgargach: kelajakdagi FREE slotlar joriy qoidalarga ANIQ mos kelmasa — bron tarixi
// bo'lmasa o'chiriladi, bo'lsa `rule_changed` bilan yopiladi; keyin generatsiya. Band slotlarga tegilmaydi.
func (s *Service) regenerate(ctx context.Context, tx pgx.Tx, ch *changes, doctorID uuid.UUID) (removed, created int, err error) {
	rules, err := store.Rules(ctx, tx, doctorID)
	if err != nil {
		return 0, 0, err
	}
	now := s.Now()
	free, err := store.SlotsWhere(ctx, tx, `doctor_id = $1 AND start_at > $2
		AND (status = 'free' OR (status = 'held' AND hold_expires_at < $2)) ORDER BY start_at FOR UPDATE`, doctorID, now)
	if err != nil {
		return 0, 0, err
	}
	var drop, block []uuid.UUID
	for _, sl := range free {
		if domain.CoveredByAny(rules, sl.ClinicID, sl.StartAt, sl.EndAt, true) {
			continue
		}
		if sl.Status == domain.StatusHeld {
			if err := s.publishReleased(ctx, tx, sl, sl.HoldRef, "hold_expired"); err != nil {
				return 0, 0, err
			}
			if err := s.releaseBuffer(ctx, tx, ch, sl.ID); err != nil {
				return 0, 0, err
			}
		}
		if sl.EverHeld {
			block = append(block, sl.ID)
		} else {
			drop = append(drop, sl.ID)
		}
		ch.touch(sl.DoctorID, sl.StartAt)
	}
	if len(drop) > 0 {
		if _, err := tx.Exec(ctx, `DELETE FROM slots WHERE id = ANY($1)`, drop); err != nil {
			return 0, 0, err
		}
	}
	if len(block) > 0 {
		if _, err := tx.Exec(ctx, `UPDATE slots SET status = 'blocked', block_reason = 'rule_changed',
			block_note = 'Ish qoidasi o''zgardi', hold_ref = NULL, hold_expires_at = NULL, updated_at = now()
			WHERE id = ANY($1)`, block); err != nil {
			return 0, 0, err
		}
	}
	created, err = s.generateDoctor(ctx, tx, ch, doctorID)
	return len(drop) + len(block), created, err
}

// Package service — schedule-service biznes amallari. Har amal o'z tranzaksiyasida (yoki consumer
// tranzaksiyasida); eventlar outbox orqali shu tranzaksiyada; Redis kesh commit'dan KEYIN tozalanadi.
package service

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"sort"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	gcbus "github.com/Muhiddin1408/nurse_new/libs/go-common/eventbus"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/events"
	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/cache"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/clients"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/domain"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/store"
)

// Biznes xatolari (yagona format: {"detail": {"code", "message"}}).
var (
	ErrSlotNotFound   = gchttp.NotFound("SLOT_NOT_FOUND", "Slot topilmadi")
	ErrSlotTaken      = gchttp.Conflict("SLOT_TAKEN", "Bu vaqt band yoki mavjud emas")
	ErrHoldLost       = gchttp.Conflict("HOLD_LOST", "Slot bu bron uchun band emas (muddati o'tgan yoki boshqa bron olgan)")
	ErrRuleNotFound   = gchttp.NotFound("RULE_NOT_FOUND", "Ish qoidasi topilmadi")
	ErrNotFound       = gchttp.NotFound("NOT_FOUND", "Topilmadi")
	ErrDoctorInactive = gchttp.Forbidden("DOCTOR_NOT_ACTIVE", "Shifokor profili tasdiqlanmagan yoki to'xtatilgan")
)

// Resolution — faol bronlar bilan to'qnashganda (ADR-0011: faqat saqlab qolish; bekor qilish — booking orqali).
const ResolutionKeepBookings = "keep_bookings"

// SettingsProvider — admin sozlamalari.
type SettingsProvider interface {
	Schedule(ctx context.Context) clients.ScheduleSettings
	Invalidate(ctx context.Context)
}

// CatalogClient — catalog internal API.
type CatalogClient interface {
	ClinicAccess(ctx context.Context, userID, clinicID uuid.UUID) (bool, error)
	Room(ctx context.Context, id uuid.UUID) (clients.Room, error)
	Bookability(ctx context.Context, doctorID uuid.UUID) (clients.Bookability, error)
}

// Service — bog'liqliklar.
type Service struct {
	Pool     *pgxpool.Pool
	Outbox   *gcbus.Outbox
	Cache    *cache.Slots
	Settings SettingsProvider
	Catalog  CatalogClient
	Now      func() time.Time
}

// New — standart soat bilan.
func New(pool *pgxpool.Pool, c *cache.Slots, s SettingsProvider, cat CatalogClient) *Service {
	return &Service{Pool: pool, Outbox: gcbus.NewOutbox("schedule-service"), Cache: c, Settings: s, Catalog: cat,
		Now: func() time.Time { return time.Now().UTC() }}
}

// changes — tranzaksiya davomida o'zgargan (shifokor, kun) juftliklari: availability eventi va kesh tozalash.
type changes struct {
	touched map[uuid.UUID]map[domain.Date]bool
}

func (c *changes) touch(doctorID uuid.UUID, at time.Time) {
	if c.touched == nil {
		c.touched = map[uuid.UUID]map[domain.Date]bool{}
	}
	if c.touched[doctorID] == nil {
		c.touched[doctorID] = map[domain.Date]bool{}
	}
	c.touched[doctorID][domain.LocalDate(at)] = true
}

func (c *changes) touchSlots(slots []domain.Slot) {
	for _, s := range slots {
		c.touch(s.DoctorID, s.StartAt)
	}
}

// inTx — yangi tranzaksiya; muvaffaqiyatda availability eventlari yoziladi, commit'dan keyin kesh tozalanadi.
func (s *Service) inTx(ctx context.Context, fn func(tx pgx.Tx, ch *changes) error) error {
	ch := &changes{}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if err := fn(tx, ch); err != nil {
		return err
	}
	if err := s.flush(ctx, tx, ch); err != nil {
		return err
	}
	if err := tx.Commit(ctx); err != nil {
		return err
	}
	s.invalidate(ctx, ch)
	return nil
}

// lockDoctor — shifokor jadvalini o'zgartiruvchi amallarni ketma-ket qiladi (qoida kesishuvi, ta'til
// kesishuvi tekshiruvlari parallel so'rovlarda ham to'g'ri bo'lsin). Tranzaksiya oxirida o'zi bo'shaydi.
func lockDoctor(ctx context.Context, tx pgx.Tx, doctorID uuid.UUID) error {
	_, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtext('schedule:doctor:' || $1::text))`, doctorID)
	return err
}

// InConsumerTx — consumer tranzaksiyasi ichida (commit'ni consumer qiladi). Kesh shu yerda tozalanadi —
// commit'dan oldin, ya'ni 30 s gacha eski qiymat qaytishi mumkin (TTL — xavfsizlik to'ri).
func (s *Service) InConsumerTx(ctx context.Context, tx pgx.Tx, fn func(ch *changes) error) error {
	ch := &changes{}
	if err := fn(ch); err != nil {
		return err
	}
	if err := s.flush(ctx, tx, ch); err != nil {
		return err
	}
	s.invalidate(ctx, ch)
	return nil
}

func (s *Service) invalidate(ctx context.Context, ch *changes) {
	for doctorID, days := range ch.touched {
		dates := make([]domain.Date, 0, len(days))
		for d := range days {
			dates = append(dates, d)
		}
		s.Cache.Invalidate(ctx, doctorID, dates...)
	}
}

type availabilityChanged struct {
	DoctorID        uuid.UUID  `json:"doctor_id"`
	Dates           []string   `json:"dates"`
	NextAvailableAt *time.Time `json:"next_available_at"`
}

// flush — har shifokor uchun bitta `schedule.availability_changed` (search, ws-gateway).
func (s *Service) flush(ctx context.Context, tx pgx.Tx, ch *changes) error {
	for doctorID, days := range ch.touched {
		dates := make([]string, 0, len(days))
		for d := range days {
			dates = append(dates, d.Format(time.DateOnly))
		}
		sort.Strings(dates)
		var next *time.Time
		now := s.Now()
		if err := tx.QueryRow(ctx, `SELECT min(start_at) FROM slots WHERE doctor_id = $1 AND start_at >= $2
			AND (status = 'free' OR (status = 'held' AND hold_expires_at < $2))`, doctorID, now).Scan(&next); err != nil {
			return err
		}
		if next != nil {
			u := next.UTC()
			next = &u
		}
		if _, err := s.Outbox.AddEvent(ctx, tx, events.ScheduleAvailabilityChanged, doctorID.String(),
			availabilityChanged{DoctorID: doctorID, Dates: dates, NextAvailableAt: next}); err != nil {
			return err
		}
	}
	return nil
}

type slotReleased struct {
	SlotID   uuid.UUID  `json:"slot_id"`
	DoctorID uuid.UUID  `json:"doctor_id"`
	ClinicID *uuid.UUID `json:"clinic_id"`
	Place    string     `json:"place"`
	StartAt  time.Time  `json:"start_at"`
	EndAt    time.Time  `json:"end_at"`
	HoldRef  *string    `json:"hold_ref"`
	Reason   string     `json:"reason"`
}

func (s *Service) publishReleased(ctx context.Context, tx pgx.Tx, slot domain.Slot, holdRef *string, reason string) error {
	ref := holdRef
	if ref != nil {
		if _, err := uuid.Parse(*ref); err != nil {
			ref = nil // kontrakt: hold_ref — uuid (booking_id); boshqa ko'rinish e'lon qilinmaydi
		}
	}
	_, err := s.Outbox.AddEvent(ctx, tx, events.SlotReleased, slot.ID.String(), slotReleased{
		SlotID: slot.ID, DoctorID: slot.DoctorID, ClinicID: slot.ClinicID, Place: slot.Place(),
		StartAt: slot.StartAt, EndAt: slot.EndAt, HoldRef: ref, Reason: reason,
	})
	return err
}

// freeSlot — slotni FREE qiladi va shu slot sababli qo'yilgan travel_buffer bloklarini ochadi (ADR-0011 #4).
// Bron davomida jadval o'zgargan bo'lsa (keep_bookings): qoida endi qoplamasa — `rule_changed`, boshqa sabab
// (dam olish kuni, ta'til, ...) amalda bo'lsa — o'sha sabab bilan yopiladi; aks holda slot qayta sotuvga chiqadi.
func (s *Service) freeSlot(ctx context.Context, tx pgx.Tx, ch *changes, slot domain.Slot) error {
	if _, err := tx.Exec(ctx, `UPDATE slots SET status = 'free', hold_ref = NULL, hold_expires_at = NULL,
		travel_buffer_minutes = NULL, block_reason = '', block_note = '', time_off_id = NULL, updated_at = now()
		WHERE id = $1`, slot.ID); err != nil {
		return err
	}
	if err := s.closeFreed(ctx, tx, slot); err != nil {
		return err
	}
	ch.touch(slot.DoctorID, slot.StartAt)
	return s.releaseBuffer(ctx, tx, ch, slot.ID)
}

// closedReasonFor — slot hozir sotuvga chiqmasligi kerak bo'lsa sababi ("" — ochiq bo'lishi mumkin).
func (s *Service) closedReasonFor(ctx context.Context, tx pgx.Tx, slot domain.Slot) (string, error) {
	rules, err := store.Rules(ctx, tx, slot.DoctorID)
	if err != nil {
		return "", err
	}
	if !domain.CoveredByAny(rules, slot.ClinicID, slot.StartAt, slot.EndAt, true) {
		return domain.BlockRuleChanged, nil
	}
	var reason *string
	if err := tx.QueryRow(ctx, `SELECT CASE WHEN `+stillClosed+` THEN `+closedReason+` END FROM slots WHERE id = $1`,
		slot.ID).Scan(&reason); err != nil {
		return "", err
	}
	if reason == nil {
		return "", nil
	}
	return *reason, nil
}

// closeFreed — endigina bo'shagan slot: kerak bo'lsa sababi bilan yopiladi.
func (s *Service) closeFreed(ctx context.Context, tx pgx.Tx, slot domain.Slot) error {
	reason, err := s.closedReasonFor(ctx, tx, slot)
	if err != nil || reason == "" {
		return err
	}
	_, err = tx.Exec(ctx, `UPDATE slots SET status = 'blocked', block_reason = $2::text,
		time_off_id = CASE WHEN $2::text = 'time_off' THEN `+sqlTimeOff+` END WHERE id = $1`, slot.ID, reason)
	return err
}

func (s *Service) releaseBuffer(ctx context.Context, tx pgx.Tx, ch *changes, slotID uuid.UUID) error {
	freed, err := store.SlotsWhere(ctx, tx, `blocked_by_slot_id = $1`, slotID)
	if err != nil {
		return err
	}
	// Bufer davomida boshqa sabab paydo bo'lgan bo'lsa (masalan dam olish kuni) — slot o'sha sabab bilan yopiq qoladi
	if _, err := tx.Exec(ctx, `UPDATE slots SET block_reason = `+closedReason+`, time_off_id = `+sqlTimeOff+`,
		blocked_by_slot_id = NULL, updated_at = now()
		WHERE blocked_by_slot_id = $1 AND status = 'blocked' AND block_reason = 'travel_buffer' AND `+stillClosed, slotID); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `UPDATE slots SET status = 'free', block_reason = '', block_note = '', blocked_by_slot_id = NULL,
		updated_at = now() WHERE blocked_by_slot_id = $1 AND status = 'blocked' AND block_reason = 'travel_buffer'`, slotID); err != nil {
		return err
	}
	ch.touchSlots(freed)
	return nil
}

// blockAvailable — `where` dagi FREE (va muddati o'tgan HELD) slotlarni yopadi. Muddati o'tgan hold egasiga
// `slot.released(hold_expired)` yuboriladi. Faol bronlarga tegilmaydi. Qaytaradi: yopilganlar soni.
func (s *Service) blockAvailable(ctx context.Context, tx pgx.Tx, ch *changes, reason, note string, timeOffID *uuid.UUID,
	where string, args ...any) (int, error) {
	now := s.Now()
	args = append(args, now)
	cond := fmt.Sprintf(`(%s) AND (status = 'free' OR (status = 'held' AND hold_expires_at < $%d)) ORDER BY start_at FOR UPDATE`,
		where, len(args))
	slots, err := store.SlotsWhere(ctx, tx, cond, args...)
	if err != nil {
		return 0, err
	}
	ids := make([]uuid.UUID, 0, len(slots))
	for _, sl := range slots {
		if sl.Status == domain.StatusHeld {
			if err := s.publishReleased(ctx, tx, sl, sl.HoldRef, "hold_expired"); err != nil {
				return 0, err
			}
			if err := s.releaseBuffer(ctx, tx, ch, sl.ID); err != nil {
				return 0, err
			}
		}
		ids = append(ids, sl.ID)
	}
	if len(ids) == 0 {
		return 0, nil
	}
	if _, err := tx.Exec(ctx, `UPDATE slots SET status = 'blocked', block_reason = $2::text, block_note = $3, time_off_id = $4,
		hold_ref = NULL, hold_expires_at = NULL, travel_buffer_minutes = NULL, blocked_by_slot_id = NULL, updated_at = now()
		WHERE id = ANY($1)`, ids, reason, note, timeOffID); err != nil {
		return 0, err
	}
	ch.touchSlots(slots)
	return len(ids), nil
}

// Slotni boshqa sabab hali ham yopib turibdimi — SQL bo'laklari (`slots` jadvali ustida).
const (
	sqlDayOff = `EXISTS (SELECT 1 FROM schedule_exceptions e WHERE e.doctor_id = slots.doctor_id
		AND e.date = (slots.start_at AT TIME ZONE 'Asia/Tashkent')::date)`
	sqlClinicClosed = `EXISTS (SELECT 1 FROM clinic_closures c WHERE c.clinic_id = slots.clinic_id
		AND c.date = (slots.start_at AT TIME ZONE 'Asia/Tashkent')::date)`
	sqlTimeOff = `(SELECT t.id FROM time_offs t WHERE t.doctor_id = slots.doctor_id AND t.start_at < slots.end_at
		AND t.end_at > slots.start_at ORDER BY t.start_at LIMIT 1)`
	sqlDoctorOff = `NOT EXISTS (SELECT 1 FROM doctors d WHERE d.doctor_id = slots.doctor_id AND d.status = 'approved')`
	sqlClinicOff = `(slots.clinic_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM clinics c WHERE c.clinic_id = slots.clinic_id
		AND c.status = 'active'))`
	sqlAffiliationOff = `(slots.clinic_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM affiliations a WHERE a.doctor_id = slots.doctor_id
		AND a.clinic_id = slots.clinic_id AND a.status = 'active'))`

	// stillClosed — slot biror holat sababli yopiq turishi kerak (dam olish kuni, klinika yopiq kuni, ta'til,
	// shifokor/klinika/hamkorlik faol emas).
	stillClosed = `(` + sqlDayOff + ` OR ` + sqlClinicClosed + ` OR ` + sqlTimeOff + ` IS NOT NULL OR ` +
		sqlDoctorOff + ` OR ` + sqlClinicOff + ` OR ` + sqlAffiliationOff + `)`
	// closedReason — o'sha sababning block_reason qiymati.
	closedReason = `CASE WHEN ` + sqlDayOff + ` THEN 'day_off' WHEN ` + sqlClinicClosed + ` THEN 'clinic_closed'
		WHEN ` + sqlTimeOff + ` IS NOT NULL THEN 'time_off' WHEN ` + sqlDoctorOff + ` THEN 'doctor_suspended'
		WHEN ` + sqlClinicOff + ` THEN 'clinic_suspended' ELSE 'affiliation_paused' END`
)

// stateReasons — holatdan kelib chiqadigan blok sabablari: ulardan birortasi bekor bo'lganda shu doiradagi
// barcha shunday slotlar qayta ko'rib chiqiladi (masalan klinika qayta faol, lekin hamkorlik hali pauzada —
// slot `affiliation_paused` bo'lib yopiq qoladi, hamkorlik tiklanganda ochiladi).
var stateReasons = []string{domain.BlockDayOff, domain.BlockClinicClosed, domain.BlockTimeOff, domain.BlockDoctorSuspended,
	domain.BlockClinicSuspended, domain.BlockAffiliationPaused}

// unblock — `where` dagi, `reason` bilan yopilgan KELAJAKDAGI slotlarni qayta ko'rib chiqadi: qoida endi
// qoplamasa — `rule_changed`; boshqa holat sababi amalda bo'lsa — o'sha sabab bilan yopiq qoladi; aks holda
// ochiladi. Holat sabablari (stateReasons) birgalikda ko'riladi. Qaytaradi: ochilganlar soni.
func (s *Service) unblock(ctx context.Context, tx pgx.Tx, ch *changes, reason, where string, args ...any) (int, error) {
	reasons := []string{reason}
	if slices.Contains(stateReasons, reason) {
		reasons = stateReasons
	}
	args = append(args, reasons, s.Now())
	n := len(args)
	slots, err := store.SlotsWhere(ctx, tx, fmt.Sprintf(`(%s) AND status = 'blocked' AND block_reason = ANY($%d)
		AND start_at > $%d ORDER BY start_at FOR UPDATE`, where, n-1, n), args...)
	if err != nil || len(slots) == 0 {
		return 0, err
	}
	rules := map[uuid.UUID][]domain.Rule{}
	var covered, stale []uuid.UUID
	for _, sl := range slots {
		if _, ok := rules[sl.DoctorID]; !ok {
			if rules[sl.DoctorID], err = store.Rules(ctx, tx, sl.DoctorID); err != nil {
				return 0, err
			}
		}
		if domain.CoveredByAny(rules[sl.DoctorID], sl.ClinicID, sl.StartAt, sl.EndAt, true) {
			covered = append(covered, sl.ID)
		} else {
			stale = append(stale, sl.ID)
		}
	}
	if _, err := tx.Exec(ctx, `UPDATE slots SET block_reason = 'rule_changed', time_off_id = NULL, updated_at = now()
		WHERE id = ANY($1)`, stale); err != nil {
		return 0, err
	}
	if _, err := tx.Exec(ctx, `UPDATE slots SET block_reason = `+closedReason+`, time_off_id = `+sqlTimeOff+`, updated_at = now()
		WHERE id = ANY($1) AND `+stillClosed, covered); err != nil {
		return 0, err
	}
	rows, err := tx.Query(ctx, `UPDATE slots SET status = 'free', block_reason = '', block_note = '', time_off_id = NULL,
		updated_at = now() WHERE id = ANY($1) AND NOT `+stillClosed+` RETURNING id`, covered)
	if err != nil {
		return 0, err
	}
	opened, err := pgx.CollectRows(rows, pgx.RowTo[uuid.UUID])
	if err != nil {
		return 0, err
	}
	for _, sl := range slots {
		if slices.Contains(opened, sl.ID) {
			ch.touch(sl.DoctorID, sl.StartAt)
		}
	}
	return len(opened), nil
}

// ConflictSlot — faol bron bilan band slot (409 javobida).
type ConflictSlot struct {
	SlotID  uuid.UUID `json:"slot_id"`
	StartAt time.Time `json:"start_at"`
	EndAt   time.Time `json:"end_at"`
	Status  string    `json:"status"`
	HoldRef *string   `json:"booking_id"`
}

func conflictsOf(slots []domain.Slot) []ConflictSlot {
	out := make([]ConflictSlot, 0, len(slots))
	for _, s := range slots {
		out = append(out, ConflictSlot{SlotID: s.ID, StartAt: s.StartAt, EndAt: s.EndAt, Status: s.Status, HoldRef: s.HoldRef})
	}
	return out
}

func liveConflict(slots []domain.Slot) error {
	return gchttp.Conflict("LIVE_BOOKINGS",
		"O'zgarish faol bronlarga tegadi: saqlab qolish uchun resolution=keep_bookings yuboring yoki bronlarni booking'da bekor qiling").
		With("conflicts", conflictsOf(slots)).With("options", []string{ResolutionKeepBookings})
}

// liveSlots — `where` dagi faol bronli kelajak slotlar.
func (s *Service) liveSlots(ctx context.Context, db store.DB, where string, args ...any) ([]domain.Slot, error) {
	now := s.Now()
	args = append(args, now)
	n := len(args)
	return store.SlotsWhere(ctx, db, fmt.Sprintf(`(%s) AND start_at > $%d AND (status = 'booked' OR (status = 'held' AND hold_expires_at >= $%d))
		ORDER BY start_at`, where, n, n), args...)
}

func isNotFound(err error) bool { return errors.Is(err, store.ErrNotFound) }

func sortedIDs(m map[uuid.UUID]bool) []uuid.UUID {
	out := make([]uuid.UUID, 0, len(m))
	for id := range m {
		out = append(out, id)
	}
	slices.SortFunc(out, func(a, b uuid.UUID) int { return compareUUID(a, b) })
	return out
}

func compareUUID(a, b uuid.UUID) int {
	for i := range a {
		if a[i] != b[i] {
			return int(a[i]) - int(b[i])
		}
	}
	return 0
}

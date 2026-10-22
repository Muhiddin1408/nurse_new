package service

import (
	"context"
	"encoding/json"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/cache"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/domain"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/store"
)

// SlotView — internal API (contracts/openapi/schedule-service.internal.yaml `Slot`).
type SlotView struct {
	ID                 uuid.UUID  `json:"id"`
	DoctorID           uuid.UUID  `json:"doctor_id"`
	ClinicID           *uuid.UUID `json:"clinic_id"`
	RoomID             *uuid.UUID `json:"room_id"`
	Place              string     `json:"place"`
	StartAt            time.Time  `json:"start_at"`
	EndAt              time.Time  `json:"end_at"`
	DurationMinutes    int        `json:"duration_minutes"`
	Status             string     `json:"status"`
	HoldRef            *string    `json:"hold_ref"`
	HoldExpiresAt      *time.Time `json:"hold_expires_at"`
	PriceAdjustPercent int        `json:"price_adjust_percent"`
}

func (s *Service) view(sl domain.Slot, rules []domain.PriceRule) SlotView {
	now := s.Now()
	v := SlotView{ID: sl.ID, DoctorID: sl.DoctorID, ClinicID: sl.ClinicID, RoomID: sl.RoomID, Place: sl.Place(),
		StartAt: sl.StartAt, EndAt: sl.EndAt, DurationMinutes: sl.DurationMinutes(), Status: sl.EffectiveStatus(now),
		PriceAdjustPercent: domain.PercentFor(rules, sl.StartAt)}
	if v.Status != domain.StatusFree {
		v.HoldRef, v.HoldExpiresAt = sl.HoldRef, sl.HoldExpiresAt
	}
	return v
}

func (s *Service) viewOne(ctx context.Context, db store.DB, sl domain.Slot) (SlotView, error) {
	rules, err := store.PriceRules(ctx, db, sl.DoctorID)
	if err != nil {
		return SlotView{}, err
	}
	return s.view(sl, rules), nil
}

// GetSlot — internal.
func (s *Service) GetSlot(ctx context.Context, id uuid.UUID) (SlotView, error) {
	sl, err := store.GetSlot(ctx, s.Pool, id, false)
	if isNotFound(err) {
		return SlotView{}, ErrSlotNotFound
	}
	if err != nil {
		return SlotView{}, err
	}
	return s.viewOne(ctx, s.Pool, sl)
}

// HoldRequest — internal hold.
type HoldRequest struct {
	HoldRef             string    `json:"hold_ref"`
	DoctorID            uuid.UUID `json:"doctor_id"`
	TTLSeconds          int       `json:"ttl_seconds"`
	TravelBufferMinutes *int      `json:"travel_buffer_minutes"`
}

// Hold — FREE (yoki muddati o'tgan HELD) → HELD; o'sha hold_ref bilan qayta — muddat yangilanadi (idempotent).
// Boshqa shifokorning sloti, o'tgan vaqt va band slot — bir xil 409 SLOT_TAKEN (A2: farq oshkor qilinmaydi).
func (s *Service) Hold(ctx context.Context, id uuid.UUID, req HoldRequest) (SlotView, error) {
	if req.HoldRef == "" || len(req.HoldRef) > 64 {
		return SlotView{}, gchttp.BadRequest("INVALID_HOLD_REF", "hold_ref majburiy (≤ 64)")
	}
	ttl := time.Duration(req.TTLSeconds) * time.Second
	if req.TTLSeconds == 0 {
		ttl = time.Duration(s.Settings.Schedule(ctx).HoldTTLMinutes) * time.Minute
	} else if req.TTLSeconds < 60 || req.TTLSeconds > 3600 {
		return SlotView{}, gchttp.BadRequest("INVALID_TTL", "ttl_seconds 60..3600")
	}
	var out SlotView
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		now := s.Now()
		sl, err := store.GetSlot(ctx, tx, id, true)
		if isNotFound(err) || (err == nil && (sl.DoctorID != req.DoctorID || !sl.StartAt.After(now))) {
			return ErrSlotTaken
		}
		if err != nil {
			return err
		}
		expires := now.Add(ttl)
		sameRef := sl.HoldRef != nil && *sl.HoldRef == req.HoldRef
		switch {
		case sl.Status == domain.StatusHeld && sameRef:
			if _, err := tx.Exec(ctx, `UPDATE slots SET hold_expires_at = $2, updated_at = now() WHERE id = $1`, id, expires); err != nil {
				return err
			}
		case sl.Status == domain.StatusBooked && sameRef: // qayta urinish tasdiqdan keyin keldi — o'zgarishsiz
		case sl.Status == domain.StatusFree || sl.HoldExpired(now):
			if reason, err := s.closedReasonFor(ctx, tx, sl); err != nil || reason != "" {
				if err != nil {
					return err
				}
				return ErrSlotTaken // jadval o'zgargan (keep_bookings) — expire job uni sababi bilan yopadi
			}
			if sl.Status == domain.StatusHeld { // boshqa bronning muddati o'tgan hold'i — egasiga xabar
				if err := s.publishReleased(ctx, tx, sl, sl.HoldRef, "hold_expired"); err != nil {
					return err
				}
				if err := s.releaseBuffer(ctx, tx, ch, sl.ID); err != nil {
					return err
				}
			}
			var buffer *int
			if sl.ClinicID == nil {
				b := domain.DefaultTravelBufferMinutes
				if req.TravelBufferMinutes != nil {
					b = *req.TravelBufferMinutes
				}
				buffer = &b
			}
			if _, err := tx.Exec(ctx, `UPDATE slots SET status = 'held', hold_ref = $2, hold_expires_at = $3, ever_held = true,
				travel_buffer_minutes = $4, updated_at = now() WHERE id = $1`, id, req.HoldRef, expires, buffer); err != nil {
				return err
			}
			if buffer != nil && *buffer > 0 {
				if err := s.blockTravelBuffer(ctx, tx, ch, sl, *buffer); err != nil {
					return err
				}
			}
		default:
			return ErrSlotTaken
		}
		ch.touch(sl.DoctorID, sl.StartAt)
		fresh, err := store.GetSlot(ctx, tx, id, false)
		if err != nil {
			return err
		}
		out, err = s.viewOne(ctx, tx, fresh)
		return err
	})
	return out, err
}

// blockTravelBuffer — [start−bufer, end+bufer) bilan kesishgan FREE qo'shnilar (C4); start_at bo'yicha tartib — deadlock yo'q.
func (s *Service) blockTravelBuffer(ctx context.Context, tx pgx.Tx, ch *changes, sl domain.Slot, minutes int) error {
	buf := time.Duration(minutes) * time.Minute
	neighbours, err := store.SlotsWhere(ctx, tx, `doctor_id = $1 AND id <> $2 AND status = 'free'
		AND start_at < $4 AND end_at > $3 ORDER BY start_at FOR UPDATE`, sl.DoctorID, sl.ID, sl.StartAt.Add(-buf), sl.EndAt.Add(buf))
	if err != nil || len(neighbours) == 0 {
		return err
	}
	ids := make([]uuid.UUID, len(neighbours))
	for i, n := range neighbours {
		ids[i] = n.ID
	}
	if _, err := tx.Exec(ctx, `UPDATE slots SET status = 'blocked', block_reason = 'travel_buffer', blocked_by_slot_id = $2,
		updated_at = now() WHERE id = ANY($1)`, ids, sl.ID); err != nil {
		return err
	}
	ch.touchSlots(neighbours)
	return nil
}

// Confirm — HELD (shu hold_ref) → BOOKED. Allaqachon BOOKED shu ref bilan — idempotent.
// Muddat tekshirilmaydi: to'lov o'tgan, slot hali boshqaga berilmagan bo'lsa — bron tasdiqlanadi.
func (s *Service) Confirm(ctx context.Context, id uuid.UUID, ref string) (SlotView, error) {
	var out SlotView
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		sl, err := store.GetSlot(ctx, tx, id, true)
		if isNotFound(err) {
			return ErrSlotNotFound
		}
		if err != nil {
			return err
		}
		if sl.HoldRef == nil || *sl.HoldRef != ref || (sl.Status != domain.StatusHeld && sl.Status != domain.StatusBooked) {
			return ErrHoldLost
		}
		if sl.Status == domain.StatusHeld {
			if _, err := tx.Exec(ctx, `UPDATE slots SET status = 'booked', hold_expires_at = NULL, updated_at = now() WHERE id = $1`, id); err != nil {
				return err
			}
			ch.touch(sl.DoctorID, sl.StartAt)
			sl.Status, sl.HoldExpiresAt = domain.StatusBooked, nil
		}
		out, err = s.viewOne(ctx, tx, sl)
		return err
	})
	return out, err
}

// Extend — to'lov ochilganda: hold muddati faqat uzayadi.
func (s *Service) Extend(ctx context.Context, id uuid.UUID, ref string, minutes int) (SlotView, error) {
	if minutes < 1 || minutes > 60 {
		return SlotView{}, gchttp.BadRequest("INVALID_MINUTES", "minutes 1..60")
	}
	var out SlotView
	err := s.inTx(ctx, func(tx pgx.Tx, _ *changes) error {
		sl, err := store.GetSlot(ctx, tx, id, true)
		if isNotFound(err) {
			return ErrSlotNotFound
		}
		if err != nil {
			return err
		}
		if sl.Status != domain.StatusHeld || sl.HoldRef == nil || *sl.HoldRef != ref {
			return ErrHoldLost
		}
		target := s.Now().Add(time.Duration(minutes) * time.Minute)
		if sl.HoldExpiresAt == nil || sl.HoldExpiresAt.Before(target) {
			if _, err := tx.Exec(ctx, `UPDATE slots SET hold_expires_at = $2, updated_at = now() WHERE id = $1`, id, target); err != nil {
				return err
			}
			sl.HoldExpiresAt = &target
		}
		out, err = s.viewOne(ctx, tx, sl)
		return err
	})
	return out, err
}

// Release — saga kompensatsiyasi (booking o'zi chaqiradi, shuning uchun slot.released yuborilmaydi). Idempotent.
func (s *Service) Release(ctx context.Context, id uuid.UUID, ref string) error {
	return s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		_, err := s.releaseByRef(ctx, tx, ch, id, ref, "")
		return err
	})
}

// releaseByRef — shu bron (hold_ref) ning HELD/BOOKED slotini bo'shatadi; reason bo'lsa — slot.released.
func (s *Service) releaseByRef(ctx context.Context, tx pgx.Tx, ch *changes, id uuid.UUID, ref, reason string) (bool, error) {
	sl, err := store.GetSlot(ctx, tx, id, true)
	if isNotFound(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if sl.HoldRef == nil || *sl.HoldRef != ref || (sl.Status != domain.StatusHeld && sl.Status != domain.StatusBooked) {
		return false, nil
	}
	if err := s.freeSlot(ctx, tx, ch, sl); err != nil {
		return false, err
	}
	if reason != "" {
		return true, s.publishReleased(ctx, tx, sl, &ref, reason)
	}
	return true, nil
}

// ExpireHolds — muddati o'tgan hold'lar (har daqiqa). CAS: `status='held' AND hold_expires_at < now` —
// shu orada boshqa bron qayta band qilgan slot bo'shatilmaydi (ADR-0011 #3). Qaytaradi: bo'shatilganlar.
func (s *Service) ExpireHolds(ctx context.Context, limit int) (int, error) {
	n := 0
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		rows, err := tx.Query(ctx, `WITH expired AS (
				SELECT id, hold_ref FROM slots WHERE status = 'held' AND hold_expires_at < $1
				ORDER BY hold_expires_at LIMIT $2 FOR UPDATE SKIP LOCKED)
			UPDATE slots s SET status = 'free', hold_ref = NULL, hold_expires_at = NULL, travel_buffer_minutes = NULL,
				updated_at = now()
			FROM expired e WHERE s.id = e.id AND s.status = 'held' AND s.hold_expires_at < $1
			RETURNING s.id, s.doctor_id, s.clinic_id, s.start_at, s.end_at, e.hold_ref`, s.Now(), limit)
		if err != nil {
			return err
		}
		type released struct {
			slot domain.Slot
			ref  *string
		}
		var list []released
		for rows.Next() {
			var r released
			if err := rows.Scan(&r.slot.ID, &r.slot.DoctorID, &r.slot.ClinicID, &r.slot.StartAt, &r.slot.EndAt, &r.ref); err != nil {
				rows.Close()
				return err
			}
			list = append(list, r)
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return err
		}
		for _, r := range list {
			ch.touch(r.slot.DoctorID, r.slot.StartAt)
			if err := s.closeFreed(ctx, tx, r.slot); err != nil {
				return err
			}
			if err := s.releaseBuffer(ctx, tx, ch, r.slot.ID); err != nil {
				return err
			}
			if err := s.publishReleased(ctx, tx, r.slot, r.ref, "hold_expired"); err != nil {
				return err
			}
		}
		n = len(list)
		return nil
	})
	return n, err
}

// PurgeOld — `days` dan eski, hech qachon band qilinmagan slotlar (E14). Bron tarixi borlari qoladi.
func (s *Service) PurgeOld(ctx context.Context, days int) (int64, error) {
	return store.Exec(ctx, s.Pool, `DELETE FROM slots WHERE start_at < $1 AND NOT ever_held AND status IN ('free', 'blocked')`,
		s.Now().AddDate(0, 0, -days))
}

// --- ommaviy ro'yxat --------------------------------------------------------------------------

// PublicSlot — mijozga ko'rinadigan bo'sh slot.
type PublicSlot struct {
	ID                 uuid.UUID  `json:"id"`
	StartAt            time.Time  `json:"start_at"`
	EndAt              time.Time  `json:"end_at"`
	ClinicID           *uuid.UUID `json:"clinic_id"`
	Place              string     `json:"place"`
	PriceAdjustPercent int        `json:"price_adjust_percent"`
}

func (s *Service) publicSlots(ctx context.Context, doctorID uuid.UUID, from, to time.Time, limit int) ([]PublicSlot, error) {
	slots, err := store.AvailableSlots(ctx, s.Pool, doctorID, from, to, s.Now(), limit)
	if err != nil {
		return nil, err
	}
	rules, err := store.PriceRules(ctx, s.Pool, doctorID)
	if err != nil {
		return nil, err
	}
	out := make([]PublicSlot, 0, len(slots))
	for _, sl := range slots {
		out = append(out, PublicSlot{ID: sl.ID, StartAt: sl.StartAt, EndAt: sl.EndAt, ClinicID: sl.ClinicID,
			Place: sl.Place(), PriceAdjustPercent: domain.PercentFor(rules, sl.StartAt)})
	}
	return out, nil
}

// MaxRangeDays va MaxRangeSlots — ommaviy oraliq so'rovi chegaralari.
const (
	MaxRangeDays  = 31
	MaxRangeSlots = 200
)

func (s *Service) leadFilter(ctx context.Context, slots []PublicSlot) []PublicSlot {
	earliest := s.Now().Add(time.Duration(s.Settings.Schedule(ctx).MinLeadTimeMinutes) * time.Minute)
	out := slots[:0:0]
	for _, sl := range slots {
		if !sl.StartAt.Before(earliest) {
			out = append(out, sl)
		}
	}
	return out
}

// DaySlots — bitta mahalliy kun (Redis cache-aside 30 s; lead time javob paytida qo'llanadi).
func (s *Service) DaySlots(ctx context.Context, doctorID uuid.UUID, day domain.Date) ([]PublicSlot, error) {
	raw, err := s.Cache.GetOrLoad(ctx, cache.Key(doctorID, day), func() ([]byte, error) {
		from, to := domain.DayBounds(day)
		slots, err := s.publicSlots(ctx, doctorID, from, to, 500)
		if err != nil {
			return nil, err
		}
		return json.Marshal(slots)
	})
	if err != nil {
		return nil, err
	}
	var slots []PublicSlot
	if err := json.Unmarshal(raw, &slots); err != nil {
		return nil, err
	}
	return s.leadFilter(ctx, slots), nil
}

// RangeSlots — oraliq (keshsiz, ≤ 31 kun, ≤ 200 slot).
func (s *Service) RangeSlots(ctx context.Context, doctorID uuid.UUID, from, to time.Time) ([]PublicSlot, error) {
	if !to.After(from) || to.Sub(from) > MaxRangeDays*24*time.Hour {
		return nil, gchttp.BadRequest("INVALID_RANGE", "date_to date_from dan keyin va oraliq ≤ 31 kun bo'lishi kerak")
	}
	slots, err := s.publicSlots(ctx, doctorID, from, to, MaxRangeSlots*2)
	if err != nil {
		return nil, err
	}
	slots = s.leadFilter(ctx, slots)
	if len(slots) > MaxRangeSlots {
		slots = slots[:MaxRangeSlots]
	}
	return slots, nil
}

// FreeSlotsQuery — internal free-slots.
type FreeSlotsQuery struct {
	DoctorID   uuid.UUID
	From, To   time.Time
	Place      string
	ClinicID   *uuid.UUID
	MinMinutes int
	Limit      int
}

// FreeSlots — booking uchun muqobillar (lead time — chaqiruvchida).
func (s *Service) FreeSlots(ctx context.Context, q FreeSlotsQuery) ([]SlotView, error) {
	switch {
	case q.Limit <= 0:
		q.Limit = 5
	case q.Limit > 50:
		q.Limit = 50
	}
	slots, err := store.AvailableSlots(ctx, s.Pool, q.DoctorID, q.From, q.To, s.Now(), 1000)
	if err != nil {
		return nil, err
	}
	rules, err := store.PriceRules(ctx, s.Pool, q.DoctorID)
	if err != nil {
		return nil, err
	}
	out := []SlotView{}
	for _, sl := range slots {
		if (q.Place != "" && sl.Place() != q.Place) || (q.ClinicID != nil && !domain.SameClinic(sl.ClinicID, q.ClinicID)) ||
			sl.DurationMinutes() < q.MinMinutes {
			continue
		}
		out = append(out, s.view(sl, rules))
		if len(out) >= q.Limit {
			break
		}
	}
	return out, nil
}

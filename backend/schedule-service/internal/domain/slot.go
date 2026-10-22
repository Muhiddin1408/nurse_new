package domain

import (
	"time"

	"github.com/google/uuid"
)

// Slot — vaqt oralig'i. Muddati o'tgan hold (HoldExpiresAt < now) har o'qishda FREE hisoblanadi (lazy expiry, E8).
type Slot struct {
	ID                  uuid.UUID
	DoctorID            uuid.UUID
	ClinicID            *uuid.UUID
	RoomID              *uuid.UUID
	StartAt             time.Time
	EndAt               time.Time
	Status              string
	HoldRef             *string
	HoldExpiresAt       *time.Time
	BlockReason         string
	BlockNote           string
	TimeOffID           *uuid.UUID
	BlockedBySlotID     *uuid.UUID
	TravelBufferMinutes *int
	EverHeld            bool
}

// Place — clinic | home.
func (s Slot) Place() string {
	if s.ClinicID == nil {
		return PlaceHome
	}
	return PlaceClinic
}

// HoldExpired — HELD, lekin muddati o'tgan.
func (s Slot) HoldExpired(now time.Time) bool {
	return s.Status == StatusHeld && s.HoldExpiresAt != nil && s.HoldExpiresAt.Before(now)
}

// EffectiveStatus — tashqariga ko'rinadigan holat.
func (s Slot) EffectiveStatus(now time.Time) string {
	if s.HoldExpired(now) {
		return StatusFree
	}
	return s.Status
}

// IsLive — faol bron bilan band (HELD muddati o'tmagan yoki BOOKED) va kelajakda.
func (s Slot) IsLive(now time.Time) bool {
	if !s.StartAt.After(now) {
		return false
	}
	return s.Status == StatusBooked || (s.Status == StatusHeld && !s.HoldExpired(now))
}

// DurationMinutes — slot uzunligi.
func (s Slot) DurationMinutes() int { return int(s.EndAt.Sub(s.StartAt).Minutes()) }

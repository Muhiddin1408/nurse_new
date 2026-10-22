// Package domain — kanallar va event → klient xabari xaritasi (sof funksiyalar, I/O yo'q).
//
// Kanallar (SERVICE_REGISTRY 16):
//
//	user:<id>              — avtomatik (ulanishda), shu foydalanuvchining bronlari va to'lovlari
//	booking:<id>           — obuna bilan (booking `/authz`: mijoz yoki shu bronning shifokori)
//	doctor:<id>:schedule   — obuna bilan (faqat token'dagi `doctor_id` = id)
//
// Xabar: `{type, data, occurred_at}`. `data` — UI yangilashga yetarli MINIMAL maydonlar: bemor, manzil,
// izoh, telefon kabi PII hech qachon WebSocket'ga chiqmaydi (klient kerak bo'lsa REST'dan oladi).
package domain

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
)

// TypeKick — boshqaruv xabari: shu foydalanuvchining ulanishlari yopiladi (akkaunt bloklandi).
const TypeKick = "user.deactivated"

// Message — klientga yuboriladigan xabar.
type Message struct {
	Type       string         `json:"type"`
	Data       map[string]any `json:"data"`
	OccurredAt time.Time      `json:"occurred_at"`
}

// Delivery — bitta event natijasi: qaysi kanallarga qaysi xabar.
type Delivery struct {
	Channels []string
	Message  Message
}

// UserChannel va boshqalar — kanal nomlari.
func UserChannel(id uuid.UUID) string     { return "user:" + id.String() }
func BookingChannel(id uuid.UUID) string  { return "booking:" + id.String() }
func ScheduleChannel(id uuid.UUID) string { return "doctor:" + id.String() + ":schedule" }

// ChannelKind — obuna so'rovidagi kanal turi.
type ChannelKind int

// Kanal turlari.
const (
	KindUser ChannelKind = iota + 1
	KindBooking
	KindSchedule
)

// ErrBadChannel — noto'g'ri kanal nomi.
var ErrBadChannel = errors.New("noto'g'ri kanal")

// ParseChannel — `booking:<uuid>` | `doctor:<uuid>:schedule` | `user:<uuid>`.
func ParseChannel(s string) (ChannelKind, uuid.UUID, error) {
	parts := strings.Split(s, ":")
	switch {
	case len(parts) == 2 && parts[0] == "booking":
		id, err := uuid.Parse(parts[1])
		return KindBooking, id, wrap(err)
	case len(parts) == 3 && parts[0] == "doctor" && parts[2] == "schedule":
		id, err := uuid.Parse(parts[1])
		return KindSchedule, id, wrap(err)
	case len(parts) == 2 && parts[0] == "user":
		id, err := uuid.Parse(parts[1])
		return KindUser, id, wrap(err)
	}
	return 0, uuid.Nil, ErrBadChannel
}

func wrap(err error) error {
	if err != nil {
		return fmt.Errorf("%w: %w", ErrBadChannel, err)
	}
	return nil
}

// Har event uchun klientga chiqadigan maydonlar (oq ro'yxat). Kalit — event turi.
var fields = map[string][]string{
	"booking.created":               {"booking_id", "number", "doctor_id", "status", "place", "start_at", "payment_mode", "prepay_tiyin", "payment_due_at"},
	"booking.confirmed":             {"booking_id", "number", "doctor_id", "start_at", "confirmed_at"},
	"booking.cancelled":             {"booking_id", "number", "cancelled_by", "reason_code", "refund_tiyin", "previous_status"},
	"booking.expired":               {"booking_id", "number"},
	"booking.rescheduled":           {"booking_id", "number", "new_start_at", "new_end_at", "rescheduled_count"},
	"booking.started":               {"booking_id", "started_at"},
	"booking.completed":             {"booking_id", "number", "completed_at"},
	"booking.no_show":               {"booking_id", "number", "no_show_by"},
	"booking.confirmation_failed":   {"booking_id", "reason", "amount_tiyin"},
	"payment.captured":              {"payment_id", "booking_id", "provider", "amount_tiyin", "captured_at"},
	"payment.failed":                {"payment_id", "booking_id", "provider", "reason"},
	"waitlist.slot_offered":         {"entry_id", "doctor_id", "slot_id", "start_at", "offer_expires_at"},
	"schedule.availability_changed": {"doctor_id", "dates", "next_available_at"},
	TypeKick:                        {"reason"},
}

// Events — router qo'llab-quvvatlaydigan event turlari (har biriga consumer handleri bor).
func Events() []string {
	out := make([]string, 0, len(fields))
	for k := range fields {
		out = append(out, k)
	}
	return out
}

// Route — event'dan kanallar va xabar. Noma'lum tur yoki kerakli ID yo'q — xato (consumer DLQ'ga).
func Route(eventType string, occurredAt time.Time, raw json.RawMessage) (Delivery, error) {
	keep, ok := fields[eventType]
	if !ok {
		return Delivery{}, fmt.Errorf("ws-gateway %s ni yo'naltirmaydi", eventType)
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		return Delivery{}, fmt.Errorf("event data: %w", err)
	}
	msg := Message{Type: eventType, Data: make(map[string]any, len(keep)), OccurredAt: occurredAt.UTC()}
	for _, k := range keep {
		if v, present := data[k]; present {
			msg.Data[k] = v
		}
	}

	var channels []string
	add := func(key string, ch func(uuid.UUID) string) error {
		id, err := uuid.Parse(fmt.Sprint(data[key]))
		if err != nil {
			return fmt.Errorf("%s: %s noto'g'ri: %w", eventType, key, err)
		}
		channels = append(channels, ch(id))
		return nil
	}
	var err error
	switch eventType {
	case TypeKick:
		err = add("user_id", UserChannel)
	case "schedule.availability_changed":
		err = add("doctor_id", ScheduleChannel)
	case "waitlist.slot_offered":
		err = add("client_id", UserChannel)
	default: // booking.* va payment.* — mijozga va shu bron kanaliga (shifokor ekrani obuna bo'lgan bo'lsa)
		if err = add("client_id", UserChannel); err == nil {
			err = add("booking_id", BookingChannel)
		}
	}
	if err != nil {
		return Delivery{}, err
	}
	return Delivery{Channels: channels, Message: msg}, nil
}

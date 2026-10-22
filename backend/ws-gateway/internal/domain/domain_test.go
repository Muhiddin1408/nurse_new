package domain_test

import (
	"encoding/json"
	"slices"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/events"

	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/domain"
)

func TestEveryCatalogEventIsRouted(t *testing.T) {
	var want []string
	for name, spec := range events.Catalog {
		if spec.HasConsumer("ws-gateway") {
			want = append(want, name)
		}
	}
	got := domain.Events()
	slices.Sort(want)
	slices.Sort(got)
	if !slices.Equal(want, got) {
		t.Fatalf("katalog %v, router %v", want, got)
	}
}

func TestBookingEventGoesToClientAndBookingWithoutPII(t *testing.T) {
	booking, client := uuid.New(), uuid.New()
	raw, _ := json.Marshal(map[string]any{
		"booking_id": booking, "client_id": client, "number": "MB-1", "doctor_id": uuid.New(),
		"patient_id": uuid.New(), "start_at": "2026-10-22T05:00:00Z", "confirmed_at": "2026-10-21T05:00:00Z",
		"money": map[string]any{"total_tiyin": 1}, "address": "Navoiy 10",
	})
	at := time.Date(2026, 10, 21, 5, 0, 0, 0, time.FixedZone("x", 5*3600))
	d, err := domain.Route("booking.confirmed", at, raw)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(d.Channels, []string{domain.UserChannel(client), domain.BookingChannel(booking)}) {
		t.Fatal(d.Channels)
	}
	for _, leaked := range []string{"patient_id", "money", "address", "client_id"} {
		if _, ok := d.Message.Data[leaked]; ok {
			t.Fatal("PII/ichki maydon chiqib ketdi:", leaked)
		}
	}
	if d.Message.Data["number"] != "MB-1" || d.Message.OccurredAt.Location() != time.UTC {
		t.Fatal(d.Message)
	}
}

func TestSpecialRoutes(t *testing.T) {
	doctor, user := uuid.New(), uuid.New()
	sched, _ := json.Marshal(map[string]any{"doctor_id": doctor, "dates": []string{"2026-10-22"}, "next_available_at": nil})
	d, err := domain.Route("schedule.availability_changed", time.Now(), sched)
	if err != nil || !slices.Equal(d.Channels, []string{domain.ScheduleChannel(doctor)}) {
		t.Fatal(d, err)
	}
	kick, _ := json.Marshal(map[string]any{"user_id": user, "reason": "blocked"})
	d, err = domain.Route(domain.TypeKick, time.Now(), kick)
	if err != nil || !slices.Equal(d.Channels, []string{domain.UserChannel(user)}) || d.Message.Type != domain.TypeKick {
		t.Fatal(d, err)
	}
	offer, _ := json.Marshal(map[string]any{"client_id": user, "entry_id": uuid.New(), "doctor_id": doctor})
	d, err = domain.Route("waitlist.slot_offered", time.Now(), offer)
	if err != nil || !slices.Equal(d.Channels, []string{domain.UserChannel(user)}) {
		t.Fatal(d, err)
	}
	if _, err := domain.Route("payment.captured", time.Now(), []byte(`{"booking_id":"x"}`)); err == nil {
		t.Fatal("ID'siz event yo'naltirilmasligi kerak")
	}
	if _, err := domain.Route("review.published", time.Now(), []byte(`{}`)); err == nil {
		t.Fatal("noma'lum tur")
	}
	if _, err := domain.Route("payment.failed", time.Now(), []byte(`not json`)); err == nil {
		t.Fatal("buzuq json")
	}
}

func TestParseChannel(t *testing.T) {
	id := uuid.New()
	cases := map[string]domain.ChannelKind{
		"booking:" + id.String():              domain.KindBooking,
		"doctor:" + id.String() + ":schedule": domain.KindSchedule,
		"user:" + id.String():                 domain.KindUser,
	}
	for s, kind := range cases {
		k, got, err := domain.ParseChannel(s)
		if err != nil || k != kind || got != id {
			t.Fatal(s, k, got, err)
		}
	}
	for _, bad := range []string{"", "booking:x", "doctor:" + id.String(), "rtws:*", "booking:" + id.String() + ":x"} {
		if _, _, err := domain.ParseChannel(bad); err == nil {
			t.Fatal("qabul qilinmasligi kerak:", bad)
		}
	}
}

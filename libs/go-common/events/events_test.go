package events_test

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/events"
)

func vector(t *testing.T) map[string]any {
	t.Helper()
	raw, err := os.ReadFile("../../testdata/envelope-vector.json")
	if err != nil {
		t.Fatal(err)
	}
	var v struct {
		Envelope map[string]any `json:"envelope"`
	}
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatal(err)
	}
	return v.Envelope
}

func TestPythonEnvelopeValidInGo(t *testing.T) {
	b, _ := json.Marshal(vector(t))
	if err := events.ValidateJSON(b); err != nil {
		t.Fatal(err)
	}
}

func TestSchemaViolationsDetected(t *testing.T) {
	mutations := map[string]func(map[string]any){
		"extra data field": func(e map[string]any) { e["data"].(map[string]any)["oops"] = 1 },
		"bad enum":         func(e map[string]any) { e["data"].(map[string]any)["place"] = "moon" },
		"negative money":   func(e map[string]any) { e["data"].(map[string]any)["refund_tiyin"] = -1 },
		"bad uuid":         func(e map[string]any) { e["data"].(map[string]any)["booking_id"] = "nope" },
		"missing field":    func(e map[string]any) { delete(e["data"].(map[string]any), "slot_id") },
		"extra envelope":   func(e map[string]any) { e["junk"] = true },
		"wrong version":    func(e map[string]any) { e["event_version"] = 2 },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			env := vector(t)
			mutate(env)
			b, _ := json.Marshal(env)
			if err := events.ValidateJSON(b); err == nil {
				t.Fatal("expected schema error")
			}
		})
	}
	if err := events.ValidateJSON([]byte(`{"event_type":"rating.new"}`)); err == nil ||
		!strings.Contains(err.Error(), "katalogda") {
		t.Fatal("unknown event must fail", err)
	}
}

func TestCatalogConstants(t *testing.T) {
	spec, ok := events.Lookup(events.BookingConfirmed)
	if !ok || spec.EventType != "booking.confirmed" || !spec.HasPublisher("booking-service") ||
		!spec.HasConsumer("notification-service") || spec.HasConsumer("auth-service") {
		t.Fatal(spec)
	}
	if len(events.Catalog) != 53 {
		t.Fatalf("catalog size %d", len(events.Catalog))
	}
}

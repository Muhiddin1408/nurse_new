// Package eventbus — yagona event bus (BLUEPRINT 8): topologiya, envelope, publisher, outbox,
// relay va idempotent consumer. Python libs/py-common/eventbus bilan bir xil kontrakt.
package eventbus

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"
)

// Envelope — barcha eventlar uchun yagona konvert (contracts/events/_envelope.v1.json).
type Envelope struct {
	EventID       uuid.UUID       `json:"event_id"`
	EventType     string          `json:"event_type"`
	EventVersion  int             `json:"event_version"`
	OccurredAt    time.Time       `json:"occurred_at"`
	Producer      string          `json:"producer"`
	TraceID       *string         `json:"trace_id"`
	AggregateType string          `json:"aggregate_type"`
	AggregateID   string          `json:"aggregate_id"`
	Data          json.RawMessage `json:"data"`
}

// NewEnvelope — event_id UUIDv7, occurred_at UTC.
func NewEnvelope(eventType string, version int, producer, aggregateType, aggregateID string, data any, traceID string) (Envelope, error) {
	raw, err := json.Marshal(data)
	if err != nil {
		return Envelope{}, fmt.Errorf("event data: %w", err)
	}
	id, err := uuid.NewV7()
	if err != nil {
		return Envelope{}, err
	}
	env := Envelope{
		EventID: id, EventType: eventType, EventVersion: version,
		OccurredAt: time.Now().UTC(), Producer: producer,
		AggregateType: aggregateType, AggregateID: aggregateID, Data: raw,
	}
	if traceID != "" {
		env.TraceID = &traceID
	}
	return env, nil
}

// Decode — data qismini struct'ga.
func (e Envelope) Decode(dst any) error { return json.Unmarshal(e.Data, dst) }

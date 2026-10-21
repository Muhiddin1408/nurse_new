package eventbus

import (
	"fmt"
	"slices"

	amqp "github.com/rabbitmq/amqp091-go"
)

// RetryTierHeader — `x-` prefiksisiz! RabbitMQ headers exchange `x-*` header'larni moslashtirishda
// e'tiborsiz qoldiradi; `x-retry-tier` bilan xabar BARCHA retry navbatlariga tushardi.
const (
	RetryTierHeader  = "retry-tier"
	RetryCountHeader = "x-retry-count"
)

// Tier — retry darajasi.
type Tier struct {
	Name  string
	TTLms int64
}

// Topology — ARCHITECTURE.md 8.1 (Python Topology bilan bir xil nomlar).
type Topology struct {
	Exchange      string
	DLX           string
	DLQ           string
	RetryExchange string
	RetryTiers    []Tier
	Quorum        bool
}

// Default — prod topologiyasi.
var Default = Topology{
	Exchange: "medbron.events", DLX: "medbron.dlx", DLQ: "medbron.dlq", RetryExchange: "medbron.retry",
	RetryTiers: []Tier{{"5s", 5_000}, {"30s", 30_000}, {"5m", 300_000}},
	Quorum:     true,
}

// RetryQueue — tier navbati nomi.
func (t Topology) RetryQueue(tier string) string { return fmt.Sprintf("%s.%s", t.RetryExchange, tier) }

// Declare — umumiy exchange/DLX/DLQ/retry (idempotent).
func (t Topology) Declare(ch *amqp.Channel) error {
	if err := ch.ExchangeDeclare(t.Exchange, amqp.ExchangeTopic, true, false, false, false, nil); err != nil {
		return err
	}
	if err := ch.ExchangeDeclare(t.DLX, amqp.ExchangeTopic, true, false, false, false, nil); err != nil {
		return err
	}
	dlqArgs := amqp.Table{}
	if t.Quorum {
		dlqArgs["x-queue-type"] = "quorum"
	}
	if _, err := ch.QueueDeclare(t.DLQ, true, false, false, false, dlqArgs); err != nil {
		return err
	}
	if err := ch.QueueBind(t.DLQ, "#", t.DLX, false, nil); err != nil {
		return err
	}
	if err := ch.ExchangeDeclare(t.RetryExchange, amqp.ExchangeHeaders, true, false, false, false, nil); err != nil {
		return err
	}
	for _, tier := range t.RetryTiers {
		q := t.RetryQueue(tier.Name)
		args := amqp.Table{"x-message-ttl": tier.TTLms, "x-dead-letter-exchange": ""}
		if _, err := ch.QueueDeclare(q, true, false, false, false, args); err != nil {
			return err
		}
		bind := amqp.Table{"x-match": "all", RetryTierHeader: tier.Name}
		if err := ch.QueueBind(q, "", t.RetryExchange, false, bind); err != nil {
			return err
		}
	}
	return nil
}

// DeclareServiceQueue — servisning YAGONA navbati va binding'lari (BLUEPRINT 8.2).
func (t Topology) DeclareServiceQueue(ch *amqp.Channel, queue string, keys []string) error {
	if err := ch.ExchangeDeclare(t.Exchange, amqp.ExchangeTopic, true, false, false, false, nil); err != nil {
		return err
	}
	args := amqp.Table{"x-dead-letter-exchange": t.DLX}
	if t.Quorum {
		args["x-queue-type"] = "quorum"
	}
	if _, err := ch.QueueDeclare(queue, true, false, false, false, args); err != nil {
		return err
	}
	sorted := slices.Clone(keys)
	slices.Sort(sorted)
	for _, k := range slices.Compact(sorted) {
		if err := ch.QueueBind(queue, k, t.Exchange, false, nil); err != nil {
			return err
		}
	}
	return nil
}

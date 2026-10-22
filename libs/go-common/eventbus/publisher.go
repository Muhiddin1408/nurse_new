package eventbus

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

// Publisher — brokerga publish (publisher confirms bilan). FAQAT outbox relay ishlatadi:
// servis kodida to'g'ridan-to'g'ri publish taqiqlangan (BLUEPRINT 8.4).
type Publisher struct {
	URL      string
	Topology Topology

	mu   sync.Mutex
	conn *amqp.Connection
	ch   *amqp.Channel
}

// NewPublisher — ulanish birinchi Publish'da (va uzilgandan keyin qayta) ochiladi.
func NewPublisher(url string, t Topology) *Publisher { return &Publisher{URL: url, Topology: t} }

func (p *Publisher) ensure() error {
	if p.ch != nil && !p.ch.IsClosed() {
		return nil
	}
	p.closeLocked()
	conn, err := amqp.Dial(p.URL)
	if err != nil {
		return err
	}
	ch, err := conn.Channel()
	if err != nil {
		_ = conn.Close()
		return err
	}
	if err := ch.Confirm(false); err != nil {
		_ = conn.Close()
		return err
	}
	if err := p.Topology.Declare(ch); err != nil {
		_ = conn.Close()
		return err
	}
	p.conn, p.ch = conn, ch
	return nil
}

// Message — AMQP xabari (envelope JSON'idan).
func Message(payload []byte, traceparent string) (amqp.Publishing, string, error) {
	var env Envelope
	if err := json.Unmarshal(payload, &env); err != nil {
		return amqp.Publishing{}, "", err
	}
	headers := amqp.Table{"event_version": int64(env.EventVersion), "producer": env.Producer}
	if traceparent != "" {
		headers["traceparent"] = traceparent
	}
	return amqp.Publishing{
		Body:         payload,
		MessageId:    env.EventID.String(),
		Type:         env.EventType,
		ContentType:  "application/json",
		DeliveryMode: amqp.Persistent,
		Timestamp:    env.OccurredAt,
		AppId:        env.Producer,
		Headers:      headers,
	}, env.EventType, nil
}

// Publish — broker tasdiqlamaguncha qaytmaydi.
func (p *Publisher) Publish(ctx context.Context, payload []byte, traceparent string) error {
	msg, routingKey, err := Message(payload, traceparent)
	if err != nil {
		return err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if err := p.ensure(); err != nil {
		return err
	}
	confirm, err := p.ch.PublishWithDeferredConfirmWithContext(ctx, p.Topology.Exchange, routingKey, false, false, msg)
	if err != nil {
		p.closeLocked()
		return err
	}
	ok, err := confirm.WaitContext(ctx)
	if err != nil {
		p.closeLocked()
		return err
	}
	if !ok {
		return errors.New("broker xabarni rad etdi (nack)")
	}
	return nil
}

func (p *Publisher) closeLocked() {
	if p.conn != nil {
		_ = p.conn.Close()
	}
	p.conn, p.ch = nil, nil
}

// Close — ulanishni yopadi.
func (p *Publisher) Close() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.closeLocked()
}

// DefaultPublishTimeout — relay uchun.
const DefaultPublishTimeout = 5 * time.Second

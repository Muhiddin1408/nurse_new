// Package eventbus — ws-gateway event consumer.
//
// go-common `eventbus.Consumer` idempotentlikni Postgres `processed_events` bilan qiladi; ws-gateway'da
// Postgres YO'Q (reestr: ma'lumot saqlamaydi). Shuning uchun consumer shu yerda: topologiya, navbat,
// sxema tekshiruvi, retry darajalari (5s → 30s → 5m) va DLQ — go-common bilan AYNAN bir xil
// (`gcbus.Topology`, `events.ValidateJSON`), takror esa Redis `SET NX` (`EVENT_DEDUP_TTL`) bilan tanib olinadi.
// Handler muvaffaqiyatsiz bo'lsa belgi o'chiriladi — retry'da qayta ishlanadi.
package eventbus

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	amqp "github.com/rabbitmq/amqp091-go"
	"github.com/redis/go-redis/v9"

	gcbus "github.com/Muhiddin1408/nurse_new/libs/go-common/eventbus"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/events"
)

var consumed = promauto.NewCounterVec(prometheus.CounterOpts{
	Name: "ws_events_consumed_total", Help: "Qabul qilingan eventlar natijasi",
}, []string{"event_type", "result"})

// Handler — event'ni qayta ishlaydi (tranzaksiya yo'q — faqat pub/sub'ga tarqatish).
type Handler func(ctx context.Context, ev gcbus.Envelope) error

// Consumer — Redis bilan idempotent consumer.
type Consumer struct {
	Service    string
	Queue      string
	URL        string
	Redis      *redis.Client
	DedupTTL   time.Duration
	Prefetch   int
	MaxRetries int
	Topology   gcbus.Topology

	handlers map[string]Handler
}

// NewConsumer — standart: navbat "<servis>.events", prefetch 20, 3 retry.
func NewConsumer(service, url string, rdb *redis.Client, dedupTTL time.Duration) *Consumer {
	return &Consumer{
		Service: service, Queue: service + ".events", URL: url, Redis: rdb, DedupTTL: dedupTTL,
		Prefetch: 20, MaxRetries: 3, Topology: gcbus.Default, handlers: map[string]Handler{},
	}
}

// MustOn — handler. Katalogda shu servis consumer bo'lmasa — panic (Trasso #4: e'lon qilinmagan consumer).
func (c *Consumer) MustOn(eventType string, h Handler) {
	spec, ok := events.Lookup(eventType)
	if !ok || !spec.HasConsumer(c.Service) {
		panic(fmt.Sprintf("%s katalogda %s consumer'i emas", c.Service, eventType))
	}
	if _, dup := c.handlers[eventType]; dup {
		panic(eventType + " uchun handler allaqachon bor")
	}
	c.handlers[eventType] = h
}

// RoutingKeys — bind qilinadigan kalitlar.
func (c *Consumer) RoutingKeys() []string {
	keys := make([]string, 0, len(c.handlers))
	for k := range c.handlers {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}

// RunForever — uzilishda backoff bilan qayta ulanadi.
func (c *Consumer) RunForever(ctx context.Context) {
	backoff := time.Second
	for ctx.Err() == nil {
		err := c.Run(ctx)
		if ctx.Err() != nil {
			return
		}
		slog.ErrorContext(ctx, "consumer stopped, reconnecting", slog.String("error", fmt.Sprint(err)),
			slog.Duration("backoff", backoff))
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff = min(backoff*2, 30*time.Second)
	}
}

// Run — ulanadi va ctx bekor qilinguncha ishlaydi.
func (c *Consumer) Run(ctx context.Context) error {
	if len(c.handlers) == 0 {
		return errors.New("hech qanday handler ro'yxatdan o'tmagan")
	}
	conn, err := amqp.Dial(c.URL)
	if err != nil {
		return err
	}
	defer func() { _ = conn.Close() }()
	ch, err := conn.Channel()
	if err != nil {
		return err
	}
	if err := ch.Confirm(false); err != nil {
		return err
	}
	if err := ch.Qos(c.Prefetch, 0, false); err != nil {
		return err
	}
	if err := c.Topology.Declare(ch); err != nil {
		return err
	}
	if err := c.Topology.DeclareServiceQueue(ch, c.Queue, c.RoutingKeys()); err != nil {
		return err
	}
	tag := c.Queue + "-" + fmt.Sprint(time.Now().UnixNano())
	deliveries, err := ch.Consume(c.Queue, tag, false, false, false, false, nil)
	if err != nil {
		return err
	}
	slog.InfoContext(ctx, "consumer started", slog.String("queue", c.Queue), slog.Any("bindings", c.RoutingKeys()))

	sem := make(chan struct{}, max(1, c.Prefetch))
	var wg sync.WaitGroup
	defer wg.Wait()
	closed := conn.NotifyClose(make(chan *amqp.Error, 1))
	for {
		select {
		case <-ctx.Done():
			_ = ch.Cancel(tag, false)
			return nil
		case amqpErr := <-closed:
			if amqpErr == nil {
				return errors.New("amqp connection closed")
			}
			return fmt.Errorf("amqp connection closed: %w", amqpErr)
		case d, ok := <-deliveries:
			if !ok {
				return errors.New("deliveries channel closed")
			}
			sem <- struct{}{}
			wg.Add(1)
			go func() {
				defer func() { <-sem; wg.Done() }()
				c.process(context.WithoutCancel(ctx), ch, d)
			}()
		}
	}
}

func (c *Consumer) process(ctx context.Context, ch *amqp.Channel, d amqp.Delivery) {
	eventType := d.Type
	if eventType == "" {
		eventType = d.RoutingKey
	}
	var env gcbus.Envelope
	if err := json.Unmarshal(d.Body, &env); err != nil || env.EventType == "" || env.EventID == uuid.Nil {
		c.deadLetter(ctx, ch, d, eventType, "invalid_envelope", err)
		return
	}
	eventType = env.EventType
	h, ok := c.handlers[eventType]
	if !ok {
		c.deadLetter(ctx, ch, d, eventType, "no_handler", nil)
		return
	}
	if env.EventVersion != 1 {
		c.deadLetter(ctx, ch, d, eventType, "unknown_version", nil)
		return
	}
	if err := events.ValidateJSON(d.Body); err != nil {
		c.deadLetter(ctx, ch, d, eventType, "schema_mismatch", err)
		return
	}
	fresh, err := c.Handle(ctx, h, env)
	switch {
	case errors.Is(err, gcbus.ErrPermanent):
		c.deadLetter(ctx, ch, d, eventType, "permanent_error", err)
		return
	case err != nil:
		c.retry(ctx, ch, d, eventType, err)
		return
	}
	_ = d.Ack(false)
	result := "ok"
	if !fresh {
		result = "duplicate"
	}
	consumed.WithLabelValues(eventType, result).Inc()
}

// Handle — idempotent bajarish (testlar AMQP'siz shu orqali). Qaytaradi: false — takror, o'tkazildi.
func (c *Consumer) Handle(ctx context.Context, h Handler, env gcbus.Envelope) (bool, error) {
	key := "ws:evt:" + env.EventID.String()
	if c.Redis != nil {
		set, err := c.Redis.SetNX(ctx, key, env.EventType, c.DedupTTL).Result()
		if err != nil {
			return false, err // Redis yo'q — tarqatib ham bo'lmaydi; retry
		}
		if !set {
			return false, nil
		}
	}
	if err := h(ctx, env); err != nil {
		if c.Redis != nil {
			_ = c.Redis.Del(ctx, key).Err()
		}
		return false, err
	}
	return true, nil
}

func copyHeaders(h amqp.Table) amqp.Table {
	out := amqp.Table{}
	for k, v := range h {
		out[k] = v
	}
	return out
}

func asInt(v any) int {
	switch n := v.(type) {
	case int:
		return n
	case int32:
		return int(n)
	case int64:
		return int(n)
	case float64:
		return int(n)
	}
	return 0
}

func errText(err error) string {
	if err == nil {
		return ""
	}
	s := err.Error()
	if len(s) > 500 {
		s = s[:500]
	}
	return s
}

func (c *Consumer) republish(ctx context.Context, ch *amqp.Channel, exchange, key string, d amqp.Delivery, headers amqp.Table) error {
	confirm, err := ch.PublishWithDeferredConfirmWithContext(ctx, exchange, key, false, false, amqp.Publishing{
		Body: d.Body, Headers: headers, ContentType: d.ContentType, MessageId: d.MessageId,
		Type: d.Type, Timestamp: d.Timestamp, AppId: d.AppId, DeliveryMode: amqp.Persistent,
	})
	if err != nil {
		return err
	}
	ok, err := confirm.WaitContext(ctx)
	if err != nil {
		return err
	}
	if !ok {
		return errors.New("nack")
	}
	return nil
}

func (c *Consumer) retry(ctx context.Context, ch *amqp.Channel, d amqp.Delivery, eventType string, cause error) {
	count := asInt(d.Headers[gcbus.RetryCountHeader])
	if count >= c.MaxRetries {
		c.deadLetter(ctx, ch, d, eventType, "retries_exhausted", cause)
		return
	}
	tiers := c.Topology.RetryTiers
	tier := tiers[min(count, len(tiers)-1)].Name
	headers := copyHeaders(d.Headers)
	headers[gcbus.RetryCountHeader] = int64(count + 1)
	headers[gcbus.RetryTierHeader] = tier
	headers["x-last-error"] = errText(cause)
	if err := c.republish(ctx, ch, c.Topology.RetryExchange, c.Queue, d, headers); err != nil {
		_ = d.Nack(false, true)
		return
	}
	_ = d.Ack(false)
	consumed.WithLabelValues(eventType, "retry").Inc()
	slog.WarnContext(ctx, "event handling failed, scheduled retry", slog.String("event_type", eventType),
		slog.Int("retry", count+1), slog.String("tier", tier), slog.String("error", errText(cause)))
}

func (c *Consumer) deadLetter(ctx context.Context, ch *amqp.Channel, d amqp.Delivery, eventType, reason string, cause error) {
	headers := copyHeaders(d.Headers)
	headers["x-dead-letter-reason"] = reason
	headers["x-source-queue"] = c.Queue
	if cause != nil {
		headers["x-last-error"] = errText(cause)
	}
	consumed.WithLabelValues(eventType, "dead_letter").Inc()
	slog.ErrorContext(ctx, "event dead-lettered", slog.String("event_type", eventType),
		slog.String("reason", reason), slog.String("error", errText(cause)))
	if err := c.republish(ctx, ch, c.Topology.DLX, eventType, d, headers); err != nil {
		_ = d.Nack(false, false)
		return
	}
	_ = d.Ack(false)
}

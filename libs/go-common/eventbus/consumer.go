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
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	amqp "github.com/rabbitmq/amqp091-go"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/events"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/telemetry"
)

var (
	eventsConsumed = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "events_consumed_total", Help: "Qabul qilingan eventlar natijasi",
	}, []string{"event_type", "result"})
	eventHandleDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name: "event_handle_duration_seconds", Help: "Event handler davomiyligi",
	}, []string{"event_type"})
	consumerLag = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name: "consumer_lag_seconds", Help: "occurred_at dan qayta ishlashgacha",
		Buckets: []float64{0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 300, 900},
	}, []string{"event_type"})
)

// ErrPermanent — qayta urinishdan foyda yo'q → DLQ. `Permanent(err)` bilan o'rang.
var ErrPermanent = errors.New("permanent error")

// Permanent — xatoni doimiy deb belgilaydi.
func Permanent(err error) error { return fmt.Errorf("%w: %w", ErrPermanent, err) }

// Handler — biznes o'zgarishi va processed_events belgisi BITTA tx ichida.
type Handler func(ctx context.Context, tx pgx.Tx, ev Envelope) error

type registration struct {
	handler  Handler
	versions []int
}

// Consumer — idempotent consumer (BLUEPRINT 8.5), Python EventConsumer bilan bir xil xatti-harakat.
type Consumer struct {
	Service        string
	Queue          string
	URL            string
	Pool           *pgxpool.Pool
	Prefetch       int
	MaxRetries     int
	ValidateSchema bool
	EnforceCatalog bool
	Topology       Topology
	// processed_events cheksiz o'smasin. Retention eng uzoq qayta yetkazishdan (retry 5m + DLQ
	// replay oynasi) ancha katta bo'lishi kerak — aks holda dublikat o'tib ketadi.
	ProcessedRetention time.Duration
	PurgeInterval      time.Duration

	handlers map[string]registration
}

// NewConsumer — standart: navbat "<servis>.events", prefetch 20, 3 retry.
func NewConsumer(service, url string, pool *pgxpool.Pool) *Consumer {
	return &Consumer{
		Service: service, Queue: service + ".events", URL: url, Pool: pool,
		Prefetch: 20, MaxRetries: 3, ValidateSchema: true, EnforceCatalog: true,
		Topology: Default, handlers: map[string]registration{},
		ProcessedRetention: 30 * 24 * time.Hour, PurgeInterval: time.Hour,
	}
}

// On — handler ro'yxatdan o'tkazish. Katalogda consumer bo'lmasa — xato (Trasso #4).
func (c *Consumer) On(eventType string, h Handler, versions ...int) error {
	if len(versions) == 0 {
		versions = []int{1}
	}
	if c.EnforceCatalog {
		spec, ok := events.Lookup(eventType)
		if !ok {
			return fmt.Errorf("%q katalogda yo'q", eventType)
		}
		if !spec.HasConsumer(c.Service) {
			return fmt.Errorf("%s katalogda %s consumer'i emas — avval catalog.yaml ni yangilang", c.Service, eventType)
		}
	}
	if _, dup := c.handlers[eventType]; dup {
		return fmt.Errorf("%s uchun handler allaqachon bor", eventType)
	}
	c.handlers[eventType] = registration{handler: h, versions: versions}
	return nil
}

// MustOn — xato bo'lsa panic (main'da konfiguratsiya xatosi darhol ko'rinsin).
func (c *Consumer) MustOn(eventType string, h Handler, versions ...int) {
	if err := c.On(eventType, h, versions...); err != nil {
		panic(err)
	}
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

// Run — ulanadi va ctx bekor qilinguncha ishlaydi. Ulanish uzilsa xato qaytaradi
// (RunForever qayta ulanadi).
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

// PurgeProcessed — ProcessedRetention dan eski processed_events qatorlarini o'chiradi.
func (c *Consumer) PurgeProcessed(ctx context.Context) (int64, error) {
	tag, err := c.Pool.Exec(ctx, `DELETE FROM processed_events WHERE processed_at < now() - $1::interval`,
		fmt.Sprintf("%d seconds", int64(c.ProcessedRetention.Seconds())))
	if err != nil {
		return 0, err
	}
	return tag.RowsAffected(), nil
}

func (c *Consumer) purgeLoop(ctx context.Context) {
	interval := c.PurgeInterval
	if interval <= 0 {
		interval = time.Hour
	}
	for {
		if n, err := c.PurgeProcessed(ctx); err != nil && ctx.Err() == nil {
			slog.ErrorContext(ctx, "processed_events purge failed", slog.String("error", err.Error()))
		} else if n > 0 {
			slog.InfoContext(ctx, "processed_events purged", slog.Int64("deleted", n))
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(interval):
		}
	}
}

// RunForever — uzilishda backoff bilan qayta ulanadi; processed_events'ni davriy tozalaydi.
func (c *Consumer) RunForever(ctx context.Context) {
	go c.purgeLoop(ctx)
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

func (c *Consumer) process(ctx context.Context, ch *amqp.Channel, d amqp.Delivery) {
	eventType := d.Type
	if eventType == "" {
		eventType = d.RoutingKey
	}
	var env Envelope
	if err := json.Unmarshal(d.Body, &env); err != nil || env.EventType == "" || env.EventID == uuid.Nil {
		c.deadLetter(ctx, ch, d, eventType, "invalid_envelope", err)
		return
	}
	eventType = env.EventType
	reg, ok := c.handlers[eventType]
	if !ok {
		c.deadLetter(ctx, ch, d, eventType, "no_handler", nil)
		return
	}
	if !slices.Contains(reg.versions, env.EventVersion) {
		c.deadLetter(ctx, ch, d, eventType, "unknown_version", nil)
		return
	}
	if c.ValidateSchema {
		if err := events.ValidateJSON(d.Body); err != nil {
			c.deadLetter(ctx, ch, d, eventType, "schema_mismatch", err)
			return
		}
	}
	if tp, ok := d.Headers["traceparent"].(string); ok {
		ctx = telemetry.ExtractTraceparent(ctx, tp)
	}
	started := time.Now()
	handled, err := c.handle(ctx, reg.handler, env)
	eventHandleDuration.WithLabelValues(eventType).Observe(time.Since(started).Seconds())
	switch {
	case errors.Is(err, ErrPermanent):
		c.deadLetter(ctx, ch, d, eventType, "permanent_error", err)
		return
	case err != nil:
		c.retry(ctx, ch, d, eventType, err)
		return
	}
	_ = d.Ack(false)
	result := "ok"
	if !handled {
		result = "duplicate"
	}
	eventsConsumed.WithLabelValues(eventType, result).Inc()
	consumerLag.WithLabelValues(eventType).Observe(max(0, time.Since(env.OccurredAt).Seconds()))
}

func (c *Consumer) handle(ctx context.Context, h Handler, env Envelope) (bool, error) {
	tx, err := c.Pool.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	var inserted string
	err = tx.QueryRow(ctx, `INSERT INTO processed_events (event_id, event_type) VALUES ($1, $2)
		ON CONFLICT (event_id) DO NOTHING RETURNING event_id::text`, env.EventID, env.EventType).Scan(&inserted)
	if errors.Is(err, pgx.ErrNoRows) {
		slog.InfoContext(ctx, "duplicate event skipped", slog.String("event_id", env.EventID.String()),
			slog.String("event_type", env.EventType))
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if err := h(ctx, tx, env); err != nil {
		return false, err
	}
	return true, tx.Commit(ctx)
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
	case int8:
		return int(n)
	case int16:
		return int(n)
	case int32:
		return int(n)
	case int64:
		return int(n)
	case uint8:
		return int(n)
	case uint16:
		return int(n)
	case uint32:
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
	count := asInt(d.Headers[RetryCountHeader])
	if count >= c.MaxRetries {
		c.deadLetter(ctx, ch, d, eventType, "retries_exhausted", cause)
		return
	}
	tiers := c.Topology.RetryTiers
	tier := tiers[min(count, len(tiers)-1)].Name
	headers := copyHeaders(d.Headers)
	headers[RetryCountHeader] = int64(count + 1)
	headers[RetryTierHeader] = tier
	headers["x-last-error"] = errText(cause)
	// routing key = navbat nomi: TTL dan keyin default exchange FAQAT shu navbatga qaytaradi.
	if err := c.republish(ctx, ch, c.Topology.RetryExchange, c.Queue, d, headers); err != nil {
		slog.ErrorContext(ctx, "retry publish failed, requeueing", slog.String("error", err.Error()))
		_ = d.Nack(false, true)
		return
	}
	_ = d.Ack(false)
	eventsConsumed.WithLabelValues(eventType, "retry").Inc()
	slog.WarnContext(ctx, "event handling failed, scheduled retry", slog.String("event_type", eventType),
		slog.String("message_id", d.MessageId), slog.Int("retry", count+1), slog.String("tier", tier),
		slog.String("error", errText(cause)))
}

func (c *Consumer) deadLetter(ctx context.Context, ch *amqp.Channel, d amqp.Delivery, eventType, reason string, cause error) {
	headers := copyHeaders(d.Headers)
	headers["x-dead-letter-reason"] = reason
	headers["x-source-queue"] = c.Queue
	if cause != nil {
		headers["x-last-error"] = errText(cause)
	}
	eventsConsumed.WithLabelValues(eventType, "dead_letter").Inc()
	// ALERT: DLQ > 0.
	slog.ErrorContext(ctx, "event dead-lettered", slog.String("event_type", eventType),
		slog.String("message_id", d.MessageId), slog.String("reason", reason), slog.String("error", errText(cause)))
	if err := c.republish(ctx, ch, c.Topology.DLX, eventType, d, headers); err != nil {
		slog.ErrorContext(ctx, "dlx publish failed, falling back to queue DLX", slog.String("error", err.Error()))
		_ = d.Nack(false, false)
		return
	}
	_ = d.Ack(false)
}

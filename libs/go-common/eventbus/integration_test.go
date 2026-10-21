//go:build integration

// Integratsiya testlari: haqiqiy Postgres 16 va RabbitMQ 3.13 (testcontainers-go).
// Python tests/integration/test_eventbus.py bilan bir xil ssenariylar.
//
//	scripts/go.sh libs/go-common go test -tags integration ./eventbus/...
package eventbus_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	amqp "github.com/rabbitmq/amqp091-go"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/eventbus"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/events"
)

var (
	pgURL   string
	amqpURL string
)

func TestMain(m *testing.M) {
	ctx := context.Background()
	pg, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image:        "postgres:16-alpine",
			Env:          map[string]string{"POSTGRES_USER": "test", "POSTGRES_PASSWORD": "test", "POSTGRES_DB": "test"},
			ExposedPorts: []string{"5432/tcp"},
			WaitingFor:   wait.ForLog("database system is ready to accept connections").WithOccurrence(2),
		},
		Started: true,
	})
	if err != nil {
		panic(err)
	}
	rmq, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image:        "rabbitmq:3.13-alpine",
			Env:          map[string]string{"RABBITMQ_DEFAULT_USER": "test", "RABBITMQ_DEFAULT_PASS": "test"},
			ExposedPorts: []string{"5672/tcp"},
			WaitingFor:   wait.ForLog("Server startup complete").WithStartupTimeout(2 * time.Minute),
		},
		Started: true,
	})
	if err != nil {
		panic(err)
	}
	pgHost, _ := pg.Host(ctx)
	pgPort, _ := pg.MappedPort(ctx, "5432")
	rHost, _ := rmq.Host(ctx)
	rPort, _ := rmq.MappedPort(ctx, "5672")
	pgURL = fmt.Sprintf("postgres://test:test@%s:%s/test", pgHost, pgPort.Port())
	amqpURL = fmt.Sprintf("amqp://test:test@%s:%s/", rHost, rPort.Port())

	pool, err := pgxpool.New(ctx, pgURL)
	if err != nil {
		panic(err)
	}
	if _, err := pool.Exec(ctx, eventbus.SchemaSQL+`CREATE TABLE notes (id TEXT PRIMARY KEY);`); err != nil {
		panic(err)
	}
	pool.Close()

	code := m.Run()
	_ = pg.Terminate(ctx)
	_ = rmq.Terminate(ctx)
	os.Exit(code)
}

func setup(t *testing.T) (*pgxpool.Pool, eventbus.Topology) {
	t.Helper()
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, pgURL)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	if _, err := pool.Exec(ctx, `TRUNCATE outbox_events, processed_events, notes`); err != nil {
		t.Fatal(err)
	}
	prefix := "g" + uuid.NewString()[:8]
	return pool, eventbus.Topology{
		Exchange: prefix + ".events", DLX: prefix + ".dlx", DLQ: prefix + ".dlq", RetryExchange: prefix + ".retry",
		RetryTiers: []eventbus.Tier{{"a", 200}, {"b", 300}, {"c", 400}}, Quorum: true,
	}
}

func consumer(service string, pool *pgxpool.Pool, topo eventbus.Topology) *eventbus.Consumer {
	c := eventbus.NewConsumer(service, amqpURL, pool)
	c.Queue = topo.Exchange + "." + service
	c.Topology = topo
	return c
}

func start(t *testing.T, c *eventbus.Consumer) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- c.Run(ctx) }()
	t.Cleanup(func() {
		cancel()
		if err := <-done; err != nil {
			t.Log("consumer:", err)
		}
	})
	time.Sleep(300 * time.Millisecond) // navbat e'lon qilinsin
}

func scheduleChanged() map[string]any {
	return map[string]any{"doctor_id": uuid.NewString(), "dates": []string{"2026-10-22"}, "next_available_at": nil}
}

func publishEnvelope(t *testing.T, topo eventbus.Topology, eventType string, data any) []byte {
	t.Helper()
	env, err := eventbus.NewEnvelope(eventType, 1, "schedule-service", "doctor", uuid.NewString(), data, "")
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(env)
	pub := eventbus.NewPublisher(amqpURL, topo)
	defer pub.Close()
	if err := pub.Publish(context.Background(), b, ""); err != nil {
		t.Fatal(err)
	}
	return b
}

func drainDLQ(t *testing.T, topo eventbus.Topology, expect int, timeout time.Duration) []amqp.Delivery {
	t.Helper()
	conn, err := amqp.Dial(amqpURL)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = conn.Close() }()
	ch, _ := conn.Channel()
	var out []amqp.Delivery
	deadline := time.Now().Add(timeout)
	for len(out) < expect && time.Now().Before(deadline) {
		d, ok, err := ch.Get(topo.DLQ, true)
		if err != nil {
			t.Fatal(err)
		}
		if !ok {
			time.Sleep(100 * time.Millisecond)
			continue
		}
		out = append(out, d)
	}
	time.Sleep(300 * time.Millisecond)
	for {
		d, ok, _ := ch.Get(topo.DLQ, true)
		if !ok {
			break
		}
		out = append(out, d)
	}
	return out
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatal("condition not met")
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func TestOutboxRelayConsumerEndToEnd(t *testing.T) {
	pool, topo := setup(t)
	ctx := context.Background()
	var got atomic.Int32
	c := consumer("search-service", pool, topo)
	c.MustOn(events.ScheduleAvailabilityChanged, func(ctx context.Context, tx pgx.Tx, ev eventbus.Envelope) error {
		var d struct {
			DoctorID string `json:"doctor_id"`
		}
		if err := ev.Decode(&d); err != nil {
			return eventbus.Permanent(err)
		}
		_, err := tx.Exec(ctx, `INSERT INTO notes (id) VALUES ($1)`, "seen:"+d.DoctorID)
		got.Add(1)
		return err
	})
	start(t, c)

	ob := eventbus.NewOutbox("schedule-service")
	tx, _ := pool.Begin(ctx)
	if _, err := ob.AddEvent(ctx, tx, events.ScheduleAvailabilityChanged, uuid.NewString(), scheduleChanged()); err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	pub := eventbus.NewPublisher(amqpURL, topo)
	defer pub.Close()
	relay := eventbus.NewRelay(pool, pub)
	if n, err := relay.RunOnce(ctx); err != nil || n != 1 {
		t.Fatal(n, err)
	}
	waitFor(t, func() bool { return got.Load() == 1 })
	var status string
	_ = pool.QueryRow(ctx, `SELECT status FROM outbox_events`).Scan(&status)
	if status != "published" {
		t.Fatal(status)
	}
}

func TestOutboxRejectsContractViolations(t *testing.T) {
	pool, _ := setup(t)
	ctx := context.Background()
	tx, _ := pool.Begin(ctx)
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := eventbus.NewOutbox("booking-service").AddEvent(ctx, tx, events.ScheduleAvailabilityChanged, "x", scheduleChanged()); err == nil {
		t.Fatal("non-publisher must fail")
	}
	if _, err := eventbus.NewOutbox("schedule-service").AddEvent(ctx, tx, events.ScheduleAvailabilityChanged, "x", map[string]any{"doctor_id": "bad"}); err == nil {
		t.Fatal("schema violation must fail")
	}
}

func TestDuplicateHandledOnce(t *testing.T) {
	pool, topo := setup(t)
	var calls atomic.Int32
	c := consumer("search-service", pool, topo)
	c.MustOn(events.ScheduleAvailabilityChanged, func(context.Context, pgx.Tx, eventbus.Envelope) error {
		calls.Add(1)
		return nil
	})
	start(t, c)
	env, _ := eventbus.NewEnvelope(events.ScheduleAvailabilityChanged, 1, "schedule-service", "doctor", "d", scheduleChanged(), "")
	b, _ := json.Marshal(env)
	pub := eventbus.NewPublisher(amqpURL, topo)
	defer pub.Close()
	for range 3 {
		if err := pub.Publish(context.Background(), b, ""); err != nil {
			t.Fatal(err)
		}
	}
	time.Sleep(time.Second)
	if calls.Load() != 1 {
		t.Fatal(calls.Load())
	}
}

func TestRetryIsolationAndRollback(t *testing.T) {
	pool, topo := setup(t)
	var flaky, stable atomic.Int32
	fc := consumer("search-service", pool, topo)
	fc.MustOn(events.ScheduleAvailabilityChanged, func(ctx context.Context, tx pgx.Tx, ev eventbus.Envelope) error {
		n := flaky.Add(1)
		if _, err := tx.Exec(ctx, `INSERT INTO notes (id) VALUES ($1)`, fmt.Sprintf("flaky:%d", n)); err != nil {
			return err
		}
		if n < 3 {
			return errors.New("vaqtinchalik")
		}
		return nil
	})
	// ws-gateway ham shu eventni oladi (o'z navbati). Muhim tomoni: flaky'ning retry'lari unga QAYTA kelmaydi.
	sc := consumer("ws-gateway", pool, topo)
	sc.MustOn(events.ScheduleAvailabilityChanged, func(ctx context.Context, tx pgx.Tx, ev eventbus.Envelope) error {
		stable.Add(1)
		// Testda ikki "servis" bitta bazani (processed_events) bo'lishadi. Prod'da har servisda o'z
		// bazasi bor; bu yerda stable belgisini rollback qilamiz, flaky bilan to'qnashmasin.
		return errStableRollback
	})
	start(t, fc)
	start(t, sc)
	publishEnvelope(t, topo, events.ScheduleAvailabilityChanged, scheduleChanged())
	waitFor(t, func() bool { return flaky.Load() == 3 })
	time.Sleep(time.Second)
	if stable.Load() != 1 {
		// flaky'ning 2 ta retry'i faqat flaky navbatiga qaytdi — stable ularni QAYTA olmadi.
		t.Fatalf("stable got %d deliveries", stable.Load())
	}
	var notes []string
	rows, _ := pool.Query(context.Background(), `SELECT id FROM notes ORDER BY id`)
	notes, _ = pgx.CollectRows(rows, pgx.RowTo[string])
	if len(notes) != 1 || notes[0] != "flaky:3" {
		t.Fatal("failed attempts must roll back", notes)
	}
}

var errStableRollback = eventbus.Permanent(errors.New("stable consumer: test rollback"))

func TestRetriesExhaustedAndPermanentToDLQ(t *testing.T) {
	pool, topo := setup(t)
	var calls atomic.Int32
	c := consumer("search-service", pool, topo)
	c.MaxRetries = 2
	c.MustOn(events.ScheduleAvailabilityChanged, func(ctx context.Context, tx pgx.Tx, ev eventbus.Envelope) error {
		calls.Add(1)
		var d struct {
			Dates []string `json:"dates"`
		}
		_ = ev.Decode(&d)
		if len(d.Dates) == 2 {
			return eventbus.Permanent(errors.New("ma'nosiz"))
		}
		return errors.New("db lock")
	})
	start(t, c)
	publishEnvelope(t, topo, events.ScheduleAvailabilityChanged, scheduleChanged())
	perm := scheduleChanged()
	perm["dates"] = []string{"2026-10-22", "2026-10-23"}
	publishEnvelope(t, topo, events.ScheduleAvailabilityChanged, perm)
	bad := scheduleChanged()
	bad["doctor_id"] = 42
	publishEnvelope(t, topo, events.ScheduleAvailabilityChanged, bad)

	// yaroqsiz JSON
	conn, _ := amqp.Dial(amqpURL)
	ch, _ := conn.Channel()
	_ = ch.Publish(topo.Exchange, events.ScheduleAvailabilityChanged, false, false,
		amqp.Publishing{Body: []byte("{nope"), Type: events.ScheduleAvailabilityChanged})
	_ = conn.Close()

	dead := drainDLQ(t, topo, 4, 15*time.Second)
	reasons := map[string]int{}
	var exhausted amqp.Delivery
	for _, d := range dead {
		r, _ := d.Headers["x-dead-letter-reason"].(string)
		reasons[r]++
		if r == "retries_exhausted" {
			exhausted = d
		}
	}
	want := map[string]int{"retries_exhausted": 1, "permanent_error": 1, "schema_mismatch": 1, "invalid_envelope": 1}
	if fmt.Sprint(reasons) != fmt.Sprint(want) {
		t.Fatal(reasons)
	}
	if fmt.Sprint(exhausted.Headers["x-retry-count"]) != "2" {
		t.Fatal(exhausted.Headers)
	}
	if calls.Load() != 3+1 { // 1+2 retry + 1 permanent
		t.Fatal(calls.Load())
	}
}

type failingPublisher struct {
	mu        sync.Mutex
	failTimes int
	published []string
	delay     time.Duration
}

func (f *failingPublisher) Publish(ctx context.Context, payload []byte, _ string) error {
	time.Sleep(f.delay)
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failTimes > 0 {
		f.failTimes--
		return errors.New("broker yo'q")
	}
	var env eventbus.Envelope
	_ = json.Unmarshal(payload, &env)
	f.published = append(f.published, env.EventID.String())
	return nil
}

func addEvents(t *testing.T, pool *pgxpool.Pool, n int) {
	t.Helper()
	ctx := context.Background()
	tx, _ := pool.Begin(ctx)
	ob := eventbus.NewOutbox("schedule-service")
	for range n {
		if _, err := ob.AddEvent(ctx, tx, events.ScheduleAvailabilityChanged, uuid.NewString(), scheduleChanged()); err != nil {
			t.Fatal(err)
		}
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
}

func TestRelayBackoffAndFailed(t *testing.T) {
	pool, _ := setup(t)
	ctx := context.Background()
	addEvents(t, pool, 1)
	r := eventbus.NewRelay(pool, &failingPublisher{failTimes: 10})
	r.MaxAttempts, r.BackoffBase = 2, time.Minute
	if _, err := r.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}
	var status string
	var attempts int
	var future bool
	_ = pool.QueryRow(ctx, `SELECT status, attempts, next_attempt_at > now() + interval '50 seconds' FROM outbox_events`).
		Scan(&status, &attempts, &future)
	if status != "pending" || attempts != 1 || !future {
		t.Fatal(status, attempts, future)
	}
	if n, _ := r.RunOnce(ctx); n != 0 {
		t.Fatal("not due yet")
	}
	_, _ = pool.Exec(ctx, `UPDATE outbox_events SET next_attempt_at = now() - interval '1 second'`)
	_, _ = r.RunOnce(ctx)
	_ = pool.QueryRow(ctx, `SELECT status, attempts FROM outbox_events`).Scan(&status, &attempts)
	if status != "failed" || attempts != 2 {
		t.Fatal(status, attempts)
	}
}

func TestConcurrentRelaysSkipLocked(t *testing.T) {
	pool, _ := setup(t)
	addEvents(t, pool, 40)
	pub := &failingPublisher{delay: 5 * time.Millisecond}
	var wg sync.WaitGroup
	for range 4 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r := eventbus.NewRelay(pool, pub)
			r.BatchSize = 7
			for range 10 {
				_, _ = r.RunOnce(context.Background())
			}
		}()
	}
	wg.Wait()
	seen := map[string]bool{}
	for _, id := range pub.published {
		if seen[id] {
			t.Fatal("duplicate publish", id)
		}
		seen[id] = true
	}
	if len(seen) != 40 {
		t.Fatal(len(seen))
	}
}

func TestPurge(t *testing.T) {
	pool, _ := setup(t)
	ctx := context.Background()
	addEvents(t, pool, 3)
	r := eventbus.NewRelay(pool, &failingPublisher{})
	_, _ = r.RunOnce(ctx)
	_, _ = pool.Exec(ctx, `UPDATE outbox_events SET published_at = now() - interval '8 days' WHERE id = (SELECT min(id) FROM outbox_events)`)
	_, _ = pool.Exec(ctx, `UPDATE outbox_events SET status = 'failed' WHERE id = (SELECT max(id) FROM outbox_events)`)
	if n, err := r.Purge(ctx); err != nil || n != 1 {
		t.Fatal(n, err)
	}
}

func TestPurgeProcessed(t *testing.T) {
	pool, topo := setup(t)
	ctx := context.Background()
	_, _ = pool.Exec(ctx, `INSERT INTO processed_events (event_id, event_type, processed_at) VALUES
		($1, 'x', now() - interval '31 days'), ($2, 'x', now())`, uuid.New(), uuid.New())
	c := consumer("search-service", pool, topo)
	if n, err := c.PurgeProcessed(ctx); err != nil || n != 1 {
		t.Fatal(n, err)
	}
}

func TestRelayRunStopsOnCancel(t *testing.T) {
	pool, _ := setup(t)
	addEvents(t, pool, 3)
	pub := &failingPublisher{}
	r := eventbus.NewRelay(pool, pub)
	r.PollInterval = 20 * time.Millisecond
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- r.Run(ctx) }()
	waitFor(t, func() bool {
		pub.mu.Lock()
		defer pub.mu.Unlock()
		return len(pub.published) == 3
	})
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("relay did not stop")
	}
}

func TestRunForeverReconnectsUntilCancelled(t *testing.T) {
	pool, topo := setup(t)
	c := consumer("search-service", pool, topo)
	c.URL = "amqp://nobody:nothing@127.0.0.1:1/" // broker yo'q — Run darhol yiqiladi, RunForever kutadi
	c.MustOn(events.ScheduleAvailabilityChanged, func(context.Context, pgx.Tx, eventbus.Envelope) error { return nil })
	ctx, cancel := context.WithTimeout(context.Background(), 1500*time.Millisecond)
	defer cancel()
	started := time.Now()
	c.RunForever(ctx)
	if time.Since(started) < time.Second {
		t.Fatal("RunForever should keep retrying until ctx done")
	}
}

func TestConsumerWithoutHandlersFails(t *testing.T) {
	pool, topo := setup(t)
	if err := consumer("search-service", pool, topo).Run(context.Background()); err == nil {
		t.Fatal("expected error without handlers")
	}
	c := consumer("search-service", pool, topo)
	if err := c.On(events.UserRegistered, nil); err == nil {
		t.Fatal("search-service is not a consumer of user.registered")
	}
	if err := c.On("rating.new", nil); err == nil {
		t.Fatal("unknown event must fail")
	}
}

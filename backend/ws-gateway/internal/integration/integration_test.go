//go:build integration

// Integratsiya: haqiqiy Redis 7 + RabbitMQ 3.13 (testcontainers-go). To'liq oqim — eventbus'ga event
// → ws consumer (Redis dedup, retry/DLQ) → Redis `rtws:*` → IKKI API nusxasi → WebSocket klientlari.
//
//	scripts/go.sh backend/ws-gateway go test -tags integration -count=1 -coverpkg=./internal/... ./...
package integration_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"
	amqp "github.com/rabbitmq/amqp091-go"
	"github.com/redis/go-redis/v9"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
	gcbus "github.com/Muhiddin1408/nurse_new/libs/go-common/eventbus"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/events"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpclient"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/testutil"

	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/clients"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/config"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/eventbus"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/httpapi"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/realtime"
)

var (
	redisURL string
	amqpURL  string
)

func TestMain(m *testing.M) {
	ctx := context.Background()
	rc, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image: "redis:7.4-alpine", ExposedPorts: []string{"6379/tcp"}, WaitingFor: wait.ForLog("Ready to accept connections"),
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
	h, _ := rc.Host(ctx)
	p, _ := rc.MappedPort(ctx, "6379")
	redisURL = fmt.Sprintf("redis://%s:%s/0", h, p.Port())
	h, _ = rmq.Host(ctx)
	p, _ = rmq.MappedPort(ctx, "5672")
	amqpURL = fmt.Sprintf("amqp://test:test@%s:%s/", h, p.Port())
	code := m.Run()
	_ = rc.Terminate(ctx)
	_ = rmq.Terminate(ctx)
	if code != 0 {
		panic(fmt.Sprintf("exit %d", code))
	}
}

func rdb(t *testing.T) *redis.Client {
	t.Helper()
	opts, _ := redis.ParseURL(redisURL)
	c := redis.NewClient(opts)
	t.Cleanup(func() { _ = c.Close() })
	return c
}

type allowAll struct{}

func (allowAll) CanWatch(context.Context, uuid.UUID, uuid.UUID, *uuid.UUID) (bool, error) {
	return true, nil
}

// instance — bitta API nusxasi: o'z Hub'i, Redis listener'i va HTTP serveri.
func instance(t *testing.T, ctx context.Context, keys testutil.KeyPair) *httptest.Server {
	t.Helper()
	cfg := config.Config{MaxConnsPerUser: 5, MaxSubscriptions: 10, SendBuffer: 32, MaxMessageBytes: 4096,
		WriteTimeout: 2 * time.Second, PongWait: 10 * time.Second}
	cfg.RedisURL, cfg.RabbitMQURL = redisURL, amqpURL
	uk, _ := auth.ParsePublicKeys(keys.PublicPEM)
	hub := realtime.NewHub(cfg.MaxConnsPerUser)
	bus := &realtime.RedisBus{Redis: rdb(t)}
	a := &app.App{Config: cfg, Hub: hub, Bus: bus, Redis: bus.Redis, Booking: allowAll{},
		Auth: &auth.Authenticator{User: auth.NewVerifier(uk, "medbron-auth", "medbron")}}
	ready := make(chan struct{})
	go func() { _ = bus.Listen(ctx, hub, ready) }()
	<-ready
	srv := httptest.NewServer(httpapi.Routes(a))
	t.Cleanup(srv.Close)
	return srv
}

func dial(t *testing.T, srv *httptest.Server, token string) *websocket.Conn {
	t.Helper()
	c, resp, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(srv.URL, "http")+"/ws?token="+token, nil)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	t.Cleanup(func() { _ = c.Close() })
	return c
}

func readType(t *testing.T, c *websocket.Conn) string {
	t.Helper()
	_ = c.SetReadDeadline(time.Now().Add(10 * time.Second))
	var m map[string]any
	if err := c.ReadJSON(&m); err != nil {
		t.Fatal(err)
	}
	return fmt.Sprint(m["type"])
}

func publish(t *testing.T, env gcbus.Envelope) {
	t.Helper()
	body, _ := json.Marshal(env)
	pub := gcbus.NewPublisher(amqpURL, gcbus.Default)
	defer pub.Close()
	if err := pub.Publish(context.Background(), body, ""); err != nil {
		t.Fatal(err)
	}
}

func envelope(t *testing.T, eventType, producer, aggType string, data map[string]any) gcbus.Envelope {
	t.Helper()
	env, err := gcbus.NewEnvelope(eventType, 1, producer, aggType, uuid.NewString(), data, "")
	if err != nil {
		t.Fatal(err)
	}
	return env
}

func TestEventToWebSocketAcrossInstancesWithDedupAndDLQ(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	keys := testutil.NewKeyPair(t)
	user, booking := uuid.New(), uuid.New()
	token := keys.Sign(t, testutil.UserClaims(user))
	// Bir foydalanuvchi ikki qurilmada, har biri boshqa nusxada (load balancer)
	a, b := dial(t, instance(t, ctx, keys), token), dial(t, instance(t, ctx, keys), token)

	c := eventbus.NewConsumer("ws-gateway", amqpURL, rdb(t), time.Hour)
	c.Prefetch = 1 // tartib: payment.captured → booking.confirmed
	eventbus.Register(c, &realtime.RedisBus{Redis: rdb(t)})
	declareQueue(t, c.RoutingKeys()) // binding'lar publish'dan OLDIN (consumer ham xuddi shunday e'lon qiladi)
	go c.RunForever(ctx)

	captured := envelope(t, events.PaymentCaptured, "payment-service", "payment", map[string]any{
		"payment_id": uuid.NewString(), "booking_id": booking.String(), "client_id": user.String(),
		"provider": "payme", "amount_tiyin": 30_000_000, "captured_at": time.Now().UTC().Format(time.RFC3339),
	})
	publish(t, captured)
	publish(t, captured) // at-least-once: takror — klientga ikkinchi marta chiqmaydi
	confirmed := envelope(t, events.BookingConfirmed, "booking-service", "booking", map[string]any{
		"booking_id": booking.String(), "number": "MB-261021-AAAA", "client_id": user.String(),
		"patient_id": uuid.NewString(), "doctor_id": uuid.NewString(), "clinic_id": nil, "slot_id": uuid.NewString(),
		"place": "home", "start_at": "2026-10-25T05:00:00Z", "end_at": "2026-10-25T06:00:00Z",
		"payment_mode": "prepaid", "payment_id": uuid.NewString(), "confirmed_at": "2026-10-21T05:00:00Z",
		"promo_code": "", "money": map[string]any{"total_tiyin": 30_000_000, "platform_fee_tiyin": 4_500_000,
			"provider_fee_tiyin": 300_000, "doctor_payout_tiyin": 25_200_000, "discount_tiyin": 0, "discount_borne_by": ""},
	})
	publish(t, confirmed)

	for _, conn := range []*websocket.Conn{a, b} {
		if got := []string{readType(t, conn), readType(t, conn)}; got[0] != "payment.captured" || got[1] != "booking.confirmed" {
			t.Fatal(got)
		}
	}
	// Takror kelmadi: keyingi xabar — faqat kick
	publish(t, envelope(t, events.UserDeactivated, "auth-service", "user", map[string]any{
		"user_id": user.String(), "reason": "blocked_by_admin",
	}))
	if typ := readType(t, a); typ != "user.deactivated" {
		t.Fatal("takror yoki tartib buzildi:", typ)
	}

	// Sxemaga mos kelmaydigan event — DLQ (retry'siz)
	bad := envelope(t, events.PaymentFailed, "payment-service", "payment", map[string]any{"booking_id": "x"})
	publish(t, bad)
	waitDLQ(t, 1)
}

func declareQueue(t *testing.T, keys []string) {
	t.Helper()
	conn, err := amqp.Dial(amqpURL)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = conn.Close() }()
	ch, err := conn.Channel()
	if err != nil {
		t.Fatal(err)
	}
	if err := gcbus.Default.Declare(ch); err != nil {
		t.Fatal(err)
	}
	if err := gcbus.Default.DeclareServiceQueue(ch, "ws-gateway.events", keys); err != nil {
		t.Fatal(err)
	}
}

func waitDLQ(t *testing.T, want int) {
	t.Helper()
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) {
		conn, err := amqp.Dial(amqpURL)
		if err == nil {
			ch, _ := conn.Channel()
			q, err := ch.QueueDeclarePassive(gcbus.Default.DLQ, true, false, false, false, amqp.Table{"x-queue-type": "quorum"})
			_ = conn.Close()
			if err == nil && q.Messages >= want {
				return
			}
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatal("DLQ'ga tushmadi")
}

func TestConsumerRetriesWhenRedisPublishFails(t *testing.T) {
	r := rdb(t)
	var attempts atomic.Int32
	c := eventbus.NewConsumer("ws-gateway", amqpURL, r, time.Hour)
	h := func(context.Context, gcbus.Envelope) error {
		if attempts.Add(1) == 1 {
			return fmt.Errorf("redis down")
		}
		return nil
	}
	env := envelope(t, events.PaymentFailed, "payment-service", "payment", map[string]any{})
	if fresh, err := c.Handle(context.Background(), h, env); err == nil || fresh {
		t.Fatal("birinchi urinish xato bo'lishi kerak")
	}
	// Belgi o'chirilgan — retry'da qayta ishlanadi; uchinchisi — takror
	if fresh, err := c.Handle(context.Background(), h, env); err != nil || !fresh {
		t.Fatal(fresh, err)
	}
	if fresh, err := c.Handle(context.Background(), h, env); err != nil || fresh {
		t.Fatal("takror", fresh, err)
	}
}

func TestBookingAuthzIsCached(t *testing.T) {
	var calls atomic.Int32
	user, booking := uuid.New(), uuid.New()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get("X-Caller") != "ws-gateway" || r.URL.Query().Get("user_id") != user.String() {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = fmt.Fprintf(w, `{"allowed": %t}`, strings.Contains(r.URL.Path, booking.String()))
	}))
	defer srv.Close()
	b := &clients.Booking{HTTP: httpclient.New("booking-service", srv.URL, "ws-gateway", "tok"), Redis: rdb(t), TTL: time.Minute}
	for range 3 {
		if ok, err := b.CanWatch(context.Background(), booking, user, nil); err != nil || !ok {
			t.Fatal(ok, err)
		}
	}
	doctor := uuid.New()
	if ok, _ := b.CanWatch(context.Background(), uuid.New(), user, &doctor); ok {
		t.Fatal("begona bron")
	}
	if calls.Load() != 2 {
		t.Fatal("kesh ishlamadi:", calls.Load())
	}
}

func TestReadinessChecksAndAppWiring(t *testing.T) {
	cfg := config.Config{MaxConnsPerUser: 1, SendBuffer: 1, PongWait: time.Second}
	cfg.RedisURL, cfg.RabbitMQURL, cfg.ServiceName = redisURL, amqpURL, "ws-gateway"
	cfg.JWTPublicKeyPath = "/nonexistent.pem"
	a, err := app.New(context.Background(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = a.Close() }()
	for name, check := range a.ReadinessChecks() {
		if err := check(context.Background()); err != nil {
			t.Fatal(name, err)
		}
	}
	if _, ok := a.Bus.(*realtime.RedisBus); !ok || a.Auth.User != nil {
		t.Fatal("wiring")
	}
}

package httpapi_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/gorilla/websocket"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/testutil"

	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/config"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/domain"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/httpapi"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/realtime"
)

// fakeBooking — booking `/authz` soxtasi: ruxsat etilgan (bron, user) juftliklari.
type fakeBooking struct {
	mu      sync.Mutex
	allowed map[[2]uuid.UUID]bool
	fail    bool
	calls   int
}

func (f *fakeBooking) allow(bookingID, userID uuid.UUID) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.allowed[[2]uuid.UUID{bookingID, userID}] = true
}

func (f *fakeBooking) setFail(v bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.fail = v
}

func (f *fakeBooking) CanWatch(_ context.Context, bookingID, userID uuid.UUID, _ *uuid.UUID) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	if f.fail {
		return false, errors.New("booking down")
	}
	return f.allowed[[2]uuid.UUID{bookingID, userID}], nil
}

type env struct {
	srv     *httptest.Server
	app     *app.App
	user    testutil.KeyPair
	admin   testutil.KeyPair
	booking *fakeBooking
}

func setup(t *testing.T, mutate ...func(*config.Config)) *env {
	t.Helper()
	cfg := config.Config{
		AllowedOrigins: "https://app.medbron.uz", MaxConnsPerUser: 2, MaxSubscriptions: 2, SendBuffer: 8,
		MaxMessageBytes: 4096, WriteTimeout: 2 * time.Second, PongWait: 5 * time.Second,
	}
	for _, m := range mutate {
		m(&cfg)
	}
	user, admin := testutil.NewKeyPair(t), testutil.NewKeyPair(t)
	uk, _ := auth.ParsePublicKeys(user.PublicPEM)
	hub := realtime.NewHub(cfg.MaxConnsPerUser)
	fb := &fakeBooking{allowed: map[[2]uuid.UUID]bool{}}
	a := &app.App{
		Config: cfg, Hub: hub, Bus: &realtime.LocalBus{Hub: hub}, Booking: fb,
		Auth: &auth.Authenticator{User: auth.NewVerifier(uk, "medbron-auth", "medbron")},
	}
	srv := httptest.NewServer(httpapi.Routes(a))
	t.Cleanup(srv.Close)
	return &env{srv: srv, app: a, user: user, admin: admin, booking: fb}
}

func (e *env) url(token string) string {
	return "ws" + strings.TrimPrefix(e.srv.URL, "http") + "/ws?token=" + token
}

func (e *env) dial(t *testing.T, claims jwt.MapClaims, hdr http.Header) (*websocket.Conn, *http.Response, error) {
	t.Helper()
	conn, resp, err := websocket.DefaultDialer.Dial(e.url(e.user.Sign(t, claims)), hdr)
	if conn != nil {
		t.Cleanup(func() { _ = conn.Close() })
	}
	if resp != nil && resp.Body != nil {
		_ = resp.Body.Close()
	}
	return conn, resp, err
}

func (e *env) connect(t *testing.T, claims jwt.MapClaims) *websocket.Conn {
	t.Helper()
	conn, _, err := e.dial(t, claims, nil)
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, func() bool { u, _ := e.app.Hub.Stats(); return u > 0 })
	return conn
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatal("kutilgan holat bo'lmadi")
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func read(t *testing.T, c *websocket.Conn) map[string]any {
	t.Helper()
	_ = c.SetReadDeadline(time.Now().Add(3 * time.Second))
	var m map[string]any
	if err := c.ReadJSON(&m); err != nil {
		t.Fatal(err)
	}
	return m
}

func send(t *testing.T, c *websocket.Conn, action, channel string) map[string]any {
	t.Helper()
	if err := c.WriteJSON(map[string]string{"action": action, "channel": channel}); err != nil {
		t.Fatal(err)
	}
	return read(t, c)
}

func publish(t *testing.T, e *env, eventType string, data map[string]any) {
	t.Helper()
	raw, _ := json.Marshal(data)
	d, err := domain.Route(eventType, time.Now(), raw)
	if err != nil {
		t.Fatal(err)
	}
	if err := e.app.Bus.Publish(context.Background(), d); err != nil {
		t.Fatal(err)
	}
}

func closeCode(t *testing.T, c *websocket.Conn) int {
	t.Helper()
	_ = c.SetReadDeadline(time.Now().Add(3 * time.Second))
	for {
		if _, _, err := c.ReadMessage(); err != nil {
			var ce *websocket.CloseError
			if errors.As(err, &ce) {
				return ce.Code
			}
			t.Fatal("close kadri o'rniga:", err)
		}
	}
}

func TestHealthMetricsAndClosedInternal(t *testing.T) {
	e := setup(t)
	for _, p := range []string{"/health/live", "/health/ready", "/metrics"} {
		resp, err := http.Get(e.srv.URL + p) //nolint:noctx // test
		if err != nil || resp.StatusCode != 200 {
			t.Fatal(p, err)
		}
		_ = resp.Body.Close()
	}
	resp, _ := http.Get(e.srv.URL + "/internal/v1/anything") //nolint:noctx // test
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatal(resp.StatusCode)
	}
}

func TestHandshakeRejections(t *testing.T) {
	e := setup(t)
	uid := uuid.New()
	cases := []struct {
		name string
		url  string
		hdr  http.Header
		code int
	}{
		{"tokensiz", e.url(""), nil, 401},
		{"buzuq token", e.url("x.y.z"), nil, 401},
		{"admin token", e.url(e.admin.Sign(t, testutil.AdminClaims(uuid.New(), "*"))), nil, 403},
		{"begona imzo", e.url(testutil.NewKeyPair(t).Sign(t, testutil.UserClaims(uid))), nil, 401},
		{"begona origin", e.url(e.user.Sign(t, testutil.UserClaims(uid))), http.Header{"Origin": {"https://evil.example"}}, 403},
	}
	for _, tc := range cases {
		_, resp, err := websocket.DefaultDialer.Dial(tc.url, tc.hdr)
		if err == nil || resp == nil || resp.StatusCode != tc.code {
			t.Fatalf("%s: %v %v", tc.name, resp, err)
		}
		_ = resp.Body.Close()
	}
	expired := testutil.UserClaims(uid)
	expired["exp"] = time.Now().Add(-time.Minute).Unix()
	if _, resp, err := e.dial(t, expired, nil); err == nil || resp.StatusCode != 401 {
		t.Fatal("muddati o'tgan token", err)
	}
	// Ruxsat etilgan origin va Authorization header bilan — ulanadi
	hdr := http.Header{"Origin": {"https://app.medbron.uz"}, "Authorization": {"Bearer " + e.user.Sign(t, testutil.UserClaims(uid))}}
	conn, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(e.srv.URL, "http")+"/ws", hdr)
	if err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
}

func TestUserChannelBookingSubscriptionAndKick(t *testing.T) {
	e := setup(t)
	uid, booking, other := uuid.New(), uuid.New(), uuid.New()
	e.booking.allow(booking, uid)
	c := e.connect(t, testutil.UserClaims(uid))

	// Avtomatik user kanali: payment.captured ("To'lov tekshirilmoqda") va booking.confirmed
	publish(t, e, "payment.captured", map[string]any{"payment_id": uuid.New(), "booking_id": booking, "client_id": uid,
		"provider": "payme", "amount_tiyin": 100, "captured_at": "2026-10-21T05:00:00Z"})
	if m := read(t, c); m["type"] != "payment.captured" || m["data"].(map[string]any)["amount_tiyin"] != float64(100) {
		t.Fatal(m)
	}
	// Begona foydalanuvchining eventi kelmaydi
	publish(t, e, "payment.failed", map[string]any{"payment_id": uuid.New(), "booking_id": other, "client_id": uuid.New(),
		"provider": "click", "reason": "x"})

	if r := send(t, c, "subscribe", "booking:"+booking.String()); r["type"] != "subscribed" {
		t.Fatal(r)
	}
	if r := send(t, c, "subscribe", "booking:"+other.String()); r["code"] != "FORBIDDEN" {
		t.Fatal(r)
	}
	if r := send(t, c, "subscribe", "user:"+uuid.NewString()); r["code"] != "FORBIDDEN" {
		t.Fatal(r)
	}
	if r := send(t, c, "subscribe", "doctor:"+uuid.NewString()+":schedule"); r["code"] != "FORBIDDEN" {
		t.Fatal("shifokor emas", r)
	}
	if r := send(t, c, "subscribe", "rtws:*"); r["code"] != "BAD_CHANNEL" {
		t.Fatal(r)
	}
	if r := send(t, c, "dance", ""); r["code"] != "BAD_COMMAND" {
		t.Fatal(r)
	}
	if r := send(t, c, "ping", ""); r["type"] != "pong" {
		t.Fatal(r)
	}
	e.booking.setFail(true)
	if r := send(t, c, "subscribe", "booking:"+uuid.NewString()); r["code"] != "UNAVAILABLE" {
		t.Fatal(r)
	}
	e.booking.setFail(false)

	// booking.confirmed — user va booking kanallariga; klient (ikkalasiga obuna) ikki nusxa oladi
	publish(t, e, "booking.confirmed", map[string]any{"booking_id": booking, "client_id": uid, "number": "MB-7",
		"doctor_id": uuid.New(), "patient_id": uuid.New()})
	for range 2 {
		if m := read(t, c); m["type"] != "booking.confirmed" || m["data"].(map[string]any)["patient_id"] != nil {
			t.Fatal(m)
		}
	}
	if r := send(t, c, "unsubscribe", "booking:"+booking.String()); r["type"] != "unsubscribed" {
		t.Fatal(r)
	}

	// Akkaunt bloklandi — xabar yetadi, keyin 4003
	publish(t, e, domain.TypeKick, map[string]any{"user_id": uid, "reason": "blocked"})
	if m := read(t, c); m["type"] != domain.TypeKick {
		t.Fatal(m)
	}
	if code := closeCode(t, c); code != realtime.CloseDeactivated {
		t.Fatal(code)
	}
	waitFor(t, func() bool { u, ch := e.app.Hub.Stats(); return u == 0 && ch == 0 })
}

func TestDoctorScheduleSubscriptionAndLimits(t *testing.T) {
	e := setup(t)
	uid, doctor := uuid.New(), uuid.New()
	claims := testutil.UserClaims(uid, "doctor")
	claims["doctor_id"] = doctor.String()
	c := e.connect(t, claims)
	if r := send(t, c, "subscribe", "doctor:"+doctor.String()+":schedule"); r["type"] != "subscribed" {
		t.Fatal(r)
	}
	publish(t, e, "schedule.availability_changed", map[string]any{"doctor_id": doctor, "dates": []string{"2026-10-22"},
		"next_available_at": nil})
	if m := read(t, c); m["type"] != "schedule.availability_changed" {
		t.Fatal(m)
	}
	// Obunalar limiti (2): schedule + yana bitta, uchinchisi rad
	for i, b := range []uuid.UUID{uuid.New(), uuid.New()} {
		e.booking.allow(b, uid)
		r := send(t, c, "subscribe", "booking:"+b.String())
		if want := map[int]string{0: "subscribed", 1: "error"}[i]; r["type"] != want {
			t.Fatal(i, r)
		}
	}
	// Ulanishlar limiti (2): ikkinchisi ulanadi, uchinchisi — 429
	e.connect(t, claims)
	if _, resp, err := e.dial(t, claims, nil); err == nil || resp.StatusCode != http.StatusTooManyRequests {
		t.Fatal("limit", err)
	}
}

func TestTokenExpiryClosesConnection(t *testing.T) {
	e := setup(t)
	claims := testutil.UserClaims(uuid.New())
	claims["exp"] = time.Now().Add(1500 * time.Millisecond).Unix()
	c := e.connect(t, claims)
	if code := closeCode(t, c); code != realtime.CloseTokenExpired {
		t.Fatal(code)
	}
}

func TestSlowConsumerIsDisconnected(t *testing.T) {
	e := setup(t, func(c *config.Config) { c.SendBuffer = 1 })
	uid := uuid.New()
	c := e.connect(t, testutil.UserClaims(uid))
	// Klient o'qimaydi — OS buferlari (~MB) to'lgach navbat ham to'ladi; tarqatish (publish) bloklanmaydi,
	// sekin klient uziladi
	big := strings.Repeat("x", 64*1024)
	for i := 0; i < 2000; i++ {
		if u, _ := e.app.Hub.Stats(); u == 0 {
			break
		}
		publish(t, e, "payment.failed", map[string]any{"payment_id": uuid.New(), "booking_id": uuid.New(),
			"client_id": uid, "provider": "payme", "reason": big})
	}
	waitFor(t, func() bool { u, _ := e.app.Hub.Stats(); return u == 0 })
	_ = c.Close()
}

func TestGracefulShutdownSendsGoingAway(t *testing.T) {
	e := setup(t)
	c := e.connect(t, testutil.UserClaims(uuid.New()))
	e.app.Hub.CloseAll()
	if code := closeCode(t, c); code != websocket.CloseGoingAway {
		t.Fatal(code)
	}
}

func TestAuthNotConfigured(t *testing.T) {
	e := setup(t)
	e.app.Auth.User = nil
	resp, err := http.Get(e.srv.URL + "/ws?token=x") //nolint:noctx // test
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatal(resp.StatusCode)
	}
}

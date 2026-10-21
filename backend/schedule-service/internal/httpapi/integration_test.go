//go:build integration

// Integratsiya testlari: haqiqiy Postgres 16 + Redis 7 (testcontainers-go), catalog/admin — httptest soxtalari.
// To'liq HTTP stek (marshrutlar → handlerlar → servis → store) va event handlerlar.
//
//	scripts/go.sh backend/schedule-service go test -tags integration -coverpkg=./internal/... ./...
package httpapi_test

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
	gcbus "github.com/Muhiddin1408/nurse_new/libs/go-common/eventbus"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/events"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpclient"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/testutil"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/cache"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/clients"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/domain"
	schedbus "github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/eventbus"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/httpapi"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/service"
)

var (
	pgURL    string
	redisURL string
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
	rc, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image:        "redis:7-alpine",
			ExposedPorts: []string{"6379/tcp"},
			WaitingFor:   wait.ForLog("Ready to accept connections"),
		},
		Started: true,
	})
	if err != nil {
		panic(err)
	}
	host, _ := pg.Host(ctx)
	port, _ := pg.MappedPort(ctx, "5432")
	pgURL = fmt.Sprintf("postgres://test:test@%s:%s/test", host, port.Port())
	rHost, _ := rc.Host(ctx)
	rPort, _ := rc.MappedPort(ctx, "6379")
	redisURL = fmt.Sprintf("redis://%s:%s/0", rHost, rPort.Port())

	pool, err := pgxpool.New(ctx, pgURL)
	if err != nil {
		panic(err)
	}
	files, _ := filepath.Glob("../../migrations/*.up.sql")
	sort.Strings(files)
	for _, f := range files {
		sql, err := os.ReadFile(f)
		if err != nil {
			panic(err)
		}
		if _, err := pool.Exec(ctx, string(sql)); err != nil {
			panic(fmt.Sprintf("%s: %v", f, err))
		}
	}
	pool.Close()

	code := m.Run()
	_ = pg.Terminate(ctx)
	_ = rc.Terminate(ctx)
	os.Exit(code)
}

// --- muhit ------------------------------------------------------------------------------------------

type fakeCatalog struct {
	mu          sync.Mutex
	bookability map[uuid.UUID]clients.Bookability
	admins      map[string]bool // user_id:clinic_id
	rooms       map[uuid.UUID]clients.Room
	accessCalls int
}

func (f *fakeCatalog) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
	notFound := func() {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":{"code":"NOT_FOUND","message":"x"}}`))
	}
	switch {
	case r.URL.Path == "/internal/v1/access":
		f.accessCalls++
		key := r.URL.Query().Get("user_id") + ":" + r.URL.Query().Get("clinic_id")
		role := "clinic_admin"
		_ = json.NewEncoder(w).Encode(clients.Access{Allowed: f.admins[key], Role: &role, ClinicIDs: []uuid.UUID{}})
	case len(parts) == 4 && parts[2] == "rooms":
		room, ok := f.rooms[uuid.MustParse(parts[3])]
		if !ok {
			notFound()
			return
		}
		_ = json.NewEncoder(w).Encode(room)
	case len(parts) == 5 && parts[2] == "doctors" && parts[4] == "bookability":
		b, ok := f.bookability[uuid.MustParse(parts[3])]
		if !ok {
			notFound()
			return
		}
		_ = json.NewEncoder(w).Encode(b)
	default:
		notFound()
	}
}

type env struct {
	t        *testing.T
	ctx      context.Context
	h        http.Handler
	pool     *pgxpool.Pool
	rdb      *redis.Client
	svc      *service.Service
	keys     testutil.KeyPair
	cat      *fakeCatalog
	doctor   uuid.UUID // tasdiqlangan shifokor (seed)
	userID   uuid.UUID // shifokorning user ID si
	clinic   uuid.UUID
	aff      uuid.UUID
	day      domain.Date  // bugundan +2 kun — vaqt chegaralaridan uzoq
	settings atomic.Int32 // admin-service chaqiruvlari
}

const adminSettings = `{"version":3,"values":{"schedule":{"hold_ttl_minutes":10,"min_lead_time_minutes":60,"generation_days_ahead":7}}}`

func newEnv(t *testing.T) *env {
	t.Helper()
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, pgURL)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	if _, err := pool.Exec(ctx, `TRUNCATE doctors, clinics, affiliations, working_rules, schedule_exceptions, clinic_closures,
		time_offs, price_rules, slots, outbox_events, processed_events CASCADE`); err != nil {
		t.Fatal(err)
	}
	opts, _ := redis.ParseURL(redisURL)
	rdb := redis.NewClient(opts)
	t.Cleanup(func() { _ = rdb.Close() })
	if err := rdb.FlushDB(ctx).Err(); err != nil {
		t.Fatal(err)
	}

	e := &env{t: t, ctx: ctx, pool: pool, rdb: rdb, doctor: uuid.New(), userID: uuid.New(), clinic: uuid.New(), aff: uuid.New()}
	e.cat = &fakeCatalog{bookability: map[uuid.UUID]clients.Bookability{}, admins: map[string]bool{}, rooms: map[uuid.UUID]clients.Room{}}
	catSrv := httptest.NewServer(e.cat)
	t.Cleanup(catSrv.Close)
	adminSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		e.settings.Add(1)
		if r.URL.Path != "/internal/v1/settings" || r.Header.Get("X-Caller") != "schedule-service" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(adminSettings))
	}))
	t.Cleanup(adminSrv.Close)

	settings := &clients.Settings{HTTP: httpclient.New("admin-service", adminSrv.URL, "schedule-service", "tok"), Redis: rdb}
	catalog := &clients.Catalog{HTTP: httpclient.New("catalog-service", catSrv.URL, "schedule-service", "tok"), Redis: rdb}
	e.svc = service.New(pool, &cache.Slots{Redis: rdb}, settings, catalog)
	e.keys = testutil.NewKeyPair(t)
	uk, err := auth.ParsePublicKeys(e.keys.PublicPEM)
	if err != nil {
		t.Fatal(err)
	}
	a := &app.App{DB: pool, Redis: rdb, Svc: e.svc, Auth: &auth.Authenticator{
		User:          auth.NewVerifier(uk, "medbron-auth", "medbron"),
		InboundTokens: map[string]string{"booking-service": "tok", "payment-service": "tok"},
	}}
	e.h = httpapi.Routes(a)
	e.day = domain.Today(time.Now()).AddDate(0, 0, 2)
	return e
}

// seed — tasdiqlangan shifokor, faol klinika, faol hamkorlik (eventlar orqali, xuddi prod'dagidek).
func (e *env) seed() {
	e.cat.bookability[e.doctor] = clients.Bookability{DoctorID: e.doctor, Status: "approved", Bookable: true,
		AcceptsHomeVisits: true, ClinicIDs: []uuid.UUID{e.clinic}}
	e.deliver(events.DoctorApproved, map[string]any{"doctor_id": e.doctor, "user_id": e.userID, "accepts_home_visits": true})
	e.deliver(events.ClinicPublished, map[string]any{"clinic_id": e.clinic})
	e.deliver(events.AffiliationAccepted, map[string]any{"affiliation_id": e.aff, "clinic_id": e.clinic, "doctor_id": e.doctor})
}

// rules — e.day hafta kunida: uy 09:00–12:00 (30 daq, 6 slot), klinika 14:00–16:00 (60 daq, 2 slot).
func (e *env) rules() (home, clinic map[string]any) {
	wd := domain.Weekday(e.day)
	today := domain.Today(time.Now()).Format(time.DateOnly)
	code, home := e.call(http.MethodPost, "/v1/doctor/schedule/rules", map[string]any{"weekday": wd, "start_time": "09:00",
		"end_time": "12:00", "slot_minutes": 30, "valid_from": today}, e.doctorAuth())
	if code != 201 || num(home["result"], "created") != 6 {
		e.t.Fatal("home rule", code, home)
	}
	code, clinic = e.call(http.MethodPost, "/v1/doctor/schedule/rules", map[string]any{"clinic_id": e.clinic, "weekday": wd,
		"start_time": "14:00", "end_time": "16:00", "slot_minutes": 60, "valid_from": today}, e.doctorAuth())
	if code != 201 || num(clinic["result"], "created") != 2 {
		e.t.Fatal("clinic rule", code, clinic)
	}
	return home, clinic
}

func (e *env) deliver(eventType string, data any) {
	e.t.Helper()
	if err := e.tryDeliver(eventType, data, time.Now().UTC()); err != nil {
		e.t.Fatal(eventType, err)
	}
}

func (e *env) tryDeliver(eventType string, data any, at time.Time) error {
	raw, _ := json.Marshal(data)
	h := schedbus.Handler(e.svc, eventType)
	if h == nil {
		return fmt.Errorf("no handler for %s", eventType)
	}
	tx, err := e.pool.Begin(e.ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(e.ctx) }()
	if err := h(e.ctx, tx, gcbus.Envelope{EventID: uuid.New(), EventType: eventType, OccurredAt: at, Data: raw}); err != nil {
		return err
	}
	return tx.Commit(e.ctx)
}

func (e *env) userAuth(userID uuid.UUID) map[string]string {
	return map[string]string{"Authorization": "Bearer " + e.keys.Sign(e.t, testutil.UserClaims(userID))}
}

func (e *env) doctorAuth() map[string]string {
	c := testutil.UserClaims(e.userID, "doctor")
	c["doctor_id"] = e.doctor.String()
	return map[string]string{"Authorization": "Bearer " + e.keys.Sign(e.t, c)}
}

var internal = map[string]string{"X-Caller": "booking-service", "X-Internal-Token": "tok"}

func (e *env) do(method, path string, body any, hdr map[string]string) (int, []byte, http.Header) {
	e.t.Helper()
	var rd *bytes.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rd = bytes.NewReader(raw)
	} else {
		rd = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rd)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	e.h.ServeHTTP(rec, req)
	return rec.Code, rec.Body.Bytes(), rec.Header()
}

func (e *env) call(method, path string, body any, hdr map[string]string) (int, map[string]any) {
	e.t.Helper()
	code, raw, _ := e.do(method, path, body, hdr)
	var out map[string]any
	_ = json.Unmarshal(raw, &out)
	return code, out
}

func (e *env) list(method, path string, hdr map[string]string) (int, []map[string]any) {
	e.t.Helper()
	code, raw, _ := e.do(method, path, nil, hdr)
	var out []map[string]any
	_ = json.Unmarshal(raw, &out)
	return code, out
}

func num(m any, key string) int {
	v, _ := m.(map[string]any)[key].(float64)
	return int(v)
}

func detailCode(body map[string]any) string {
	d, _ := body["detail"].(map[string]any)
	c, _ := d["code"].(string)
	return c
}

// slotAt — shifokorning e.day dagi mahalliy daqiqadagi sloti.
func (e *env) slotAt(minute int) uuid.UUID {
	e.t.Helper()
	var id uuid.UUID
	if err := e.pool.QueryRow(e.ctx, `SELECT id FROM slots WHERE doctor_id = $1 AND start_at = $2`, e.doctor,
		domain.At(e.day, minute)).Scan(&id); err != nil {
		e.t.Fatalf("slot at %d: %v", minute, err)
	}
	return id
}

func (e *env) status(id uuid.UUID) (status, reason string) {
	e.t.Helper()
	if err := e.pool.QueryRow(e.ctx, `SELECT status, block_reason FROM slots WHERE id = $1`, id).Scan(&status, &reason); err != nil {
		e.t.Fatal(err)
	}
	return status, reason
}

func (e *env) expectStatus(id uuid.UUID, status, reason string) {
	e.t.Helper()
	if s, r := e.status(id); s != status || r != reason {
		e.t.Fatalf("slot %s: got %s/%q, want %s/%q", id, s, r, status, reason)
	}
}

type outboxEvent struct {
	Type string
	Data map[string]any
}

func (e *env) outbox(eventType string) []outboxEvent {
	e.t.Helper()
	rows, err := e.pool.Query(e.ctx, `SELECT event_type, payload FROM outbox_events WHERE event_type = $1 ORDER BY id`, eventType)
	if err != nil {
		e.t.Fatal(err)
	}
	defer rows.Close()
	var out []outboxEvent
	for rows.Next() {
		var t string
		var raw []byte
		if err := rows.Scan(&t, &raw); err != nil {
			e.t.Fatal(err)
		}
		var env struct {
			Data map[string]any `json:"data"`
		}
		_ = json.Unmarshal(raw, &env)
		out = append(out, outboxEvent{Type: t, Data: env.Data})
	}
	return out
}

func (e *env) publicDay() []map[string]any {
	e.t.Helper()
	code, out := e.list(http.MethodGet, fmt.Sprintf("/v1/schedule/doctors/%s/slots?date=%s", e.doctor, e.day.Format(time.DateOnly)), nil)
	if code != 200 {
		e.t.Fatal("public day", code)
	}
	return out
}

func (e *env) hold(id uuid.UUID, ref string, extra map[string]any) (int, map[string]any) {
	body := map[string]any{"hold_ref": ref, "doctor_id": e.doctor}
	for k, v := range extra {
		body[k] = v
	}
	return e.call(http.MethodPost, "/internal/v1/slots/"+id.String()+"/hold", body, internal)
}

// --- testlar ------------------------------------------------------------------------------------

func TestSlotLifecycle(t *testing.T) {
	e := newEnv(t)
	e.seed()
	e.rules()

	// Ommaviy kun ro'yxati: 8 slot, kesh sarlavhasi, Redis'ga yozildi
	code, raw, hdr := e.do(http.MethodGet, fmt.Sprintf("/v1/schedule/doctors/%s/slots?date=%s", e.doctor, e.day.Format(time.DateOnly)), nil, nil)
	var day []service.PublicSlot
	_ = json.Unmarshal(raw, &day)
	if code != 200 || len(day) != 8 || hdr.Get("Cache-Control") != "public, max-age=30" {
		t.Fatal(code, len(day), hdr)
	}
	if n, _ := e.rdb.Exists(e.ctx, cache.Key(e.doctor, e.day)).Result(); n != 1 {
		t.Fatal("day not cached")
	}
	if day[0].Place != "home" || day[7].Place != "clinic" || *day[7].ClinicID != e.clinic {
		t.Fatal(day)
	}
	// Oraliq
	from := domain.At(e.day, 0).Format(time.RFC3339)
	to := domain.At(e.day.AddDate(0, 0, 1), 0).Format(time.RFC3339)
	if code, out := e.list(http.MethodGet, "/v1/schedule/doctors/"+e.doctor.String()+"/slots?date_from="+from+"&date_to="+to, nil); code != 200 || len(out) != 8 {
		t.Fatal("range", code, len(out))
	}
	far := domain.At(e.day.AddDate(0, 0, 40), 0).Format(time.RFC3339)
	if code, body := e.call(http.MethodGet, "/v1/schedule/doctors/"+e.doctor.String()+"/slots?date_from="+from+"&date_to="+far, nil, nil); code != 400 || detailCode(body) != "INVALID_RANGE" {
		t.Fatal("range > 31", code, body)
	}
	if code, _ := e.call(http.MethodGet, "/v1/schedule/doctors/"+e.doctor.String()+"/slots?date_from="+from, nil, nil); code != 400 {
		t.Fatal("half range", code)
	}
	if code, _ := e.call(http.MethodGet, "/v1/schedule/doctors/"+e.doctor.String()+"/slots?date=bad", nil, nil); code != 400 {
		t.Fatal("bad date", code)
	}

	// Internal free-slots
	q := fmt.Sprintf("/internal/v1/doctors/%s/free-slots?from=%s&to=%s", e.doctor, from, to)
	if code, out := e.call(http.MethodGet, q+"&place=home&limit=3", nil, internal); code != 200 || len(out["items"].([]any)) != 3 {
		t.Fatal("free-slots", code, out)
	}
	if _, out := e.call(http.MethodGet, q+"&clinic_id="+e.clinic.String()+"&min_minutes=60", nil, internal); len(out["items"].([]any)) != 2 {
		t.Fatal("free-slots clinic", out)
	}
	if code, _ := e.call(http.MethodGet, q+"&clinic_id=bad", nil, internal); code != 400 {
		t.Fatal("bad clinic_id", code)
	}

	// Hold → buferlar (09:30 va 10:30), boshqa ref — SLOT_TAKEN
	slot := e.slotAt(10 * 60)
	ref := uuid.NewString()
	code, held := e.hold(slot, ref, nil)
	if code != 200 || held["status"] != "held" || held["hold_ref"] != ref || num(held, "duration_minutes") != 30 || held["place"] != "home" {
		t.Fatal("hold", code, held)
	}
	e.expectStatus(e.slotAt(9*60+30), "blocked", "travel_buffer")
	e.expectStatus(e.slotAt(10*60+30), "blocked", "travel_buffer")
	e.expectStatus(e.slotAt(9*60), "free", "")
	e.expectStatus(e.slotAt(11*60), "free", "")
	if got := len(e.publicDay()); got != 5 {
		t.Fatal("public after hold (cache invalidated)", got)
	}
	if code, body := e.hold(slot, uuid.NewString(), nil); code != 409 || detailCode(body) != "SLOT_TAKEN" {
		t.Fatal("other ref", code, body)
	}
	if code, _ := e.hold(slot, ref, map[string]any{"ttl_seconds": 600}); code != 200 {
		t.Fatal("same ref idempotent", code)
	}
	other := map[string]any{"hold_ref": uuid.NewString(), "doctor_id": uuid.New()}
	if code, body := e.call(http.MethodPost, "/internal/v1/slots/"+e.slotAt(9*60).String()+"/hold", other, internal); code != 409 || detailCode(body) != "SLOT_TAKEN" {
		t.Fatal("other doctor", code, body)
	}
	if code, body := e.hold(uuid.New(), ref, nil); code != 409 || detailCode(body) != "SLOT_TAKEN" {
		t.Fatal("missing slot", code, body)
	}
	if code, _ := e.hold(slot, ref, map[string]any{"ttl_seconds": 5}); code != 400 {
		t.Fatal("ttl", code)
	}
	if code, _ := e.hold(slot, ref, map[string]any{"travel_buffer_minutes": 200}); code != 400 {
		t.Fatal("buffer", code)
	}
	if code, _ := e.hold(slot, "", nil); code != 400 {
		t.Fatal("empty ref", code)
	}

	// Extend, confirm
	path := "/internal/v1/slots/" + slot.String()
	if code, out := e.call(http.MethodPost, path+"/extend", map[string]any{"hold_ref": ref, "minutes": 30}, internal); code != 200 || out["hold_expires_at"] == nil {
		t.Fatal("extend", code, out)
	}
	if code, _ := e.call(http.MethodPost, path+"/extend", map[string]any{"hold_ref": ref, "minutes": 61}, internal); code != 400 {
		t.Fatal("extend > 60", code)
	}
	if code, body := e.call(http.MethodPost, path+"/confirm", map[string]any{"hold_ref": "other"}, internal); code != 409 || detailCode(body) != "HOLD_LOST" {
		t.Fatal("confirm other", code, body)
	}
	if code, out := e.call(http.MethodPost, path+"/confirm", map[string]any{"hold_ref": ref}, internal); code != 200 || out["status"] != "booked" || out["hold_expires_at"] != nil {
		t.Fatal("confirm", code, out)
	}
	if code, out := e.call(http.MethodPost, path+"/confirm", map[string]any{"hold_ref": ref}, internal); code != 200 || out["status"] != "booked" {
		t.Fatal("confirm idempotent", code, out)
	}
	if code, out := e.hold(slot, ref, nil); code != 200 || out["status"] != "booked" {
		t.Fatal("hold after confirm (retry)", code, out)
	}
	if code, body := e.call(http.MethodPost, path+"/extend", map[string]any{"hold_ref": ref, "minutes": 5}, internal); code != 409 || detailCode(body) != "HOLD_LOST" {
		t.Fatal("extend booked", code, body)
	}
	if code, out := e.call(http.MethodGet, path, nil, internal); code != 200 || out["status"] != "booked" || out["price_adjust_percent"].(float64) != 0 {
		t.Fatal("get", code, out)
	}
	if code, body := e.call(http.MethodGet, "/internal/v1/slots/"+uuid.NewString(), nil, internal); code != 404 || detailCode(body) != "SLOT_NOT_FOUND" {
		t.Fatal("get missing", code, body)
	}
	if code, _ := e.call(http.MethodPost, path+"/confirm", map[string]any{}, internal); code != 400 {
		t.Fatal("confirm without ref", code)
	}

	// Release: boshqa ref — o'zgarishsiz; o'zi — bo'sh, buferlar ochiladi, slot.released yuborilmaydi
	if code, _, _ := e.do(http.MethodPost, path+"/release", map[string]any{"hold_ref": "other"}, internal); code != 204 {
		t.Fatal(code)
	}
	e.expectStatus(slot, "booked", "")
	if code, _, _ := e.do(http.MethodPost, path+"/release", map[string]any{"hold_ref": ref}, internal); code != 204 {
		t.Fatal(code)
	}
	if code, _, _ := e.do(http.MethodPost, "/internal/v1/slots/bad/release", map[string]any{"hold_ref": ref}, internal); code != 204 {
		t.Fatal("release bad id", code)
	}
	e.expectStatus(slot, "free", "")
	e.expectStatus(e.slotAt(9*60+30), "free", "")
	if got := len(e.publicDay()); got != 8 {
		t.Fatal("public after release", got)
	}
	if n := len(e.outbox(events.SlotReleased)); n != 0 {
		t.Fatal("compensation must not publish slot.released", n)
	}
	av := e.outbox(events.ScheduleAvailabilityChanged)
	if len(av) == 0 || av[len(av)-1].Data["doctor_id"] != e.doctor.String() || av[len(av)-1].Data["next_available_at"] == nil {
		t.Fatal("availability events", av)
	}
}

func TestHoldConcurrency(t *testing.T) {
	e := newEnv(t)
	e.seed()
	e.rules()
	slot := e.slotAt(14 * 60)
	const n = 20
	var wg sync.WaitGroup
	codes := make([]int, n)
	for i := range n {
		wg.Add(1)
		go func() {
			defer wg.Done()
			codes[i], _ = e.hold(slot, uuid.NewString(), nil)
		}()
	}
	wg.Wait()
	won := 0
	for _, c := range codes {
		switch c {
		case 200:
			won++
		case 409:
		default:
			t.Fatal("unexpected", c)
		}
	}
	if won != 1 {
		t.Fatal("exactly one hold must win", won, codes)
	}
}

func TestTravelBufferOwnership(t *testing.T) {
	e := newEnv(t)
	e.seed()
	e.rules()
	a, c := e.slotAt(9*60), e.slotAt(11*60)
	refA, refC := uuid.NewString(), uuid.NewString()
	if code, _ := e.hold(a, refA, nil); code != 200 {
		t.Fatal(code)
	}
	if code, _ := e.hold(c, refC, map[string]any{"travel_buffer_minutes": 30}); code != 200 {
		t.Fatal(code)
	}
	e.expectStatus(e.slotAt(9*60+30), "blocked", "travel_buffer")
	e.expectStatus(e.slotAt(10*60+30), "blocked", "travel_buffer")
	e.expectStatus(e.slotAt(11*60+30), "blocked", "travel_buffer")
	e.expectStatus(e.slotAt(10*60), "free", "")
	// A bekor qilinadi — faqat A ning buferi ochiladi (ADR-0011 #4)
	e.deliver(events.BookingCancelled, map[string]any{"booking_id": refA, "slot_id": a})
	e.expectStatus(a, "free", "")
	e.expectStatus(e.slotAt(9*60+30), "free", "")
	e.expectStatus(e.slotAt(10*60+30), "blocked", "travel_buffer")
	rel := e.outbox(events.SlotReleased)
	if len(rel) != 1 || rel[0].Data["reason"] != "booking_cancelled" || rel[0].Data["hold_ref"] != refA || rel[0].Data["place"] != "home" {
		t.Fatal(rel)
	}
	// Takroriy event — o'zgarishsiz
	e.deliver(events.BookingCancelled, map[string]any{"booking_id": refA, "slot_id": a})
	if len(e.outbox(events.SlotReleased)) != 1 {
		t.Fatal("duplicate release")
	}
	// Klinika slotida bufer yo'q
	if code, _ := e.hold(e.slotAt(14*60), uuid.NewString(), map[string]any{"travel_buffer_minutes": 60}); code != 200 {
		t.Fatal(code)
	}
	e.expectStatus(e.slotAt(15*60), "free", "")
}

func TestHoldExpiry(t *testing.T) {
	e := newEnv(t)
	e.seed()
	e.rules()
	a, b := e.slotAt(9*60), e.slotAt(14*60)
	refA, refB := uuid.NewString(), uuid.NewString()
	for _, h := range []struct {
		id  uuid.UUID
		ref string
	}{{a, refA}, {b, refB}} {
		if code, _ := e.hold(h.id, h.ref, map[string]any{"ttl_seconds": 60}); code != 200 {
			t.Fatal(code)
		}
	}
	if _, err := e.pool.Exec(e.ctx, `UPDATE slots SET hold_expires_at = now() - interval '1 minute' WHERE status = 'held'`); err != nil {
		t.Fatal(err)
	}
	e.svc.Cache.Invalidate(e.ctx, e.doctor, e.day)
	// Lazy expiry: o'qishda bo'sh; GET da status free
	if got := len(e.publicDay()); got != 8-1 { // 09:30 hali bufer bilan yopiq
		t.Fatal("lazy public", got)
	}
	if _, out := e.call(http.MethodGet, "/internal/v1/slots/"+b.String(), nil, internal); out["status"] != "free" {
		t.Fatal("effective status", out)
	}
	// Boshqa bron muddati o'tgan hold'ni oladi — eski egaga slot.released(hold_expired)
	refB2 := uuid.NewString()
	if code, _ := e.hold(b, refB2, nil); code != 200 {
		t.Fatal(code)
	}
	if code, body := e.call(http.MethodPost, "/internal/v1/slots/"+b.String()+"/confirm", map[string]any{"hold_ref": refB}, internal); code != 409 || detailCode(body) != "HOLD_LOST" {
		t.Fatal("old ref lost", code, body)
	}
	// Job: faqat A (B qayta band) — CAS
	n, err := e.svc.ExpireHolds(e.ctx, 100)
	if err != nil || n != 1 {
		t.Fatal(n, err)
	}
	e.expectStatus(a, "free", "")
	e.expectStatus(e.slotAt(9*60+30), "free", "")
	e.expectStatus(b, "held", "")
	refs := map[string]string{}
	for _, ev := range e.outbox(events.SlotReleased) {
		refs[ev.Data["hold_ref"].(string)] = ev.Data["reason"].(string)
	}
	if len(refs) != 2 || refs[refA] != "hold_expired" || refs[refB] != "hold_expired" {
		t.Fatal(refs)
	}
	if n, _ := e.svc.ExpireHolds(e.ctx, 100); n != 0 {
		t.Fatal("second run", n)
	}
	// Uuid bo'lmagan hold_ref — event'da null
	c := e.slotAt(15 * 60)
	if code, _ := e.hold(c, "not-a-uuid", map[string]any{"ttl_seconds": 60}); code != 200 {
		t.Fatal(code)
	}
	_, _ = e.pool.Exec(e.ctx, `UPDATE slots SET hold_expires_at = now() - interval '1 second' WHERE id = $1`, c)
	if n, _ := e.svc.ExpireHolds(e.ctx, 100); n != 1 {
		t.Fatal(n)
	}
	rel := e.outbox(events.SlotReleased)
	if rel[len(rel)-1].Data["hold_ref"] != nil {
		t.Fatal("non-uuid ref", rel[len(rel)-1])
	}
}

func TestRuleChangesWithBookings(t *testing.T) {
	e := newEnv(t)
	e.seed()
	home, clinic := e.rules()
	clinicRule := clinic["rule"].(map[string]any)["id"].(string)
	homeRule := home["rule"].(map[string]any)["id"].(string)
	booked := e.slotAt(14 * 60)
	ref := uuid.NewString()
	e.hold(booked, ref, nil)
	e.call(http.MethodPost, "/internal/v1/slots/"+booked.String()+"/confirm", map[string]any{"hold_ref": ref}, internal)

	// Kesishuv
	if code, body := e.call(http.MethodPost, "/v1/doctor/schedule/rules", map[string]any{"weekday": domain.Weekday(e.day),
		"start_time": "11:00", "end_time": "13:00", "slot_minutes": 30, "valid_from": e.day.Format(time.DateOnly)}, e.doctorAuth()); code != 409 || detailCode(body) != "RULE_OVERLAP" {
		t.Fatal("overlap", code, body)
	}
	// Validatsiya
	for _, bad := range []map[string]any{
		{"weekday": 1, "start_time": "10:00", "end_time": "09:00", "slot_minutes": 30, "valid_from": "2030-01-01"},
		{"weekday": 9, "start_time": "09:00", "end_time": "10:00", "slot_minutes": 30, "valid_from": "2030-01-01"},
		{"weekday": 1, "start_time": "09:00", "end_time": "10:00", "slot_minutes": 7, "valid_from": "2030-01-01"},
		{"weekday": 1, "start_time": "09:00", "end_time": "10:00", "slot_minutes": 30},
		{"weekday": 1, "start_time": "09:00", "end_time": "10:00", "slot_minutes": 30, "valid_from": "x"},
		{"weekday": 1, "start_time": "09:00", "end_time": "10:00", "slot_minutes": 30, "valid_from": "2030-01-01", "valid_to": "y"},
	} {
		if code, _ := e.call(http.MethodPost, "/v1/doctor/schedule/rules", bad, e.doctorAuth()); code != 400 {
			t.Fatal("validation", bad, code)
		}
	}
	other := uuid.New()
	if code, body := e.call(http.MethodPost, "/v1/doctor/schedule/rules", map[string]any{"clinic_id": other, "weekday": 0,
		"start_time": "09:00", "end_time": "10:00", "slot_minutes": 30, "valid_from": "2030-01-01"}, e.doctorAuth()); code != 400 || detailCode(body) != "NO_ACTIVE_AFFILIATION" {
		t.Fatal("affiliation", code, body)
	}

	// Klinika qoidasini o'chirish — faol bron → 409, keep_bookings → 200
	code, body := e.call(http.MethodDelete, "/v1/doctor/schedule/rules/"+clinicRule, nil, e.doctorAuth())
	conf, _ := body["detail"].(map[string]any)["conflicts"].([]any)
	if code != 409 || detailCode(body) != "LIVE_BOOKINGS" || len(conf) != 1 || conf[0].(map[string]any)["booking_id"] != ref {
		t.Fatal("delete live", code, body)
	}
	code, body = e.call(http.MethodDelete, "/v1/doctor/schedule/rules/"+clinicRule+"?resolution=keep_bookings", nil, e.doctorAuth())
	if code != 200 || num(body["result"], "kept_bookings") != 1 || num(body["result"], "removed") != 1 {
		t.Fatal("delete keep", code, body)
	}
	e.expectStatus(booked, "booked", "")
	if code, _ := e.call(http.MethodDelete, "/v1/doctor/schedule/rules/"+clinicRule, nil, e.doctorAuth()); code != 404 {
		t.Fatal("deleted twice", code)
	}
	// Bron bekor bo'lsa — qoida yo'q: slot sotuvga chiqmaydi
	e.deliver(events.BookingCancelled, map[string]any{"booking_id": ref, "slot_id": booked})
	e.expectStatus(booked, "blocked", "rule_changed")

	// PATCH: 30 → 60 daqiqa (bron yo'q) — qayta generatsiya
	code, body = e.call(http.MethodPatch, "/v1/doctor/schedule/rules/"+homeRule, map[string]any{"slot_minutes": 60}, e.doctorAuth())
	if code != 200 || num(body["result"], "removed") != 6 || num(body["result"], "created") != 3 {
		t.Fatal("patch", code, body)
	}
	if got := len(e.publicDay()); got != 3 {
		t.Fatal("after patch", got)
	}
	// PATCH: valid_to (null ham qabul qilinadi), klinika — yo'q hamkorlik
	if code, _ := e.call(http.MethodPatch, "/v1/doctor/schedule/rules/"+homeRule, map[string]any{"valid_to": nil, "start_time": "08:00"}, e.doctorAuth()); code != 200 {
		t.Fatal("patch valid_to", code)
	}
	if code, _ := e.call(http.MethodPatch, "/v1/doctor/schedule/rules/"+homeRule, map[string]any{"clinic_id": other}, e.doctorAuth()); code != 400 {
		t.Fatal("patch clinic", code)
	}
	if code, _ := e.call(http.MethodPatch, "/v1/doctor/schedule/rules/"+uuid.NewString(), map[string]any{"slot_minutes": 60}, e.doctorAuth()); code != 404 {
		t.Fatal("patch missing", code)
	}
	if code, _ := e.call(http.MethodPatch, "/v1/doctor/schedule/rules/bad", map[string]any{}, e.doctorAuth()); code != 404 {
		t.Fatal("patch bad id", code)
	}
	if code, _ := e.call(http.MethodPatch, "/v1/doctor/schedule/rules/"+homeRule, map[string]any{"valid_from": "z"}, e.doctorAuth()); code != 400 {
		t.Fatal("patch bad date", code)
	}
	// Bron bilan PATCH: qisqartirish bronni qoplamasa — 409
	slot := e.slotAt(10 * 60)
	r2 := uuid.NewString()
	e.hold(slot, r2, nil)
	if code, body := e.call(http.MethodPatch, "/v1/doctor/schedule/rules/"+homeRule, map[string]any{"end_time": "10:00"}, e.doctorAuth()); code != 409 || detailCode(body) != "LIVE_BOOKINGS" {
		t.Fatal("patch live", code, body)
	}
	code, list := e.list(http.MethodGet, "/v1/doctor/schedule/rules", e.doctorAuth())
	if code != 200 || len(list) != 1 || list[0]["place"] != "home" || list[0]["slot_minutes"].(float64) != 60 || list[0]["start_time"] != "08:00" {
		t.Fatal("list", code, list)
	}
	if code, body := e.call(http.MethodPost, "/v1/doctor/schedule/regenerate", nil, e.doctorAuth()); code != 200 || num(body, "created") != 0 {
		t.Fatal("regenerate", code, body)
	}
}

func TestTimeOffAndDayOff(t *testing.T) {
	e := newEnv(t)
	e.seed()
	e.rules()
	a := e.slotAt(9 * 60)
	ref := uuid.NewString()
	e.hold(a, ref, nil)
	toff := map[string]any{"start_at": domain.At(e.day, 8*60), "end_at": domain.At(e.day, 10*60+30), "kind": "vacation", "reason": "dam"}
	if code, body := e.call(http.MethodPost, "/v1/doctor/schedule/time-off", toff, e.doctorAuth()); code != 409 || detailCode(body) != "LIVE_BOOKINGS" {
		t.Fatal("time off live", code, body)
	}
	code, body := e.call(http.MethodPost, "/v1/doctor/schedule/time-off?resolution=keep_bookings", toff, e.doctorAuth())
	if code != 201 || num(body, "kept_bookings") != 1 {
		t.Fatal("time off", code, body)
	}
	toID := body["time_off"].(map[string]any)["id"].(string)
	e.expectStatus(e.slotAt(10*60), "blocked", "time_off")
	e.expectStatus(e.slotAt(9*60+30), "blocked", "travel_buffer")
	if code, body := e.call(http.MethodPost, "/v1/doctor/schedule/time-off", toff, e.doctorAuth()); code != 409 || detailCode(body) != "TIME_OFF_OVERLAP" {
		t.Fatal("overlap", code, body)
	}
	for _, bad := range []map[string]any{
		{"start_at": domain.At(e.day, 10*60), "end_at": domain.At(e.day, 9*60)},
		{"start_at": domain.At(e.day, 10*60), "end_at": domain.At(e.day, 11*60), "kind": "fun"},
		{"start_at": domain.At(e.day, 10*60), "end_at": domain.At(e.day.AddDate(0, 0, 100), 0)},
	} {
		if code, _ := e.call(http.MethodPost, "/v1/doctor/schedule/time-off", bad, e.doctorAuth()); code != 400 {
			t.Fatal("validation", bad, code)
		}
	}
	// Bron muddati o'tdi — slot va bufer ta'til sababi bilan yopiq qoladi (sotuvga chiqmaydi)
	e.deliver(events.BookingExpired, map[string]any{"booking_id": ref, "slot_id": a})
	e.expectStatus(a, "blocked", "time_off")
	e.expectStatus(e.slotAt(9*60+30), "blocked", "time_off")
	if rel := e.outbox(events.SlotReleased); len(rel) != 1 || rel[0].Data["reason"] != "booking_expired" {
		t.Fatal(rel)
	}
	if code, list := e.list(http.MethodGet, "/v1/doctor/schedule/time-off", e.doctorAuth()); code != 200 || len(list) != 1 || list[0]["kind"] != "vacation" {
		t.Fatal("list", code, list)
	}
	// Dam olish kuni shu kunga — ta'til o'chirilsa ham kun yopiq qoladi
	code, body = e.call(http.MethodPost, "/v1/doctor/schedule/day-offs", map[string]any{"date": e.day.Format(time.DateOnly), "reason": "to'y"}, e.doctorAuth())
	if code != 201 {
		t.Fatal("day off", code, body)
	}
	dayOffID := body["id"].(string)
	e.expectStatus(e.slotAt(14*60), "blocked", "day_off")
	if code, body := e.call(http.MethodPost, "/v1/doctor/schedule/day-offs", map[string]any{"date": e.day.Format(time.DateOnly)}, e.doctorAuth()); code != 409 || detailCode(body) != "DAY_OFF_EXISTS" {
		t.Fatal("dup day off", code, body)
	}
	if code, _ := e.call(http.MethodPost, "/v1/doctor/schedule/day-offs", map[string]any{"date": "2000-01-01"}, e.doctorAuth()); code != 400 {
		t.Fatal("past day off", code)
	}
	if code, _ := e.call(http.MethodPost, "/v1/doctor/schedule/day-offs", map[string]any{"date": "nope"}, e.doctorAuth()); code != 400 {
		t.Fatal("bad day off", code)
	}
	if code, body := e.call(http.MethodDelete, "/v1/doctor/schedule/time-off/"+toID, nil, e.doctorAuth()); code != 200 || num(body, "reopened_slots") != 0 {
		t.Fatal("delete time off under day off", code, body)
	}
	e.expectStatus(a, "blocked", "day_off")
	if code, _ := e.call(http.MethodDelete, "/v1/doctor/schedule/time-off/"+toID, nil, e.doctorAuth()); code != 404 {
		t.Fatal("delete twice", code)
	}
	if code, list := e.list(http.MethodGet, "/v1/doctor/schedule/day-offs", e.doctorAuth()); code != 200 || len(list) != 1 || list[0]["reason"] != "to'y" {
		t.Fatal("list day offs", code, list)
	}
	code, body = e.call(http.MethodDelete, "/v1/doctor/schedule/day-offs/"+dayOffID, nil, e.doctorAuth())
	if code != 200 || num(body, "reopened_slots") != 8 {
		t.Fatal("delete day off", code, body)
	}
	if got := len(e.publicDay()); got != 8 {
		t.Fatal("public after day off removal", got)
	}
	if code, _ := e.call(http.MethodDelete, "/v1/doctor/schedule/day-offs/"+dayOffID, nil, e.doctorAuth()); code != 404 {
		t.Fatal("delete twice", code)
	}
	// Bron bilan dam olish kuni — 409, keep_bookings — 201
	b := e.slotAt(15 * 60)
	e.hold(b, uuid.NewString(), nil)
	next := map[string]any{"date": e.day.Format(time.DateOnly)}
	if code, body := e.call(http.MethodPost, "/v1/doctor/schedule/day-offs", next, e.doctorAuth()); code != 409 || detailCode(body) != "LIVE_BOOKINGS" {
		t.Fatal("day off live", code, body)
	}
	if code, body := e.call(http.MethodPost, "/v1/doctor/schedule/day-offs?resolution=keep_bookings", next, e.doctorAuth()); code != 201 || num(body, "kept_bookings") != 1 {
		t.Fatal("day off keep", code, body)
	}
	e.expectStatus(b, "held", "")
}

func TestReopenedTimeOffSlots(t *testing.T) {
	e := newEnv(t)
	e.seed()
	e.rules()
	toff := map[string]any{"start_at": domain.At(e.day, 9*60), "end_at": domain.At(e.day, 10*60)}
	code, body := e.call(http.MethodPost, "/v1/doctor/schedule/time-off", toff, e.doctorAuth())
	if code != 201 {
		t.Fatal(code, body)
	}
	e.expectStatus(e.slotAt(9*60), "blocked", "time_off")
	id := body["time_off"].(map[string]any)["id"].(string)
	if code, body := e.call(http.MethodDelete, "/v1/doctor/schedule/time-off/"+id, nil, e.doctorAuth()); code != 200 || num(body, "reopened_slots") != 2 {
		t.Fatal(code, body)
	}
	if got := len(e.publicDay()); got != 8 {
		t.Fatal(got)
	}
}

func TestSlotBlockAndCalendar(t *testing.T) {
	e := newEnv(t)
	e.seed()
	e.rules()
	s := e.slotAt(9 * 60).String()
	if code, _, _ := e.do(http.MethodPost, "/v1/doctor/schedule/slots/"+s+"/block", map[string]any{"note": "majlis"}, e.doctorAuth()); code != 204 {
		t.Fatal(code)
	}
	if code, body := e.call(http.MethodPost, "/v1/doctor/schedule/slots/"+s+"/block", nil, e.doctorAuth()); code != 400 || detailCode(body) != "SLOT_NOT_FREE" {
		t.Fatal(code, body)
	}
	from, to := e.day.Format(time.DateOnly), e.day.AddDate(0, 0, 1).Format(time.DateOnly)
	code, items := e.list(http.MethodGet, "/v1/doctor/schedule/calendar?from="+from+"&to="+to, e.doctorAuth())
	if code != 200 || len(items) != 8 || items[0]["status"] != "blocked" || items[0]["block_note"] != "majlis" {
		t.Fatal(code, items)
	}
	if code, _ := e.call(http.MethodGet, "/v1/doctor/schedule/calendar?from="+from, nil, e.doctorAuth()); code != 400 {
		t.Fatal("no to", code)
	}
	if code, _ := e.call(http.MethodGet, "/v1/doctor/schedule/calendar?from="+from+"&to="+e.day.AddDate(0, 0, 40).Format(time.DateOnly), nil, e.doctorAuth()); code != 400 {
		t.Fatal("range", code)
	}
	if code, _, _ := e.do(http.MethodPost, "/v1/doctor/schedule/slots/"+s+"/unblock", nil, e.doctorAuth()); code != 204 {
		t.Fatal(code)
	}
	if code, body := e.call(http.MethodPost, "/v1/doctor/schedule/slots/"+s+"/unblock", nil, e.doctorAuth()); code != 400 || detailCode(body) != "SLOT_NOT_BLOCKED" {
		t.Fatal(code, body)
	}
	if code, _ := e.call(http.MethodPost, "/v1/doctor/schedule/slots/bad/unblock", nil, e.doctorAuth()); code != 404 {
		t.Fatal(code)
	}
	if code, _ := e.call(http.MethodPost, "/v1/doctor/schedule/slots/"+s+"/block", map[string]any{"note": strings.Repeat("x", 300)}, e.doctorAuth()); code != 400 {
		t.Fatal(code)
	}
}

func TestClinicAdmin(t *testing.T) {
	e := newEnv(t)
	e.seed()
	_, clinicRule := e.rules()
	ruleID := clinicRule["rule"].(map[string]any)["id"].(string)
	admin := uuid.New()
	e.cat.admins[admin.String()+":"+e.clinic.String()] = true
	base := "/v1/clinic/" + e.clinic.String()

	if code, body := e.call(http.MethodGet, base+"/schedule", nil, e.userAuth(uuid.New())); code != 404 || detailCode(body) != "CLINIC_NOT_FOUND" {
		t.Fatal("stranger", code, body)
	}
	code, day := e.call(http.MethodGet, base+"/schedule?date="+e.day.Format(time.DateOnly), nil, e.userAuth(admin))
	if code != 200 || len(day["doctors"].(map[string]any)[e.doctor.String()].([]any)) != 2 {
		t.Fatal("day", code, day)
	}
	accessCalls := func() int { e.cat.mu.Lock(); defer e.cat.mu.Unlock(); return e.cat.accessCalls }
	calls := accessCalls()
	e.call(http.MethodGet, base+"/schedule", nil, e.userAuth(admin))
	if accessCalls() != calls {
		t.Fatal("access must be cached")
	}
	if code, list := e.list(http.MethodGet, base+"/rules", e.userAuth(admin)); code != 200 || len(list) != 1 || list[0]["doctor_id"] != e.doctor.String() {
		t.Fatal("rules", code, list)
	}

	// Xona: boshqa klinikaniki — 400; o'ziniki — slotlar ko'chadi
	room, foreign := uuid.New(), uuid.New()
	e.cat.rooms[room] = clients.Room{ID: room, ClinicID: e.clinic, Name: "101", IsActive: true}
	e.cat.rooms[foreign] = clients.Room{ID: foreign, ClinicID: uuid.New(), Name: "x", IsActive: true}
	if code, body := e.call(http.MethodPost, base+"/rules/"+ruleID+"/room", map[string]any{"room_id": foreign}, e.userAuth(admin)); code != 400 || detailCode(body) != "INVALID_ROOM" {
		t.Fatal("foreign room", code, body)
	}
	if code, _ := e.call(http.MethodPost, base+"/rules/"+ruleID+"/room", map[string]any{"room_id": uuid.New()}, e.userAuth(admin)); code != 400 {
		t.Fatal("missing room", code)
	}
	if code, _ := e.call(http.MethodPost, base+"/rules/"+uuid.NewString()+"/room", map[string]any{"room_id": room}, e.userAuth(admin)); code != 404 {
		t.Fatal("missing rule", code)
	}
	code, res := e.call(http.MethodPost, base+"/rules/"+ruleID+"/room", map[string]any{"room_id": room}, e.userAuth(admin))
	if code != 200 || num(res, "moved") != 2 {
		t.Fatal("assign", code, res)
	}
	var withRoom int
	_ = e.pool.QueryRow(e.ctx, `SELECT count(*) FROM slots WHERE room_id = $1`, room).Scan(&withRoom)
	if withRoom != 2 {
		t.Fatal(withRoom)
	}

	// Ikkinchi shifokor shu xonani o'sha vaqtga oladi — bo'sh slot `room_busy`, bron — conflicts
	doc2 := uuid.New()
	e.cat.bookability[doc2] = clients.Bookability{DoctorID: doc2, Bookable: true, ClinicIDs: []uuid.UUID{e.clinic}}
	e.deliver(events.DoctorApproved, map[string]any{"doctor_id": doc2, "user_id": uuid.New(), "accepts_home_visits": false})
	e.deliver(events.AffiliationAccepted, map[string]any{"affiliation_id": uuid.New(), "clinic_id": e.clinic, "doctor_id": doc2})
	c2 := testutil.UserClaims(uuid.New(), "doctor")
	c2["doctor_id"] = doc2.String()
	auth2 := map[string]string{"Authorization": "Bearer " + e.keys.Sign(t, c2)}
	code, r2 := e.call(http.MethodPost, "/v1/doctor/schedule/rules", map[string]any{"clinic_id": e.clinic, "weekday": domain.Weekday(e.day),
		"start_time": "14:00", "end_time": "16:00", "slot_minutes": 60, "valid_from": domain.Today(time.Now()).Format(time.DateOnly)}, auth2)
	if code != 201 {
		t.Fatal(code, r2)
	}
	if code, body := e.call(http.MethodPost, "/v1/doctor/schedule/rules", map[string]any{"weekday": 0, "start_time": "09:00",
		"end_time": "10:00", "slot_minutes": 30, "valid_from": "2030-01-01"}, auth2); code != 400 || detailCode(body) != "HOME_VISITS_DISABLED" {
		t.Fatal("home disabled", code, body)
	}
	var s2 uuid.UUID
	_ = e.pool.QueryRow(e.ctx, `SELECT id FROM slots WHERE doctor_id = $1 AND start_at = $2`, doc2, domain.At(e.day, 14*60)).Scan(&s2)
	ref := uuid.NewString()
	e.call(http.MethodPost, "/internal/v1/slots/"+s2.String()+"/hold", map[string]any{"hold_ref": ref, "doctor_id": doc2}, internal)
	rule2 := r2["rule"].(map[string]any)["id"].(string)
	code, res = e.call(http.MethodPost, base+"/rules/"+rule2+"/room", map[string]any{"room_id": room}, e.userAuth(admin))
	if code != 200 || num(res, "blocked") != 1 || len(res["conflicts"].([]any)) != 1 {
		t.Fatal("busy room", code, res)
	}
	// Xona olib tashlanadi — `room_busy` slot qayta ochiladi
	code, res = e.call(http.MethodPost, base+"/rules/"+rule2+"/room", map[string]any{"room_id": nil}, e.userAuth(admin))
	if code != 200 || num(res, "moved") != 2 {
		t.Fatal("unassign", code, res)
	}
	var busy int
	_ = e.pool.QueryRow(e.ctx, `SELECT count(*) FROM slots WHERE block_reason = 'room_busy'`).Scan(&busy)
	if busy != 0 {
		t.Fatal("room_busy left", busy)
	}

	// Yopiq kun: bloklar + qayta yoziladigan bronlar
	code, res = e.call(http.MethodPost, base+"/closures", map[string]any{"date": e.day.Format(time.DateOnly), "reason": "ta'mir"}, e.userAuth(admin))
	if code != 201 || num(res, "blocked") != 3 || len(res["bookings_to_reschedule"].([]any)) != 1 {
		t.Fatal("closure", code, res)
	}
	e.expectStatus(e.slotAt(14*60), "blocked", "clinic_closed")
	e.expectStatus(e.slotAt(9*60), "free", "") // uy chaqiruvi — klinika yopilishi ta'sir qilmaydi
	if code, body := e.call(http.MethodPost, base+"/closures", map[string]any{"date": e.day.Format(time.DateOnly)}, e.userAuth(admin)); code != 409 || detailCode(body) != "CLOSURE_EXISTS" {
		t.Fatal("dup closure", code, body)
	}
	if code, _ := e.call(http.MethodPost, base+"/closures", map[string]any{"date": domain.Today(time.Now()).Format(time.DateOnly)}, e.userAuth(admin)); code != 400 {
		t.Fatal("today closure", code)
	}
	code, list := e.list(http.MethodGet, base+"/closures", e.userAuth(admin))
	if code != 200 || len(list) != 1 {
		t.Fatal("list", code, list)
	}
	if code, _, _ := e.do(http.MethodDelete, base+"/closures/"+list[0]["id"].(string), nil, e.userAuth(admin)); code != 204 {
		t.Fatal("delete closure", code)
	}
	e.expectStatus(e.slotAt(14*60), "free", "")
	if code, _, _ := e.do(http.MethodDelete, base+"/closures/"+list[0]["id"].(string), nil, e.userAuth(admin)); code != 404 {
		t.Fatal("delete twice", code)
	}
}

func TestPriceRules(t *testing.T) {
	e := newEnv(t)
	e.seed()
	e.rules()
	code, p := e.call(http.MethodPost, "/v1/doctor/price-rules", map[string]any{"name": "ertalab", "weekdays": "0123456",
		"start_time": "09:00", "end_time": "10:00", "percent": 20}, e.doctorAuth())
	if code != 201 || p["percent"].(float64) != 20 || p["is_active"] != true {
		t.Fatal(code, p)
	}
	id := p["id"].(string)
	day := e.publicDay()
	if day[0]["price_adjust_percent"].(float64) != 20 || day[2]["price_adjust_percent"].(float64) != 0 {
		t.Fatal("public percent", day[0], day[2])
	}
	if _, out := e.call(http.MethodGet, "/internal/v1/slots/"+e.slotAt(9*60+30).String(), nil, internal); out["price_adjust_percent"].(float64) != 20 {
		t.Fatal(out)
	}
	if code, p := e.call(http.MethodPatch, "/v1/doctor/price-rules/"+id, map[string]any{"percent": -15}, e.doctorAuth()); code != 200 || p["percent"].(float64) != -15 {
		t.Fatal(code, p)
	}
	if e.publicDay()[0]["price_adjust_percent"].(float64) != -15 {
		t.Fatal("cache must be invalidated on price change")
	}
	if code, _ := e.call(http.MethodPatch, "/v1/doctor/price-rules/"+id, map[string]any{"percent": 500}, e.doctorAuth()); code != 400 {
		t.Fatal("percent range", code)
	}
	if code, _ := e.call(http.MethodPost, "/v1/doctor/price-rules", map[string]any{"start_time": "09:00"}, e.doctorAuth()); code != 400 {
		t.Fatal("required", code)
	}
	if code, _ := e.call(http.MethodPost, "/v1/doctor/price-rules", map[string]any{"start_time": "09:00", "end_time": "10:00", "percent": 1, "weekdays": "78"}, e.doctorAuth()); code != 400 {
		t.Fatal("weekdays", code)
	}
	if code, _ := e.call(http.MethodPatch, "/v1/doctor/price-rules/"+uuid.NewString(), map[string]any{"percent": 1}, e.doctorAuth()); code != 404 {
		t.Fatal("missing", code)
	}
	if code, list := e.list(http.MethodGet, "/v1/doctor/price-rules", e.doctorAuth()); code != 200 || len(list) != 1 {
		t.Fatal(code, list)
	}
	if code, _, _ := e.do(http.MethodDelete, "/v1/doctor/price-rules/"+id, nil, e.doctorAuth()); code != 204 {
		t.Fatal(code)
	}
	if code, _, _ := e.do(http.MethodDelete, "/v1/doctor/price-rules/"+id, nil, e.doctorAuth()); code != 404 {
		t.Fatal(code)
	}
	if code, list := e.list(http.MethodGet, "/v1/doctor/price-rules", e.doctorAuth()); code != 200 || list == nil || len(list) != 0 {
		t.Fatal("empty list must be []", code, list)
	}
}

func TestProjectionEvents(t *testing.T) {
	e := newEnv(t)
	e.seed()
	e.rules()
	home, clinic := e.slotAt(9*60), e.slotAt(14*60)

	// Shifokor to'xtatildi — barcha bo'sh slotlar yopiladi, doctor endpointlari 403
	e.deliver(events.DoctorSuspended, map[string]any{"doctor_id": e.doctor, "user_id": e.userID})
	e.expectStatus(home, "blocked", "doctor_suspended")
	if code, body := e.call(http.MethodGet, "/v1/doctor/schedule/rules", nil, e.doctorAuth()); code != 403 || detailCode(body) != "DOCTOR_NOT_ACTIVE" {
		t.Fatal(code, body)
	}
	// Eskirgan event (oldingi vaqt) qo'llanmaydi
	if err := e.tryDeliver(events.DoctorReinstated, map[string]any{"doctor_id": e.doctor, "user_id": e.userID}, time.Now().Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	e.expectStatus(home, "blocked", "doctor_suspended")
	e.deliver(events.DoctorReinstated, map[string]any{"doctor_id": e.doctor, "user_id": e.userID})
	e.expectStatus(home, "free", "")

	// Klinika to'xtatildi/qayta faol
	e.deliver(events.ClinicSuspended, map[string]any{"clinic_id": e.clinic})
	e.expectStatus(clinic, "blocked", "clinic_suspended")
	e.expectStatus(home, "free", "")
	// Klinika to'xtagan paytda hamkorlik ham pauza — klinika qaytsa ham slot yopiq qoladi (endi pauza sababi bilan),
	// hamkorlik tiklanganda ochiladi
	e.deliver(events.AffiliationPaused, map[string]any{"affiliation_id": e.aff, "clinic_id": e.clinic, "doctor_id": e.doctor})
	e.deliver(events.ClinicPublished, map[string]any{"clinic_id": e.clinic})
	e.expectStatus(clinic, "blocked", "affiliation_paused")
	e.deliver(events.AffiliationResumed, map[string]any{"affiliation_id": e.aff, "clinic_id": e.clinic, "doctor_id": e.doctor})
	e.expectStatus(clinic, "free", "")

	// Hamkorlik pauza → ochish → tugash
	e.deliver(events.AffiliationPaused, map[string]any{"affiliation_id": e.aff, "clinic_id": e.clinic, "doctor_id": e.doctor})
	e.expectStatus(clinic, "blocked", "affiliation_paused")
	e.deliver(events.AffiliationResumed, map[string]any{"affiliation_id": e.aff, "clinic_id": e.clinic, "doctor_id": e.doctor})
	e.expectStatus(clinic, "free", "")
	e.deliver(events.AffiliationEnded, map[string]any{"affiliation_id": e.aff, "clinic_id": e.clinic, "doctor_id": e.doctor})
	e.expectStatus(clinic, "blocked", "affiliation_paused")

	// booking.rescheduled — eski slot bo'shaydi
	ref := uuid.NewString()
	e.hold(e.slotAt(11*60), ref, map[string]any{"travel_buffer_minutes": 0})
	e.deliver(events.BookingRescheduled, map[string]any{"booking_id": ref, "old_slot_id": e.slotAt(11 * 60), "new_slot_id": uuid.New()})
	e.expectStatus(e.slotAt(11*60), "free", "")
	rel := e.outbox(events.SlotReleased)
	if len(rel) != 1 || rel[0].Data["reason"] != "booking_rescheduled" {
		t.Fatal(rel)
	}

	// admin.settings_changed — kesh tashlanadi
	e.svc.Settings.Schedule(e.ctx)
	if n, _ := e.rdb.Exists(e.ctx, "admin:settings:v1").Result(); n != 1 {
		t.Fatal("settings not cached")
	}
	e.deliver(events.AdminSettingsChanged, map[string]any{"keys": []string{"schedule"}, "version": 4})
	if n, _ := e.rdb.Exists(e.ctx, "admin:settings:v1").Result(); n != 0 {
		t.Fatal("settings not invalidated")
	}
	if n, _ := e.rdb.Exists(e.ctx, "admin:settings:v1:last").Result(); n != 1 {
		t.Fatal("last-known must stay")
	}

	// Noto'g'ri payload — doimiy xato (DLQ)
	if err := e.tryDeliver(events.DoctorApproved, "not an object", time.Now()); err == nil || !strings.Contains(err.Error(), "permanent") {
		t.Fatal("permanent", err)
	}
	// Har bir handler mavjud
	for _, et := range []string{events.AdminSettingsChanged, events.DoctorApproved, events.DoctorSuspended, events.DoctorReinstated,
		events.ClinicPublished, events.ClinicSuspended, events.AffiliationAccepted, events.AffiliationResumed,
		events.AffiliationPaused, events.AffiliationEnded, events.BookingCancelled, events.BookingExpired, events.BookingRescheduled} {
		if schedbus.Handler(e.svc, et) == nil {
			t.Fatal("no handler", et)
		}
	}
}

func TestGenerationJobs(t *testing.T) {
	e := newEnv(t)
	e.seed()
	e.rules()
	// Idempotent
	if n, err := e.svc.GenerateAll(e.ctx); err != nil || n != 0 {
		t.Fatal(n, err)
	}
	if _, err := e.pool.Exec(e.ctx, `DELETE FROM slots WHERE start_at >= $1`, domain.At(e.day, 14*60)); err != nil {
		t.Fatal(err)
	}
	if n, err := e.svc.GenerateAll(e.ctx); err != nil || n != 2 {
		t.Fatal("regenerated", n, err)
	}
	// Klinika yopiq kuni generatsiyada hisobga olinadi
	admin := uuid.New()
	e.cat.admins[admin.String()+":"+e.clinic.String()] = true
	next := e.day.AddDate(0, 0, 7) // gorizont tashqarisi — generatsiya yo'q, faqat yozuv
	if code, _ := e.call(http.MethodPost, "/v1/clinic/"+e.clinic.String()+"/closures", map[string]any{"date": next.Format(time.DateOnly)}, e.userAuth(admin)); code != 201 {
		t.Fatal(code)
	}
	// Retention: 100 kun oldingi, hech qachon band qilinmagan slot o'chadi; bron tarixi bori qoladi
	old := time.Now().AddDate(0, 0, -100)
	if _, err := e.pool.Exec(e.ctx, `INSERT INTO slots (id, doctor_id, start_at, end_at) VALUES ($1, $2, $3, $4)`,
		uuid.New(), e.doctor, old, old.Add(30*time.Minute)); err != nil {
		t.Fatal(err)
	}
	if _, err := e.pool.Exec(e.ctx, `INSERT INTO slots (id, doctor_id, start_at, end_at, status, hold_ref, ever_held)
		VALUES ($1, $2, $3, $4, 'booked', 'b', true)`, uuid.New(), e.doctor, old.Add(time.Hour), old.Add(90*time.Minute)); err != nil {
		t.Fatal(err)
	}
	if n, err := e.svc.PurgeOld(e.ctx, 90); err != nil || n != 1 {
		t.Fatal("purge", n, err)
	}
	// Tasdiqlanmagan shifokor uchun generatsiya yo'q
	ghost := uuid.New()
	if n, err := e.svc.Generate(e.ctx, ghost); err != nil || n != 0 {
		t.Fatal(n, err)
	}
	// Admin sozlamalari Redis'da keshlanadi (bir marta chaqiruv)
	calls := e.settings.Load()
	for range 3 {
		e.svc.Settings.Schedule(e.ctx)
	}
	if got := e.settings.Load(); got != calls {
		t.Fatal("settings must be cached", got, calls)
	}
}

func TestPublicCacheStampede(t *testing.T) {
	e := newEnv(t)
	e.seed()
	e.rules()
	var wg sync.WaitGroup
	for range 10 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if got := len(e.publicDay()); got != 8 {
				t.Error("parallel read", got)
			}
		}()
	}
	wg.Wait()
	// Lead time: bugungi o'tgan/yaqin slotlar ko'rinmaydi — ertaga/indinga ta'sir yo'q
	if _, err := e.pool.Exec(e.ctx, `INSERT INTO slots (id, doctor_id, start_at, end_at) VALUES ($1, $2, $3, $4)`,
		uuid.New(), e.doctor, time.Now().Add(10*time.Minute), time.Now().Add(40*time.Minute)); err != nil {
		t.Fatal(err)
	}
	today := domain.Today(time.Now())
	code, out := e.list(http.MethodGet, fmt.Sprintf("/v1/schedule/doctors/%s/slots?date=%s", e.doctor, today.Format(time.DateOnly)), nil)
	for _, s := range out {
		st, _ := time.Parse(time.RFC3339, s["start_at"].(string))
		if st.Before(time.Now().Add(time.Hour)) {
			t.Fatal("lead time violated", code, s)
		}
	}
}

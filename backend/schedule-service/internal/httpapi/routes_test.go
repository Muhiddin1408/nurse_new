package httpapi_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/testutil"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/httpapi"
)

func setup(t *testing.T) (http.Handler, testutil.KeyPair, testutil.KeyPair) {
	t.Helper()
	user, admin := testutil.NewKeyPair(t), testutil.NewKeyPair(t)
	uk, _ := auth.ParsePublicKeys(user.PublicPEM)
	ak, _ := auth.ParsePublicKeys(admin.PublicPEM)
	tokens := map[string]string{"test-caller": "tok"}
	for _, c := range httpapi.AllowedCallers {
		tokens[c] = "tok"
	}
	a := &app.App{Auth: &auth.Authenticator{
		User:          auth.NewVerifier(uk, "medbron-auth", "medbron"),
		Admin:         auth.NewVerifier(ak, "medbron-admin", "admin"),
		InboundTokens: tokens,
	}}
	return httpapi.Routes(a), user, admin
}

func get(h http.Handler, path string, hdr map[string]string) (int, map[string]any) {
	req := httptest.NewRequest(http.MethodGet, path, nil)
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	var body map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	return rec.Code, body
}

func TestHealthAndMetrics(t *testing.T) {
	h, _, _ := setup(t)
	for _, p := range []string{"/health/live", "/health/ready", "/metrics"} {
		if code, _ := get(h, p, nil); code != 200 {
			t.Fatal(p, code)
		}
	}
}

func req(h http.Handler, method, path string, hdr map[string]string) int {
	r := httptest.NewRequest(method, path, nil)
	for k, v := range hdr {
		r.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, r)
	return rec.Code
}

// TestAuthZones — DB'ga tushmasdan tekshiriladigan chegaralar (to'liq oqimlar — integratsiya testlarida).
func TestAuthZones(t *testing.T) {
	h, user, admin := setup(t)
	ut := map[string]string{"Authorization": "Bearer " + user.Sign(t, testutil.UserClaims(uuid.New()))}
	at := map[string]string{"Authorization": "Bearer " + admin.Sign(t, testutil.AdminClaims(uuid.New()))}

	// Ommaviy: token kerak emas; noto'g'ri uuid — 404 (DB'ga bormaydi).
	if code := req(h, http.MethodGet, "/v1/schedule/doctors/x/slots", nil); code != 404 {
		t.Fatal("public bad id", code)
	}
	for _, p := range []string{"/v1/doctor/schedule/rules", "/v1/doctor/price-rules", "/v1/clinic/" + uuid.NewString() + "/schedule"} {
		if code := req(h, http.MethodGet, p, nil); code != 401 {
			t.Fatal(p, "no token", code)
		}
		if code := req(h, http.MethodGet, p, at); code != 401 && code != 403 {
			t.Fatal(p, "admin token", code)
		}
	}
	// Shifokor bo'lmagan user — 403 ROLE_REQUIRED.
	if code, body := get(h, "/v1/doctor/schedule/rules", ut); code != 403 || body["detail"].(map[string]any)["code"] != "ROLE_REQUIRED" {
		t.Fatal(code, body)
	}
	// Klinika ID noto'g'ri — 404 (catalog'ga bormaydi).
	if code := req(h, http.MethodGet, "/v1/clinic/bad/schedule", ut); code != 404 {
		t.Fatal("clinic bad id", code)
	}

	slot := "/internal/v1/slots/" + uuid.NewString()
	if code := req(h, http.MethodGet, slot, nil); code != 401 && code != 403 {
		t.Fatal("internal no token", code)
	}
	if code := req(h, http.MethodGet, slot, map[string]string{"X-Caller": "test-caller", "X-Internal-Token": "tok"}); code != 403 {
		t.Fatal("not-allowed caller", code)
	}
	ok := map[string]string{"X-Caller": "booking-service", "X-Internal-Token": "tok"}
	if code := req(h, http.MethodGet, "/internal/v1/slots/bad", ok); code != 404 {
		t.Fatal("internal bad id", code)
	}
	if code := req(h, http.MethodGet, "/internal/v1/doctors/"+uuid.NewString()+"/free-slots", ok); code != 400 {
		t.Fatal("free-slots without from/to", code)
	}
}

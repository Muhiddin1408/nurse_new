package httpapi_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/testutil"

	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/httpapi"
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

func TestAuthZones(t *testing.T) {
	h, user, admin := setup(t)
	uid := uuid.New()
	ut := map[string]string{"Authorization": "Bearer " + user.Sign(t, testutil.UserClaims(uid))}
	at := map[string]string{"Authorization": "Bearer " + admin.Sign(t, testutil.AdminClaims(uuid.New(), httpapi.PermRead))}

	if code, _ := get(h, "/v1/__SHORT__/whoami", nil); code != 401 {
		t.Fatal(code)
	}
	if code, body := get(h, "/v1/__SHORT__/whoami", ut); code != 200 || body["user_id"] != uid.String() {
		t.Fatal(code, body)
	}
	if code, _ := get(h, "/v1/__SHORT__/whoami", at); code != 403 {
		t.Fatal("admin token on user endpoint", code)
	}
	if code, _ := get(h, "/v1/admin/__SHORT__/whoami", at); code != 200 {
		t.Fatal(code)
	}
	if code, _ := get(h, "/v1/admin/__SHORT__/whoami", ut); code != 403 {
		t.Fatal(code)
	}
	for _, c := range httpapi.AllowedCallers {
		if code, _ := get(h, "/internal/v1/__SHORT__/ping", map[string]string{"X-Caller": c, "X-Internal-Token": "tok"}); code != 200 {
			t.Fatal(c, code)
		}
	}
	if code, _ := get(h, "/internal/v1/__SHORT__/ping", map[string]string{"X-Caller": "test-caller", "X-Internal-Token": "tok"}); code != 403 {
		t.Fatal("not-allowed caller", code)
	}
}

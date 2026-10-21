package auth_test

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/testutil"
)

func verifier(t *testing.T, pems ...[]byte) *auth.Verifier {
	t.Helper()
	var all []byte
	for _, p := range pems {
		all = append(all, p...)
	}
	keys, err := auth.ParsePublicKeys(all)
	if err != nil {
		t.Fatal(err)
	}
	return auth.NewVerifier(keys, "medbron-auth", "medbron")
}

func code(err error) string {
	var e *httpapi.Error
	if errors.As(err, &e) {
		return e.Code
	}
	return ""
}

func TestKidMatchesPythonVector(t *testing.T) {
	raw, err := os.ReadFile("../../testdata/kid-vector.json")
	if err != nil {
		t.Fatal(err)
	}
	var v struct {
		PublicPEM string `json:"public_pem"`
		KID       string `json:"kid"`
	}
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatal(err)
	}
	keys, err := auth.ParsePublicKeys([]byte(v.PublicPEM))
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := keys[v.KID]; !ok {
		t.Fatalf("Go kid != Python kid %q (got %v)", v.KID, keys)
	}
}

func TestVerifyUserToken(t *testing.T) {
	kp := testutil.NewKeyPair(t)
	uid, did := uuid.New(), uuid.New()
	claims := testutil.UserClaims(uid, "doctor", "client")
	claims["doctor_id"] = did.String()
	c, err := verifier(t, kp.PublicPEM).Verify(kp.Sign(t, claims))
	if err != nil {
		t.Fatal(err)
	}
	u, err := auth.UserFromClaims(c)
	if err != nil {
		t.Fatal(err)
	}
	if u.ID != uid || *u.DoctorID != did || u.Role != "doctor" || !u.HasRole("client") || u.HasRole("clinic_admin") {
		t.Fatalf("unexpected user %+v", u)
	}
}

func TestRejections(t *testing.T) {
	kp, other := testutil.NewKeyPair(t), testutil.NewKeyPair(t)
	v := verifier(t, kp.PublicPEM)
	uid := uuid.New()

	expired := testutil.UserClaims(uid)
	expired["exp"] = time.Now().Add(-time.Hour).Unix()
	wrongIss := testutil.UserClaims(uid)
	wrongIss["iss"] = "evil"
	refresh := testutil.UserClaims(uid)
	refresh["type"] = "refresh"
	hs := jwt.NewWithClaims(jwt.SigningMethodHS256, testutil.UserClaims(uid))
	hsToken, _ := hs.SignedString([]byte("public-key-as-hmac-secret-attack"))

	cases := map[string]struct {
		token string
		want  string
	}{
		"expired":       {kp.Sign(t, expired), "TOKEN_EXPIRED"},
		"wrong issuer":  {kp.Sign(t, wrongIss), "TOKEN_INVALID"},
		"refresh type":  {kp.Sign(t, refresh), "TOKEN_INVALID"},
		"unknown key":   {other.Sign(t, testutil.UserClaims(uid)), "TOKEN_INVALID"},
		"hs256":         {hsToken, "TOKEN_INVALID"},
		"garbage":       {"not.a.token", "TOKEN_INVALID"},
		"admin on user": {kp.Sign(t, testutil.AdminClaims(uid, "*")), "WRONG_AUDIENCE"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := v.Verify(tc.token)
			if got := code(err); got != tc.want {
				t.Fatalf("got %q want %q (%v)", got, tc.want, err)
			}
		})
	}
}

func TestKeyRotation(t *testing.T) {
	a, b := testutil.NewKeyPair(t), testutil.NewKeyPair(t)
	v := verifier(t, a.PublicPEM, b.PublicPEM)
	for _, kp := range []testutil.KeyPair{a, b} {
		if _, err := v.Verify(kp.Sign(t, testutil.UserClaims(uuid.New()))); err != nil {
			t.Fatal(err)
		}
	}
}

func TestMiddlewares(t *testing.T) {
	user, admin := testutil.NewKeyPair(t), testutil.NewKeyPair(t)
	ukeys, _ := auth.ParsePublicKeys(user.PublicPEM)
	akeys, _ := auth.ParsePublicKeys(admin.PublicPEM)
	a := &auth.Authenticator{
		User:          auth.NewVerifier(ukeys, "medbron-auth", "medbron"),
		Admin:         auth.NewVerifier(akeys, "medbron-admin", "admin"),
		InboundTokens: map[string]string{"booking-service": "tok", "review-service": "rev"},
	}
	ok := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(200) })

	do := func(h http.Handler, hdr map[string]string) (int, string) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		for k, v := range hdr {
			req.Header.Set(k, v)
		}
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		var body struct {
			Detail struct{ Code string } `json:"detail"`
		}
		_ = json.Unmarshal(rec.Body.Bytes(), &body)
		return rec.Code, body.Detail.Code
	}
	bearer := func(s string) map[string]string { return map[string]string{"Authorization": "Bearer " + s} }
	userTok := user.Sign(t, testutil.UserClaims(uuid.New()))
	doctorTok := user.Sign(t, testutil.UserClaims(uuid.New(), "doctor"))
	adminOK := admin.Sign(t, testutil.AdminClaims(uuid.New(), "schedule.manage"))
	adminNo := admin.Sign(t, testutil.AdminClaims(uuid.New(), "other"))

	checks := []struct {
		name   string
		h      http.Handler
		hdr    map[string]string
		status int
		code   string
	}{
		{"no token", a.RequireUser(ok), nil, 401, "UNAUTHORIZED"},
		{"user ok", a.RequireUser(ok), bearer(userTok), 200, ""},
		{"admin token on user", a.RequireUser(ok), bearer(adminOK), 403, "WRONG_AUDIENCE"},
		{"role missing", a.RequireRoles("doctor")(ok), bearer(userTok), 403, "ROLE_REQUIRED"},
		{"role ok", a.RequireRoles("doctor")(ok), bearer(doctorTok), 200, ""},
		{"admin ok", a.RequireAdmin("schedule.manage")(ok), bearer(adminOK), 200, ""},
		{"admin no perm", a.RequireAdmin("schedule.manage")(ok), bearer(adminNo), 403, "PERMISSION_DENIED"},
		{"user on admin", a.RequireAdmin("schedule.manage")(ok), bearer(userTok), 403, "WRONG_AUDIENCE"},
		{"internal ok", a.RequireInternal("booking-service")(ok),
			map[string]string{"X-Caller": "booking-service", "X-Internal-Token": "tok"}, 200, ""},
		{"internal bad token", a.RequireInternal("booking-service")(ok),
			map[string]string{"X-Caller": "booking-service", "X-Internal-Token": "x"}, 401, "INTERNAL_AUTH_FAILED"},
		{"internal unknown caller", a.RequireInternal("booking-service")(ok),
			map[string]string{"X-Caller": "evil", "X-Internal-Token": "tok"}, 401, "INTERNAL_AUTH_FAILED"},
		{"internal not allowed", a.RequireInternal("booking-service")(ok),
			map[string]string{"X-Caller": "review-service", "X-Internal-Token": "rev"}, 403, "CALLER_NOT_ALLOWED"},
	}
	for _, c := range checks {
		t.Run(c.name, func(t *testing.T) {
			status, gotCode := do(c.h, c.hdr)
			if status != c.status || gotCode != c.code {
				t.Fatalf("got %d %q want %d %q", status, gotCode, c.status, c.code)
			}
		})
	}
}

package httpapi_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/health"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"
)

func router(readyErr error) http.Handler {
	r := httpapi.NewRouter(map[string]health.Check{"db": func(context.Context) error { return readyErr }})
	r.Get("/v1/things/{id}", httpapi.Handle(func(w http.ResponseWriter, r *http.Request) error {
		return httpapi.Conflict("SLOT_TAKEN", "Bu vaqt allaqachon band qilingan")
	}))
	r.Post("/v1/things", httpapi.Handle(func(w http.ResponseWriter, r *http.Request) error {
		var body struct {
			Count int `json:"count"`
		}
		if err := httpapi.DecodeJSON(w, r, &body); err != nil {
			return err
		}
		httpapi.WriteJSON(w, 201, body)
		return nil
	}))
	r.Get("/boom", httpapi.Handle(func(w http.ResponseWriter, r *http.Request) error { return errors.New("secret") }))
	r.Get("/panic", func(w http.ResponseWriter, r *http.Request) { panic("x") })
	return r
}

func call(h http.Handler, method, path, body string, hdr map[string]string) (*httptest.ResponseRecorder, map[string]any) {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	var out map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &out)
	return rec, out
}

func detailCode(out map[string]any) string {
	d, _ := out["detail"].(map[string]any)
	c, _ := d["code"].(string)
	return c
}

func TestHealth(t *testing.T) {
	rec, _ := call(router(nil), "GET", "/health/live", "", nil)
	if rec.Code != 200 {
		t.Fatal(rec.Code)
	}
	rec, out := call(router(nil), "GET", "/health/ready", "", nil)
	if rec.Code != 200 || out["status"] != "ok" {
		t.Fatal(rec.Code, out)
	}
	rec, out = call(router(errors.New("db down")), "GET", "/health/ready", "", nil)
	if rec.Code != 503 || out["checks"].(map[string]any)["db"] != "db down" {
		t.Fatal(rec.Code, out)
	}
	if rec, _ := call(router(errors.New("db down")), "GET", "/health/live", "", nil); rec.Code != 200 {
		t.Fatal("live must not depend on db")
	}
}

func TestErrorsAndRequestID(t *testing.T) {
	h := router(nil)
	rec, out := call(h, "GET", "/v1/things/1", "", map[string]string{"X-Request-ID": "abc-1"})
	if rec.Code != 409 || detailCode(out) != "SLOT_TAKEN" || rec.Header().Get("X-Request-ID") != "abc-1" {
		t.Fatal(rec.Code, out, rec.Header())
	}
	if rec, _ := call(h, "GET", "/health/live", "", map[string]string{"X-Request-ID": "bad id with spaces"}); rec.Header().Get("X-Request-ID") == "bad id with spaces" {
		t.Fatal("invalid request id accepted")
	}
	for path, want := range map[string]string{"/nope": "HTTP_404", "/boom": "INTERNAL_ERROR", "/panic": "INTERNAL_ERROR"} {
		_, out := call(h, "GET", path, "", nil)
		if detailCode(out) != want {
			t.Errorf("%s: %v", path, out)
		}
	}
	rec, out = call(h, "POST", "/v1/things", `{"count": 1, "extra": 2}`, nil)
	if rec.Code != 422 || detailCode(out) != "VALIDATION_ERROR" {
		t.Fatal(rec.Code, out)
	}
	if rec, _ := call(h, "POST", "/v1/things", `{"count": 1}`, nil); rec.Code != 201 {
		t.Fatal(rec.Code)
	}
}

func TestMetricsRouteTemplate(t *testing.T) {
	h := router(nil)
	call(h, "GET", "/v1/things/42", "", nil)
	req := httptest.NewRequest("GET", "/metrics", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	body, _ := io.ReadAll(rec.Body)
	if !strings.Contains(string(body), `route="/v1/things/{id}"`) {
		t.Fatal("route template label missing")
	}
}

package httpclient_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/circuit"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpclient"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/reqctx"
)

func server(t *testing.T, statuses ...int) (*httptest.Server, *int32, *http.Header) {
	t.Helper()
	var n int32
	var last http.Header
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		i := atomic.AddInt32(&n, 1) - 1
		last = r.Header.Clone()
		status := 200
		if int(i) < len(statuses) {
			status = statuses[i]
		}
		w.WriteHeader(status)
		if status >= 400 {
			_ = json.NewEncoder(w).Encode(map[string]any{"detail": map[string]string{"code": "SOMETHING", "message": "m"}})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]bool{"ok": true})
	}))
	t.Cleanup(srv.Close)
	return srv, &n, &last
}

func client(url string) *httpclient.Client {
	c := httpclient.New("catalog-service", url, "schedule-service", "tok")
	c.BackoffBase, c.BackoffMax = time.Millisecond, time.Millisecond
	return c
}

func TestHeadersAndJSON(t *testing.T) {
	srv, _, hdr := server(t)
	var out map[string]bool
	ctx := reqctx.WithRequestID(context.Background(), "req-1")
	if err := client(srv.URL).Do(ctx, httpclient.Request{Method: "GET", Path: "/x", Out: &out}); err != nil || !out["ok"] {
		t.Fatal(err, out)
	}
	if hdr.Get("X-Internal-Token") != "tok" || hdr.Get("X-Caller") != "schedule-service" || hdr.Get("X-Request-ID") != "req-1" {
		t.Fatal(*hdr)
	}
}

func TestRetryPolicy(t *testing.T) {
	srv, n, _ := server(t, 503, 503, 200)
	if err := client(srv.URL).Do(context.Background(), httpclient.Request{Method: "GET", Path: "/x"}); err != nil || *n != 3 {
		t.Fatal(err, *n)
	}
	srv, n, _ = server(t, 503, 200)
	err := client(srv.URL).Do(context.Background(), httpclient.Request{Method: "POST", Path: "/x", Body: map[string]int{"a": 1}})
	if !httpclient.IsUnavailable(err) || *n != 1 {
		t.Fatal("POST without idempotency key must not retry", err, *n)
	}
	srv, n, _ = server(t, 503, 200)
	if err := client(srv.URL).Do(context.Background(), httpclient.Request{Method: "POST", Path: "/x", IdempotencyKey: "k"}); err != nil || *n != 2 {
		t.Fatal(err, *n)
	}
}

func TestUpstreamErrorMapsTo502(t *testing.T) {
	srv, n, _ := server(t, 404)
	err := client(srv.URL).Do(context.Background(), httpclient.Request{Method: "GET", Path: "/x"})
	var ue *httpclient.UpstreamError
	if !errors.As(err, &ue) || ue.Status != 404 || ue.Code != "SOMETHING" || *n != 1 {
		t.Fatal(err, *n)
	}
	var he *httpapi.Error
	if !errors.As(err, &he) || he.Status != 502 {
		t.Fatal("should surface as 502", he)
	}
}

func TestCircuitOpens(t *testing.T) {
	srv, n, _ := server(t, 500, 500, 500, 500)
	c := client(srv.URL)
	c.MaxAttempts = 1
	c.Breaker = &circuit.Breaker{Name: "catalog-service", FailureThreshold: 2, RecoveryTimeout: time.Minute}
	for range 2 {
		_ = c.Do(context.Background(), httpclient.Request{Method: "GET", Path: "/x"})
	}
	err := c.Do(context.Background(), httpclient.Request{Method: "GET", Path: "/x"})
	if !httpclient.IsUnavailable(err) || *n != 2 {
		t.Fatal("circuit should short-circuit", err, *n)
	}
}

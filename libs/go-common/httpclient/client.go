// Package httpclient — servislararo HTTP client standarti (BLUEPRINT 7.2): timeout, retry
// (faqat idempotent), exponential backoff + jitter, circuit breaker, header'lar avtomatik.
package httpclient

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math/rand/v2"
	"net"
	"net/http"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/circuit"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/reqctx"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/telemetry"
)

var (
	upstreamRequests = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "upstream_requests_total", Help: "Boshqa servislarga chiqqan so'rovlar",
	}, []string{"target", "method", "outcome"})
	circuitOpen = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "circuit_breaker_open", Help: "Circuit breaker ochiq (1) yoki yopiq (0)",
	}, []string{"target"})
)

// ErrUnavailable — target javob bermadi yoki circuit ochiq (chaqiruvchiga 503).
var ErrUnavailable = httpapi.Unavailable("UPSTREAM_UNAVAILABLE", "Bog'liq servis vaqtincha mavjud emas")

// UpstreamError — target xato javob qaytardi. Standart holatda chaqiruvchiga 502.
type UpstreamError struct {
	Target  string
	Status  int
	Code    string
	Message string
}

func (e *UpstreamError) Error() string {
	return fmt.Sprintf("upstream %s: %d %s", e.Target, e.Status, e.Code)
}

// As httpapi.Error sifatida (502) — ichki kodlar tashqariga tasodifan sizmasin.
func (e *UpstreamError) As(target any) bool {
	if t, ok := target.(**httpapi.Error); ok {
		*t = httpapi.NewError(http.StatusBadGateway, "UPSTREAM_ERROR", "Bog'liq servis xato qaytardi").
			With("target", e.Target).With("upstream_status", e.Status)
		return true
	}
	return false
}

// Client — bitta target uchun (connection pooling).
type Client struct {
	Target      string
	BaseURL     string
	Caller      string
	Token       string
	HTTP        *http.Client
	MaxAttempts int
	BackoffBase time.Duration
	BackoffMax  time.Duration
	Breaker     *circuit.Breaker
}

// New — standart: connect 2s, umumiy 5s, 3 urinish, breaker 5/30s.
func New(target, baseURL, caller, token string) *Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.DialContext = (&net.Dialer{Timeout: 2 * time.Second, KeepAlive: 30 * time.Second}).DialContext
	transport.MaxIdleConnsPerHost = 20
	b := circuit.New(target)
	b.OnStateChange = func(name string, open bool) {
		v := 0.0
		if open {
			v = 1
		}
		circuitOpen.WithLabelValues(name).Set(v)
	}
	return &Client{
		Target: target, BaseURL: baseURL, Caller: caller, Token: token,
		HTTP:        &http.Client{Timeout: 5 * time.Second, Transport: transport},
		MaxAttempts: 3, BackoffBase: 100 * time.Millisecond, BackoffMax: 2 * time.Second,
		Breaker: b,
	}
}

// Request — so'rov parametrlari.
type Request struct {
	Method         string
	Path           string
	Body           any
	IdempotencyKey string
	Out            any // javob JSON shu yerga
}

func (c *Client) delay(attempt int) time.Duration {
	cap := c.BackoffBase << (attempt - 1)
	if cap > c.BackoffMax || cap <= 0 {
		cap = c.BackoffMax
	}
	if cap <= 0 {
		return 0
	}
	return time.Duration(rand.Int64N(int64(cap) + 1)) // full jitter
}

func retryableStatus(code int) bool {
	return code == http.StatusBadGateway || code == http.StatusServiceUnavailable || code == http.StatusGatewayTimeout
}

// Do — so'rov; 4xx/5xx → *UpstreamError, tarmoq/circuit → ErrUnavailable.
func (c *Client) Do(ctx context.Context, req Request) error {
	method := req.Method
	retryable := method == http.MethodGet || method == http.MethodHead || req.IdempotencyKey != ""
	attempts := 1
	if retryable && c.MaxAttempts > 1 {
		attempts = c.MaxAttempts
	}
	var body []byte
	if req.Body != nil {
		var err error
		if body, err = json.Marshal(req.Body); err != nil {
			return err
		}
	}
	for attempt := 1; attempt <= attempts; attempt++ {
		if !c.Breaker.Allow() {
			upstreamRequests.WithLabelValues(c.Target, method, "circuit_open").Inc()
			return ErrUnavailable.With("target", c.Target).With("reason", "circuit_open")
		}
		resp, err := c.send(ctx, method, req.Path, body, req.IdempotencyKey)
		if err != nil {
			c.Breaker.Failure()
			upstreamRequests.WithLabelValues(c.Target, method, "transport_error").Inc()
			slog.WarnContext(ctx, "upstream transport error", slog.String("target", c.Target),
				slog.String("path", req.Path), slog.Int("attempt", attempt), slog.String("error", err.Error()))
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if attempt < attempts {
				sleep(ctx, c.delay(attempt))
				continue
			}
			return ErrUnavailable.With("target", c.Target).With("reason", "transport_error")
		}
		upstreamRequests.WithLabelValues(c.Target, method, fmt.Sprint(resp.StatusCode)).Inc()
		data, _ := io.ReadAll(io.LimitReader(resp.Body, 10<<20))
		_ = resp.Body.Close()
		switch {
		case retryableStatus(resp.StatusCode):
			c.Breaker.Failure()
			if attempt < attempts {
				sleep(ctx, c.delay(attempt))
				continue
			}
			return ErrUnavailable.With("target", c.Target).With("upstream_status", resp.StatusCode)
		case resp.StatusCode >= 500:
			c.Breaker.Failure()
		default:
			c.Breaker.Success() // 4xx — servis sog'lom
		}
		if resp.StatusCode >= 400 {
			ue := &UpstreamError{Target: c.Target, Status: resp.StatusCode}
			var parsed struct {
				Detail struct {
					Code    string `json:"code"`
					Message string `json:"message"`
				} `json:"detail"`
			}
			if json.Unmarshal(data, &parsed) == nil {
				ue.Code, ue.Message = parsed.Detail.Code, parsed.Detail.Message
			}
			return ue
		}
		if req.Out != nil && len(data) > 0 {
			return json.Unmarshal(data, req.Out)
		}
		return nil
	}
	return ErrUnavailable
}

func (c *Client) send(ctx context.Context, method, path string, body []byte, idemKey string) (*http.Response, error) {
	var rdr io.Reader
	if body != nil {
		rdr = bytes.NewReader(body)
	}
	r, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, rdr)
	if err != nil {
		return nil, err
	}
	if body != nil {
		r.Header.Set("Content-Type", "application/json")
	}
	r.Header.Set("X-Caller", c.Caller)
	if c.Token != "" {
		r.Header.Set("X-Internal-Token", c.Token)
	}
	if id := reqctx.RequestID(ctx); id != "" {
		r.Header.Set("X-Request-ID", id)
	}
	if idemKey != "" {
		r.Header.Set("Idempotency-Key", idemKey)
	}
	telemetry.InjectHTTP(ctx, r.Header)
	return c.HTTP.Do(r)
}

func sleep(ctx context.Context, d time.Duration) {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
	case <-t.C:
	}
}

// IsUnavailable — ErrUnavailable turidagi xatomi.
func IsUnavailable(err error) bool {
	var e *httpapi.Error
	return errors.As(err, &e) && e.Code == ErrUnavailable.Code
}

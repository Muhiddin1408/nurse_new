// Package health — /health/live va /health/ready (BLUEPRINT 5.3).
//
// live  — jarayon tirik; tashqi bog'liqliklar TEKSHIRILMAYDI.
// ready — DB, Redis, broker; biri yiqilsa 503.
package health

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"sync"
	"time"
)

// Check — tayyorlik tekshiruvi.
type Check func(ctx context.Context) error

// CheckTimeout — har tekshiruv uchun.
const CheckTimeout = 2 * time.Second

// LiveHandler — {"status":"ok"}.
func LiveHandler(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(`{"status":"ok"}` + "\n"))
}

// ReadyHandler — tekshiruvlar parallel, har biri 2s timeout.
func ReadyHandler(checks map[string]Check) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		results := make(map[string]string, len(checks))
		var mu sync.Mutex
		var wg sync.WaitGroup
		for name, check := range checks {
			wg.Add(1)
			go func() {
				defer wg.Done()
				ctx, cancel := context.WithTimeout(r.Context(), CheckTimeout)
				defer cancel()
				res := "ok"
				if err := check(ctx); err != nil {
					res = err.Error()
					if len(res) > 200 {
						res = res[:200]
					}
				}
				mu.Lock()
				results[name] = res
				mu.Unlock()
			}()
		}
		wg.Wait()
		status, code := "ok", http.StatusOK
		for _, res := range results {
			if res != "ok" {
				status, code = "unavailable", http.StatusServiceUnavailable
			}
		}
		if code != http.StatusOK {
			slog.WarnContext(r.Context(), "readiness failed", slog.Any("checks", results))
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(code)
		_ = json.NewEncoder(w).Encode(map[string]any{"status": status, "checks": results})
	}
}

// Pinger — pgxpool.Pool, redis.Client (Ping(ctx) error ga moslashtirilgan) va h.k.
type Pinger interface {
	Ping(ctx context.Context) error
}

// PingCheck — Ping(ctx) bor har qanday klient uchun.
func PingCheck(p Pinger) Check { return p.Ping }

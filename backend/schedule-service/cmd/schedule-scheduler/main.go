// schedule-scheduler — davriy vazifalar (alohida konteyner). /metrics — :9100.
//
//   - har kuni GENERATE_HOUR:00 (Asia/Tashkent) va ishga tushganda — slot generatsiyasi (advisory lock);
//   - har EXPIRE_INTERVAL — muddati o'tgan hold'larni bo'shatish (CAS, partiyalar, slot.released);
//   - har kuni — SLOT_RETENTION_DAYS dan eski, hech qachon band qilinmagan slotlarni o'chirish.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/logging"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/config"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/domain"
)

var (
	jobRuns = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "periodic_job_runs_total", Help: "Davriy vazifa ishga tushishlari"}, []string{"job", "result"})
	jobLastSuccess = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "periodic_job_last_success_timestamp", Help: "Oxirgi muvaffaqiyatli ishga tushish (unix)"}, []string{"job"})
	expiredHolds = promauto.NewCounter(prometheus.CounterOpts{
		Name: "slot_holds_expired_total", Help: "Muddati o'tib bo'shatilgan hold'lar"})
)

func main() {
	if err := run(); err != nil {
		slog.Error("fatal", slog.String("error", err.Error()))
		os.Exit(1)
	}
}

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	logging.New(cfg.ServiceName+"-scheduler", cfg.LogLevel)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()
	a, err := app.New(ctx, cfg)
	if err != nil {
		return err
	}
	defer func() { _ = a.Close() }()
	if a.DB == nil {
		return errors.New("DATABASE_URL o'rnatilmagan")
	}
	go serveMetrics(cfg.WorkerMetricsAddr)

	done := make(chan struct{}, 3)
	go every(ctx, "generate_slots", true, nextDaily(cfg.GenerateHour), func(ctx context.Context) error {
		n, err := a.Svc.GenerateAll(ctx)
		slog.InfoContext(ctx, "slots generated", slog.Int("created", n))
		return err
	}, done)
	go every(ctx, "expire_holds", true, fixed(cfg.ExpireInterval), func(ctx context.Context) error {
		for range 50 { // partiya to'la bo'lsa — keyingi intervalni kutmasdan
			n, err := a.Svc.ExpireHolds(ctx, cfg.ExpireBatchSize)
			expiredHolds.Add(float64(n))
			if err != nil || n < cfg.ExpireBatchSize {
				return err
			}
		}
		return nil
	}, done)
	go every(ctx, "purge_slots", false, fixed(24*time.Hour), func(ctx context.Context) error {
		n, err := a.Svc.PurgeOld(ctx, cfg.SlotRetentionDays)
		slog.InfoContext(ctx, "old slots purged", slog.Int64("deleted", n))
		return err
	}, done)
	for range 3 {
		<-done
	}
	slog.Info("scheduler stopped")
	return nil
}

func fixed(d time.Duration) func(time.Time) time.Time {
	return func(t time.Time) time.Time { return t.Add(d) }
}

// nextDaily — keyingi HH:00 Asia/Tashkent.
func nextDaily(hour int) func(time.Time) time.Time {
	return func(now time.Time) time.Time {
		local := now.In(domain.LocalTZ)
		next := time.Date(local.Year(), local.Month(), local.Day(), hour, 0, 0, 0, domain.LocalTZ)
		if !next.After(local) {
			next = next.AddDate(0, 0, 1)
		}
		return next
	}
}

func every(ctx context.Context, name string, onStart bool, next func(time.Time) time.Time, job func(context.Context) error, done chan<- struct{}) {
	defer func() { done <- struct{}{} }()
	at := next(time.Now())
	if onStart {
		at = time.Now()
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Until(at)):
		}
		if err := job(ctx); err != nil {
			jobRuns.WithLabelValues(name, "error").Inc()
			slog.ErrorContext(ctx, "periodic job failed", slog.String("job", name), slog.String("error", err.Error()))
		} else {
			jobRuns.WithLabelValues(name, "ok").Inc()
			jobLastSuccess.WithLabelValues(name).SetToCurrentTime()
		}
		at = next(time.Now())
	}
}

func serveMetrics(addr string) {
	srv := &http.Server{Addr: addr, Handler: promhttp.Handler(), ReadHeaderTimeout: 5 * time.Second}
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("metrics server", slog.String("error", err.Error()))
	}
}

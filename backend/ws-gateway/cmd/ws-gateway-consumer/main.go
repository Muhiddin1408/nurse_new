// ws-gateway-consumer — event consumer (alohida konteyner, BLUEPRINT 8.5): event → Redis `rtws:<kanal>`.
// Bitta nusxa yetarli (tarqatish Redis orqali hamma API nusxasiga); ko'p nusxa ham xavfsiz (Redis dedup).
// /metrics — :9100.
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

	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/logging"

	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/config"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/eventbus"
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
	logging.New(cfg.ServiceName+"-event-consumer", cfg.LogLevel)
	if cfg.RabbitMQURL == "" {
		return errors.New("RABBITMQ_URL o'rnatilmagan")
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()
	a, err := app.New(ctx, cfg)
	if err != nil {
		return err
	}
	defer func() { _ = a.Close() }()
	if a.Redis == nil {
		return errors.New("REDIS_URL o'rnatilmagan (pub/sub va idempotentlik)")
	}
	go serveMetrics(cfg.WorkerMetricsAddr)

	c := eventbus.NewConsumer(cfg.ServiceName, cfg.RabbitMQURL, a.Redis, cfg.DedupTTL)
	c.Prefetch = cfg.ConsumerPrefetch
	eventbus.Register(c, a.Bus)
	c.RunForever(ctx)
	return nil
}

func serveMetrics(addr string) {
	srv := &http.Server{Addr: addr, Handler: promhttp.Handler(), ReadHeaderTimeout: 5 * time.Second}
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("metrics server", slog.String("error", err.Error()))
	}
}

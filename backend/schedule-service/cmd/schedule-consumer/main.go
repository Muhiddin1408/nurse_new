// schedule-consumer — event consumer (alohida konteyner, BLUEPRINT 8.5). /metrics — :9100.
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

	gcbus "github.com/Muhiddin1408/nurse_new/libs/go-common/eventbus"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/logging"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/config"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/eventbus"
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
	if a.DB == nil {
		return errors.New("DATABASE_URL o'rnatilmagan")
	}
	go serveMetrics(cfg.WorkerMetricsAddr)

	c := gcbus.NewConsumer(cfg.ServiceName, cfg.RabbitMQURL, a.DB)
	c.Prefetch = cfg.ConsumerPrefetch
	eventbus.Register(c, a.Svc)
	if len(c.RoutingKeys()) == 0 {
		// Servis hech narsa iste'mol qilmaydi — konteyner tinch kutadi (restart sikliga tushmaydi).
		slog.Warn("no event handlers registered; consumer idle")
		<-ctx.Done()
		return nil
	}
	c.RunForever(ctx)
	return nil
}

func serveMetrics(addr string) {
	srv := &http.Server{Addr: addr, Handler: promhttp.Handler(), ReadHeaderTimeout: 5 * time.Second}
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("metrics server", slog.String("error", err.Error()))
	}
}

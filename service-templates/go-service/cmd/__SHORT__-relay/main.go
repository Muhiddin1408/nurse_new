// __SHORT__-relay — outbox relay (alohida konteyner, BLUEPRINT 8.4). /metrics — :9100.
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

	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/config"
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
	logging.New(cfg.ServiceName+"-outbox-relay", cfg.LogLevel)
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

	pub := gcbus.NewPublisher(cfg.RabbitMQURL, gcbus.Default)
	defer pub.Close()
	return gcbus.NewRelay(a.DB, pub).Run(ctx)
}

func serveMetrics(addr string) {
	srv := &http.Server{Addr: addr, Handler: promhttp.Handler(), ReadHeaderTimeout: 5 * time.Second}
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("metrics server", slog.String("error", err.Error()))
	}
}

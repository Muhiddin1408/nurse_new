// __SERVICE_NAME__ — HTTP server (:8080).
package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/logging"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/telemetry"

	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/config"
	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/httpapi"
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
	logging.New(cfg.ServiceName, cfg.LogLevel)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	shutdown, err := telemetry.Setup(ctx, cfg.ServiceName, cfg.Environment, cfg.OTELEndpoint)
	if err != nil {
		return err
	}
	defer func() { _ = shutdown(context.Background()) }()

	a, err := app.New(ctx, cfg)
	if err != nil {
		return err
	}
	defer func() { _ = a.Close() }()
	return gchttp.Serve(ctx, cfg.HTTPAddr, httpapi.Routes(a), cfg.ShutdownTimeout)
}

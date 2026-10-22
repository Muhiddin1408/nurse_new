// ws-gateway — WebSocket server (:8080): `/ws`, Redis `rtws:*` obunasi → shu nusxa ulanishlari.
package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/logging"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/telemetry"

	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/config"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/httpapi"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/realtime"
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

	if bus, ok := a.Bus.(*realtime.RedisBus); ok {
		go listen(ctx, bus, a.Hub)
	}
	go func() {
		// Graceful shutdown: http.Server.Shutdown hijack qilingan ulanishlarni kutmaydi — klientlarga
		// 1001 (qayta ulaning) yuboriladi; ular boshqa nusxaga ulanadi.
		<-ctx.Done()
		a.Hub.CloseAll()
	}()
	err = gchttp.Serve(ctx, cfg.HTTPAddr, httpapi.Routes(a), cfg.ShutdownTimeout)
	time.Sleep(500 * time.Millisecond) // close kadrlari yetib borsin
	return err
}

func listen(ctx context.Context, bus *realtime.RedisBus, hub *realtime.Hub) {
	backoff := time.Second
	for ctx.Err() == nil {
		if err := bus.Listen(ctx, hub, nil); err != nil && ctx.Err() == nil {
			slog.Error("redis pubsub stopped, retrying", slog.String("error", err.Error()))
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff = min(backoff*2, 30*time.Second)
	}
}

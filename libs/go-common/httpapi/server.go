package httpapi

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"time"
)

// Serve — ctx bekor qilinguncha (SIGTERM) ishlaydi, keyin graceful shutdown.
func Serve(ctx context.Context, addr string, handler http.Handler, shutdownTimeout time.Duration) error {
	srv := &http.Server{
		Addr:              addr,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
	errCh := make(chan error, 1)
	go func() {
		slog.Info("http server listening", slog.String("addr", addr))
		errCh <- srv.ListenAndServe()
	}()
	select {
	case err := <-errCh:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	case <-ctx.Done():
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
	defer cancel()
	slog.Info("http server shutting down")
	return srv.Shutdown(shutdownCtx)
}

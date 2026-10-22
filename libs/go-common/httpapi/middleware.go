package httpapi

import (
	"errors"
	"log/slog"
	"net/http"
	"regexp"
	"runtime/debug"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/reqctx"
)

var validRequestID = regexp.MustCompile(`^[A-Za-z0-9._:-]{1,128}$`)

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	if w.status == 0 {
		w.status = code
	}
	w.ResponseWriter.WriteHeader(code)
}

func (w *statusWriter) Write(b []byte) (int, error) {
	if w.status == 0 {
		w.status = http.StatusOK
	}
	return w.ResponseWriter.Write(b)
}

func (w *statusWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

// RequestContext — X-Request-ID (kelmasa yaratiladi), metrikalar va kirish logi.
func RequestContext(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Request-ID")
		if !validRequestID.MatchString(id) {
			id = uuid.NewString()
		}
		w.Header().Set("X-Request-ID", id)
		ctx := reqctx.WithRequestID(r.Context(), id)
		sw := &statusWriter{ResponseWriter: w}
		started := time.Now()
		next.ServeHTTP(sw, r.WithContext(ctx))

		status := sw.status
		if status == 0 {
			status = http.StatusOK
		}
		route := "__unmatched__"
		if rc := chi.RouteContext(ctx); rc != nil && rc.RoutePattern() != "" {
			route = rc.RoutePattern()
		}
		elapsed := time.Since(started)
		HTTPRequests.WithLabelValues(r.Method, route, strconv.Itoa(status)).Inc()
		HTTPDuration.WithLabelValues(r.Method, route).Observe(elapsed.Seconds())
		switch r.URL.Path {
		case "/health/live", "/health/ready", "/metrics":
		default:
			slog.InfoContext(ctx, r.Method+" "+r.URL.Path+" "+strconv.Itoa(status),
				slog.String("route", route), slog.Int("status", status),
				slog.Float64("duration_ms", float64(elapsed.Microseconds())/1000))
		}
	})
}

// Recoverer — panic → 500 INTERNAL_ERROR (stack logga).
func Recoverer(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				if err, ok := rec.(error); ok && errors.Is(err, http.ErrAbortHandler) {
					panic(rec)
				}
				slog.ErrorContext(r.Context(), "panic", slog.Any("panic", rec), slog.String("stack", string(debug.Stack())))
				WriteError(w, r, NewError(http.StatusInternalServerError, "INTERNAL_ERROR", "Ichki xato"))
			}
		}()
		next.ServeHTTP(w, r)
	})
}

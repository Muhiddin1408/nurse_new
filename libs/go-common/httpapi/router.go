package httpapi

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/health"
)

// NewRouter — har servis bir xil poydevor: middleware, /health/*, /metrics, 404/405 formati.
func NewRouter(checks map[string]health.Check) *chi.Mux {
	r := chi.NewRouter()
	r.Use(RequestContext, Recoverer)
	r.NotFound(func(w http.ResponseWriter, req *http.Request) {
		WriteError(w, req, NotFound("HTTP_404", "Not Found"))
	})
	r.MethodNotAllowed(func(w http.ResponseWriter, req *http.Request) {
		WriteError(w, req, NewError(http.StatusMethodNotAllowed, "HTTP_405", "Method Not Allowed"))
	})
	r.Get("/health/live", health.LiveHandler)
	r.Get("/health/ready", health.ReadyHandler(checks))
	r.Handle("/metrics", promhttp.Handler())
	return r
}

// Package httpapi — marshrutlar: /v1/__SHORT__ (user JWT), /v1/admin/__SHORT__ (admin JWT),
// /internal/v1/__SHORT__ (X-Internal-Token). Kong /internal/* ni tashqariga OCHMAYDI.
package httpapi

import (
	"net/http"

	"github.com/go-chi/chi/v5"

	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"

	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/httpapi/handlers"
)

// AllowedCallers — docs/SERVICE_REGISTRY.md dagi "Ruxsat etilgan chaqiruvchilar" bilan bir xil.
var AllowedCallers = []string{__GO_ALLOWED_CALLERS__}

// Admin permission nomlari (admin-service rollariga biriktiriladi).
const (
	PermRead   = "__SHORT__.read"
	PermManage = "__SHORT__.manage"
)

// Routes — butun HTTP handler.
func Routes(a *app.App) http.Handler {
	r := gchttp.NewRouter(a.ReadinessChecks())
	h := handlers.New(a)

	r.Route("/v1/__SHORT__", func(r chi.Router) {
		r.Use(a.Auth.RequireUser)
		r.Get("/whoami", gchttp.Handle(h.WhoAmI))
	})
	r.Route("/v1/admin/__SHORT__", func(r chi.Router) {
		r.Use(a.Auth.RequireAdmin(PermRead))
		r.Get("/whoami", gchttp.Handle(h.AdminWhoAmI))
	})
	r.Route("/internal/v1/__SHORT__", func(r chi.Router) {
		if len(AllowedCallers) == 0 {
			// Xavfsiz standart: ro'yxat bo'sh — hech kim chaqira olmaydi.
			r.Use(func(http.Handler) http.Handler {
				return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
					gchttp.WriteError(w, req, gchttp.Forbidden("CALLER_NOT_ALLOWED", "Internal API chaqiruvchilarga ochilmagan"))
				})
			})
		} else {
			r.Use(a.Auth.RequireInternal(AllowedCallers...))
		}
		r.Get("/ping", gchttp.Handle(h.Ping))
	})
	return r
}

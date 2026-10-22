// Package httpapi — marshrutlar:
//
//	/v1/schedule/*              — ommaviy (bo'sh slotlar; tokensiz)
//	/v1/doctor/schedule/*,
//	/v1/doctor/price-rules      — user JWT + doctor_id claim + tasdiqlangan shifokor (proyeksiya)
//	/v1/clinic/{clinic_id}/*    — user JWT + catalog `access` (klinika admini)
//	/internal/v1/*              — X-Internal-Token (faqat booking-service). Kong /internal/* ni OCHMAYDI.
package httpapi

import (
	"net/http"

	"github.com/go-chi/chi/v5"

	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/httpapi/handlers"
)

// AllowedCallers — docs/SERVICE_REGISTRY.md dagi "Ruxsat etilgan chaqiruvchilar" bilan bir xil.
var AllowedCallers = []string{"booking-service"}

// Routes — butun HTTP handler.
func Routes(a *app.App) http.Handler {
	r := gchttp.NewRouter(a.ReadinessChecks())
	h := handlers.New(a)

	r.Get("/v1/schedule/doctors/{doctor_id}/slots", gchttp.Handle(h.PublicSlots))

	r.Route("/v1/doctor", func(r chi.Router) {
		r.Use(a.Auth.RequireUser, h.RequireDoctor)
		r.Route("/schedule", func(r chi.Router) {
			r.Get("/rules", gchttp.Handle(h.ListRules))
			r.Post("/rules", gchttp.Handle(h.CreateRule))
			r.Patch("/rules/{rule_id}", gchttp.Handle(h.UpdateRule))
			r.Delete("/rules/{rule_id}", gchttp.Handle(h.DeleteRule))
			r.Get("/day-offs", gchttp.Handle(h.ListDayOffs))
			r.Post("/day-offs", gchttp.Handle(h.CreateDayOff))
			r.Delete("/day-offs/{id}", gchttp.Handle(h.DeleteDayOff))
			r.Get("/time-off", gchttp.Handle(h.ListTimeOffs))
			r.Post("/time-off", gchttp.Handle(h.CreateTimeOff))
			r.Delete("/time-off/{id}", gchttp.Handle(h.DeleteTimeOff))
			r.Post("/slots/{slot_id}/block", gchttp.Handle(h.BlockSlot))
			r.Post("/slots/{slot_id}/unblock", gchttp.Handle(h.UnblockSlot))
			r.Get("/calendar", gchttp.Handle(h.Calendar))
			r.Post("/regenerate", gchttp.Handle(h.Regenerate))
		})
		r.Get("/price-rules", gchttp.Handle(h.ListPriceRules))
		r.Post("/price-rules", gchttp.Handle(h.CreatePriceRule))
		r.Patch("/price-rules/{id}", gchttp.Handle(h.UpdatePriceRule))
		r.Delete("/price-rules/{id}", gchttp.Handle(h.DeletePriceRule))
	})

	r.Route("/v1/clinic/{clinic_id}", func(r chi.Router) {
		r.Use(a.Auth.RequireUser, h.RequireClinicAdmin)
		r.Get("/schedule", gchttp.Handle(h.ClinicDay))
		r.Get("/closures", gchttp.Handle(h.ListClosures))
		r.Post("/closures", gchttp.Handle(h.CreateClosure))
		r.Delete("/closures/{id}", gchttp.Handle(h.DeleteClosure))
		r.Get("/rules", gchttp.Handle(h.ClinicRules))
		r.Post("/rules/{rule_id}/room", gchttp.Handle(h.AssignRoom))
	})

	r.Route("/internal/v1", func(r chi.Router) {
		r.Use(a.Auth.RequireInternal(AllowedCallers...))
		r.Get("/slots/{slot_id}", gchttp.Handle(h.GetSlot))
		r.Post("/slots/{slot_id}/hold", gchttp.Handle(h.Hold))
		r.Post("/slots/{slot_id}/confirm", gchttp.Handle(h.Confirm))
		r.Post("/slots/{slot_id}/extend", gchttp.Handle(h.Extend))
		r.Post("/slots/{slot_id}/release", gchttp.Handle(h.Release))
		r.Get("/doctors/{doctor_id}/free-slots", gchttp.Handle(h.FreeSlots))
	})
	return r
}

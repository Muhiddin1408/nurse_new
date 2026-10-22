// Package handlers — HTTP handlerlar. Faqat so'rov/javob; biznes logika internal/service da.
package handlers

import (
	"net/http"
	"slices"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"

	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/app"
)

// Handlers — bog'liqliklar bilan.
type Handlers struct {
	app *app.App
}

// New — konstruktor.
func New(a *app.App) *Handlers { return &Handlers{app: a} }

// WhoAmI — shablon namunasi (auth zanjiri ishlayotganini ko'rsatadi). Almashtiring.
func (h *Handlers) WhoAmI(w http.ResponseWriter, r *http.Request) error {
	u, _ := auth.UserFrom(r.Context())
	roles := slices.Clone(u.Roles)
	slices.Sort(roles)
	gchttp.WriteJSON(w, http.StatusOK, map[string]any{"user_id": u.ID, "roles": roles})
	return nil
}

// AdminWhoAmI — admin namunasi.
func (h *Handlers) AdminWhoAmI(w http.ResponseWriter, r *http.Request) error {
	a, _ := auth.AdminFrom(r.Context())
	gchttp.WriteJSON(w, http.StatusOK, map[string]any{"admin_id": a.ID})
	return nil
}

// Ping — internal namunasi.
func (h *Handlers) Ping(w http.ResponseWriter, _ *http.Request) error {
	gchttp.WriteJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	return nil
}

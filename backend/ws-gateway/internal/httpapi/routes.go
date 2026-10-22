// Package httpapi — marshrutlar: `/ws` (user JWT, WebSocket) va standart `/health/*`, `/metrics`.
// Internal API yo'q (ws-gateway'ni hech kim sinxron chaqirmaydi) — `/internal/*` 403.
package httpapi

import (
	"net/http"

	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"

	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/httpapi/handlers"
)

// Routes — butun HTTP handler. `/ws` chi middleware'idan TASHQARIDA: go-common RequestContext
// ResponseWriter'ni o'raydi, WebSocket esa ulanishni hijack qiladi (va uzoq yashaydi — kirish logi/
// davomiylik metrikasi ma'nosiz). Ulanishlar metrikasi — realtime paketida.
func Routes(a *app.App) http.Handler {
	r := gchttp.NewRouter(a.ReadinessChecks())
	r.Handle("/internal/*", http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		gchttp.WriteError(w, req, gchttp.Forbidden("CALLER_NOT_ALLOWED", "Internal API chaqiruvchilarga ochilmagan"))
	}))
	mux := http.NewServeMux()
	mux.Handle("GET /ws", handlers.New(a))
	mux.Handle("/", r)
	return mux
}

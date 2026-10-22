// Package handlers — `/ws`: autentifikatsiya, upgrade va klient buyruqlari (obuna huquqi).
package handlers

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"slices"
	"strings"
	"time"

	"github.com/gorilla/websocket"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"

	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/domain"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/realtime"
)

// CloseTooMany — foydalanuvchining ulanish limiti (upgrade'dan keyingi poyga holatida).
const CloseTooMany = 4029

// WS — `/ws` handleri.
type WS struct {
	app      *app.App
	upgrader websocket.Upgrader
	limits   realtime.Limits
}

// New — konstruktor.
func New(a *app.App) *WS {
	origins := a.Config.Origins()
	cfg := a.Config
	return &WS{
		app: a,
		upgrader: websocket.Upgrader{
			ReadBufferSize: 1024, WriteBufferSize: 4096, HandshakeTimeout: 5 * time.Second,
			// Brauzer — faqat ro'yxatdagi Origin (CSWSH'dan himoya). Origin'siz (mobil ilova) — token yetarli.
			CheckOrigin: func(r *http.Request) bool {
				o := r.Header.Get("Origin")
				return o == "" || slices.Contains(origins, o)
			},
		},
		limits: realtime.Limits{
			SendBuffer: cfg.SendBuffer, MaxMessageBytes: cfg.MaxMessageBytes, WriteTimeout: cfg.WriteTimeout,
			PongWait: cfg.PongWait, PingPeriod: cfg.PingPeriod(), MaxSubscriptions: cfg.MaxSubscriptions,
		},
	}
}

func token(r *http.Request) string {
	if t := r.URL.Query().Get("token"); t != "" {
		return t
	}
	if h := r.Header.Get("Authorization"); strings.HasPrefix(h, "Bearer ") {
		return strings.TrimPrefix(h, "Bearer ")
	}
	return ""
}

func reject(w http.ResponseWriter, r *http.Request, reason string, err *gchttp.Error) {
	realtime.Rejected.WithLabelValues("connect", reason).Inc()
	gchttp.WriteError(w, r, err)
}

// ServeHTTP — `GET /ws?token=<JWT>`. Token muddati tugaganda ulanish 4001 bilan yopiladi.
func (h *WS) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if h.app.Auth == nil || h.app.Auth.User == nil {
		reject(w, r, "not_configured", gchttp.NewError(http.StatusServiceUnavailable, "AUTH_NOT_CONFIGURED", "Autentifikatsiya sozlanmagan"))
		return
	}
	raw := token(r)
	if raw == "" {
		reject(w, r, "no_token", auth.ErrUnauthorized)
		return
	}
	claims, err := h.app.Auth.User.Verify(raw)
	if err != nil {
		var e *gchttp.Error
		if !errors.As(err, &e) {
			e = auth.ErrTokenInvalid
		}
		reject(w, r, "bad_token", e)
		return
	}
	user, err := auth.UserFromClaims(claims)
	if err != nil {
		reject(w, r, "bad_token", auth.ErrTokenInvalid)
		return
	}
	if !h.app.Hub.CanAccept(user.ID) {
		reject(w, r, "too_many", gchttp.NewError(http.StatusTooManyRequests, "TOO_MANY_CONNECTIONS", "Ulanishlar soni limitdan oshdi"))
		return
	}
	var expires time.Time
	if exp, err := claims.GetExpirationTime(); err == nil && exp != nil {
		expires = exp.Time
	}
	conn, err := h.upgrader.Upgrade(w, r, nil)
	if err != nil {
		realtime.Rejected.WithLabelValues("connect", "upgrade").Inc()
		return // upgrader javobni o'zi yozgan (403 origin, 400 handshake)
	}
	c := realtime.NewClient(conn, user.ID, user.DoctorID, h.limits)
	if err := h.app.Hub.Register(c); err != nil {
		realtime.Rejected.WithLabelValues("connect", "too_many").Inc()
		_ = conn.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(CloseTooMany, "too many connections"),
			time.Now().Add(time.Second))
		_ = conn.Close()
		return
	}
	defer h.app.Hub.Unregister(c)
	slog.InfoContext(r.Context(), "ws connected", slog.String("user_id", user.ID.String()))
	c.Run(context.WithoutCancel(r.Context()), expires, h.command)
}

// command — klient buyrug'i: obuna huquqi shu yerda tekshiriladi.
func (h *WS) command(ctx context.Context, c *realtime.Client, cmd realtime.Command) realtime.Reply {
	switch cmd.Action {
	case "ping":
		return realtime.Reply{Type: "pong"}
	case "unsubscribe":
		h.app.Hub.Unsubscribe(c, cmd.Channel)
		return realtime.Reply{Type: "unsubscribed", Channel: cmd.Channel}
	case "subscribe":
	default:
		return realtime.Reply{Type: "error", Code: "BAD_COMMAND"}
	}
	fail := func(code string) realtime.Reply {
		realtime.Rejected.WithLabelValues("subscribe", strings.ToLower(code)).Inc()
		return realtime.Reply{Type: "error", Channel: cmd.Channel, Code: code}
	}
	kind, id, err := domain.ParseChannel(cmd.Channel)
	if err != nil {
		return fail("BAD_CHANNEL")
	}
	switch kind {
	case domain.KindUser:
		if id != c.UserID {
			return fail("FORBIDDEN")
		}
	case domain.KindSchedule:
		if c.DoctorID == nil || *c.DoctorID != id {
			return fail("FORBIDDEN")
		}
	case domain.KindBooking:
		cctx, cancel := context.WithTimeout(ctx, 3*time.Second)
		defer cancel()
		ok, err := h.app.Booking.CanWatch(cctx, id, c.UserID, c.DoctorID)
		if err != nil {
			slog.WarnContext(ctx, "booking authz failed", slog.String("error", err.Error()))
			return fail("UNAVAILABLE") // klient keyinroq qayta urinadi
		}
		if !ok {
			return fail("FORBIDDEN") // begona yoki mavjud emas — farqlanmaydi (A4)
		}
	}
	if !h.app.Hub.Subscribe(c, cmd.Channel, h.limits.MaxSubscriptions) {
		return fail("TOO_MANY_SUBSCRIPTIONS")
	}
	return realtime.Reply{Type: "subscribed", Channel: cmd.Channel}
}

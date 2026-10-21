// Package httpapi — HTTP qatlami: yagona xato formati, middleware, router, graceful server.
package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
)

// Error — biznes xatosi. Javob: {"detail": {"code": "...", "message": "...", ...extra}}.
type Error struct {
	Status  int
	Code    string
	Message string
	Extra   map[string]any
	Headers map[string]string
}

func (e *Error) Error() string { return fmt.Sprintf("%d %s: %s", e.Status, e.Code, e.Message) }

// With — extra maydon qo'shilgan nusxa.
func (e *Error) With(key string, value any) *Error {
	c := *e
	c.Extra = map[string]any{}
	for k, v := range e.Extra {
		c.Extra[k] = v
	}
	c.Extra[key] = value
	return &c
}

// Detail — javob tanasi uchun.
func (e *Error) Detail() map[string]any {
	d := map[string]any{"code": e.Code, "message": e.Message}
	for k, v := range e.Extra {
		d[k] = v
	}
	return d
}

// NewError — ixtiyoriy status bilan.
func NewError(status int, code, message string) *Error {
	return &Error{Status: status, Code: code, Message: message}
}

func BadRequest(code, message string) *Error { return NewError(http.StatusBadRequest, code, message) }
func NotFound(code, message string) *Error   { return NewError(http.StatusNotFound, code, message) }
func Conflict(code, message string) *Error   { return NewError(http.StatusConflict, code, message) }
func Forbidden(code, message string) *Error  { return NewError(http.StatusForbidden, code, message) }
func Unavailable(code, message string) *Error {
	return NewError(http.StatusServiceUnavailable, code, message)
}
func Unprocessable(code, message string) *Error {
	return NewError(http.StatusUnprocessableEntity, code, message)
}

// Unauthorized — WWW-Authenticate header bilan.
func Unauthorized(code, message string) *Error {
	e := NewError(http.StatusUnauthorized, code, message)
	e.Headers = map[string]string{"WWW-Authenticate": "Bearer"}
	return e
}

// WriteJSON — JSON javob.
func WriteJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if v != nil {
		_ = json.NewEncoder(w).Encode(v)
	}
}

// WriteError — *Error bo'lsa uning formati, aks holda 500 INTERNAL_ERROR (tafsilot logga, javobga emas).
func WriteError(w http.ResponseWriter, r *http.Request, err error) {
	var e *Error
	if !errors.As(err, &e) {
		slog.ErrorContext(r.Context(), "unhandled error", slog.String("error", err.Error()))
		e = NewError(http.StatusInternalServerError, "INTERNAL_ERROR", "Ichki xato")
	} else if e.Status >= 500 {
		slog.ErrorContext(r.Context(), "app error", slog.String("code", e.Code), slog.String("error", e.Message))
	}
	for k, v := range e.Headers {
		w.Header().Set(k, v)
	}
	WriteJSON(w, e.Status, map[string]any{"detail": e.Detail()})
}

// MaxBodyBytes — so'rov tanasi chegarasi.
const MaxBodyBytes = 1 << 20

// DecodeJSON — qat'iy JSON o'qish: noma'lum maydon, ortiqcha ma'lumot va katta tana — 422.
func DecodeJSON(w http.ResponseWriter, r *http.Request, dst any) error {
	r.Body = http.MaxBytesReader(w, r.Body, MaxBodyBytes)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		return Unprocessable("VALIDATION_ERROR", "Ma'lumotlar noto'g'ri").With("error", err.Error())
	}
	if err := dec.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return Unprocessable("VALIDATION_ERROR", "Tanada bitta JSON obyekt bo'lishi kerak")
	}
	return nil
}

// HandlerFunc — xato qaytaradigan handler; Handle uni http.HandlerFunc ga aylantiradi.
type HandlerFunc func(w http.ResponseWriter, r *http.Request) error

// Handle — `r.Get("/x", httpapi.Handle(h))`.
func Handle(h HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if err := h(w, r); err != nil {
			WriteError(w, r, err)
		}
	}
}

package auth

import (
	"context"
	"crypto/subtle"
	"net/http"
	"slices"
	"strings"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"
)

type ctxKey int

const (
	userKey ctxKey = iota
	adminKey
	callerKey
)

// Authenticator — servisning auth sozlamasi.
type Authenticator struct {
	User          *Verifier
	Admin         *Verifier
	InboundTokens map[string]string // caller -> token
}

func bearer(r *http.Request) (string, error) {
	h := r.Header.Get("Authorization")
	scheme, token, ok := strings.Cut(h, " ")
	if !ok || !strings.EqualFold(scheme, "bearer") || token == "" {
		return "", ErrUnauthorized
	}
	return token, nil
}

// AuthenticateUser — so'rovdan User.
func (a *Authenticator) AuthenticateUser(r *http.Request) (User, error) {
	if a.User == nil {
		return User{}, httpapi.Unavailable("AUTH_KEYS_MISSING", "Autentifikatsiya sozlanmagan")
	}
	token, err := bearer(r)
	if err != nil {
		return User{}, err
	}
	claims, err := a.User.Verify(token)
	if err != nil {
		return User{}, err
	}
	return UserFromClaims(claims)
}

// RequireUser — user JWT majburiy.
func (a *Authenticator) RequireUser(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		u, err := a.AuthenticateUser(r)
		if err != nil {
			httpapi.WriteError(w, r, err)
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), userKey, u)))
	})
}

// RequireRoles — user JWT + rollardan biri.
func (a *Authenticator) RequireRoles(roles ...string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return a.RequireUser(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if u, _ := UserFrom(r.Context()); !u.HasRole(roles...) {
				httpapi.WriteError(w, r, ErrRole)
				return
			}
			next.ServeHTTP(w, r)
		}))
	}
}

// RequireAdmin — admin JWT + permission.
func (a *Authenticator) RequireAdmin(permission string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if a.Admin == nil {
				httpapi.WriteError(w, r, httpapi.Unavailable("AUTH_KEYS_MISSING", "Autentifikatsiya sozlanmagan"))
				return
			}
			token, err := bearer(r)
			if err != nil {
				httpapi.WriteError(w, r, err)
				return
			}
			claims, err := a.Admin.Verify(token)
			if err != nil {
				httpapi.WriteError(w, r, err)
				return
			}
			admin, err := AdminFromClaims(claims)
			if err != nil {
				httpapi.WriteError(w, r, err)
				return
			}
			if !admin.Can(permission) {
				httpapi.WriteError(w, r, ErrPermission.With("permission", permission))
				return
			}
			next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), adminKey, admin)))
		})
	}
}

// CheckInternal — X-Caller + X-Internal-Token (constant-time) + ruxsat ro'yxati.
func (a *Authenticator) CheckInternal(caller, token string, allowed []string) error {
	if caller == "" || token == "" {
		return ErrInternalAuth
	}
	expected, found := a.InboundTokens[caller]
	// Token topilmasa ham solishtiramiz — javob vaqti "bunday chaqiruvchi bormi" ni oshkor qilmasin.
	valid := subtle.ConstantTimeCompare([]byte(token), []byte(expected)) == 1
	if !found || !valid {
		return ErrInternalAuth
	}
	if !slices.Contains(allowed, caller) {
		return ErrCallerDenied
	}
	return nil
}

// RequireInternal — /internal/v1/* routerlari uchun.
func (a *Authenticator) RequireInternal(allowed ...string) func(http.Handler) http.Handler {
	if len(allowed) == 0 {
		panic("RequireInternal: kamida bitta ruxsat etilgan chaqiruvchi kerak")
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			caller := r.Header.Get("X-Caller")
			if err := a.CheckInternal(caller, r.Header.Get("X-Internal-Token"), allowed); err != nil {
				httpapi.WriteError(w, r, err)
				return
			}
			next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), callerKey, caller)))
		})
	}
}

// UserFrom — RequireUser dan keyin.
func UserFrom(ctx context.Context) (User, bool) {
	u, ok := ctx.Value(userKey).(User)
	return u, ok
}

// AdminFrom — RequireAdmin dan keyin.
func AdminFrom(ctx context.Context) (Admin, bool) {
	a, ok := ctx.Value(adminKey).(Admin)
	return a, ok
}

// CallerFrom — RequireInternal dan keyin.
func CallerFrom(ctx context.Context) string {
	c, _ := ctx.Value(callerKey).(string)
	return c
}

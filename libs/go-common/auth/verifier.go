package auth

import (
	"crypto/rsa"
	"errors"
	"slices"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"
)

const algorithm = "RS256"

// Xato kodlari Python bilan bir xil.
var (
	ErrUnauthorized  = httpapi.Unauthorized("UNAUTHORIZED", "Autentifikatsiya talab qilinadi")
	ErrTokenInvalid  = httpapi.Unauthorized("TOKEN_INVALID", "Token yaroqsiz")
	ErrTokenExpired  = httpapi.Unauthorized("TOKEN_EXPIRED", "Token muddati tugagan")
	ErrWrongAudience = httpapi.Forbidden("WRONG_AUDIENCE", "Bu token ushbu endpoint uchun emas")
	ErrPermission    = httpapi.Forbidden("PERMISSION_DENIED", "Bu amal uchun ruxsat yo'q")
	ErrRole          = httpapi.Forbidden("ROLE_REQUIRED", "Bu amal uchun rol yetarli emas")
	ErrInternalAuth  = httpapi.Unauthorized("INTERNAL_AUTH_FAILED", "Ichki so'rov autentifikatsiyasi muvaffaqiyatsiz")
	ErrCallerDenied  = httpapi.Forbidden("CALLER_NOT_ALLOWED", "Bu servis ushbu endpointni chaqira olmaydi")
)

// Verifier — RS256 tokenlarni lokal tekshiradi (tarmoq chaqiruvisiz).
type Verifier struct {
	keys         map[string]*rsa.PublicKey
	issuer       string
	audience     string
	expectedType string
}

// NewVerifier — kalitlar ParsePublicKeys/LoadPublicKeys dan.
func NewVerifier(keys map[string]*rsa.PublicKey, issuer, audience string) *Verifier {
	return &Verifier{keys: keys, issuer: issuer, audience: audience, expectedType: "access"}
}

// Verify — imzo, alg, iss, aud, exp, type. Qaytaradi: claim'lar.
func (v *Verifier) Verify(token string) (jwt.MapClaims, error) {
	// Audience imzodan OLDIN: admin token user endpointiga kelsa kalit ham boshqa —
	// aniq 403 WRONG_AUDIENCE beramiz. Imzosiz claim faqat RAD ETISH uchun ishlatiladi.
	unverified := jwt.MapClaims{}
	if _, _, err := jwt.NewParser().ParseUnverified(token, unverified); err != nil {
		return nil, ErrTokenInvalid
	}
	if aud, _ := unverified.GetAudience(); !slices.Contains(aud, v.audience) {
		return nil, ErrWrongAudience
	}

	claims := jwt.MapClaims{}
	_, err := jwt.ParseWithClaims(token, claims, v.keyFunc,
		jwt.WithValidMethods([]string{algorithm}),
		jwt.WithIssuer(v.issuer),
		jwt.WithAudience(v.audience),
		jwt.WithExpirationRequired(),
		jwt.WithIssuedAt(),
		jwt.WithLeeway(10*time.Second),
	)
	switch {
	case errors.Is(err, jwt.ErrTokenExpired):
		return nil, ErrTokenExpired
	case err != nil:
		return nil, ErrTokenInvalid
	}
	if typ, _ := claims["type"].(string); typ != v.expectedType {
		return nil, ErrTokenInvalid
	}
	if sub, _ := claims.GetSubject(); sub == "" {
		return nil, ErrTokenInvalid
	}
	return claims, nil
}

func (v *Verifier) keyFunc(t *jwt.Token) (any, error) {
	if t.Method.Alg() != algorithm {
		return nil, errors.New("alg")
	}
	if kid, ok := t.Header["kid"].(string); ok {
		key, found := v.keys[kid]
		if !found {
			return nil, errors.New("unknown kid")
		}
		return key, nil
	}
	if len(v.keys) == 1 {
		for _, k := range v.keys {
			return k, nil
		}
	}
	return nil, errors.New("kid required")
}

// User — user JWT dan.
type User struct {
	ID       uuid.UUID
	Role     string
	Roles    []string
	DoctorID *uuid.UUID
	Phone    string
	TokenID  string
}

// HasRole — rollardan biri bormi.
func (u User) HasRole(roles ...string) bool {
	for _, r := range roles {
		if slices.Contains(u.Roles, r) {
			return true
		}
	}
	return false
}

// UserFromClaims — claim'larni tekshirib User qiladi.
func UserFromClaims(c jwt.MapClaims) (User, error) {
	sub, _ := c.GetSubject()
	id, err := uuid.Parse(sub)
	if err != nil {
		return User{}, ErrTokenInvalid
	}
	role, _ := c["role"].(string)
	if role == "" {
		return User{}, ErrTokenInvalid
	}
	u := User{ID: id, Role: role, Roles: stringSlice(c["roles"])}
	if len(u.Roles) == 0 {
		u.Roles = []string{role}
	}
	if d, ok := c["doctor_id"].(string); ok && d != "" {
		did, err := uuid.Parse(d)
		if err != nil {
			return User{}, ErrTokenInvalid
		}
		u.DoctorID = &did
	}
	u.Phone, _ = c["phone"].(string)
	u.TokenID, _ = c["jti"].(string)
	return u, nil
}

// Admin — admin JWT dan.
type Admin struct {
	ID          uuid.UUID
	Permissions []string
	TokenID     string
}

// Can — permission bormi ("*" — hammasi).
func (a Admin) Can(permission string) bool {
	return slices.Contains(a.Permissions, permission) || slices.Contains(a.Permissions, "*")
}

// AdminFromClaims — claim'lardan.
func AdminFromClaims(c jwt.MapClaims) (Admin, error) {
	sub, _ := c.GetSubject()
	id, err := uuid.Parse(sub)
	if err != nil {
		return Admin{}, ErrTokenInvalid
	}
	a := Admin{ID: id, Permissions: stringSlice(c["permissions"])}
	a.TokenID, _ = c["jti"].(string)
	return a, nil
}

func stringSlice(v any) []string {
	items, _ := v.([]any)
	out := make([]string, 0, len(items))
	for _, it := range items {
		if s, ok := it.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

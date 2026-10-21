// Package testutil — Go servis testlari uchun: RSA kalit, token chiqarish.
package testutil

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
)

// KeyPair — test kaliti.
type KeyPair struct {
	Private   *rsa.PrivateKey
	PublicPEM []byte
	KID       string
}

// NewKeyPair — 2048-bit RSA.
func NewKeyPair(t testing.TB) KeyPair {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	der, err := x509.MarshalPKIXPublicKey(&key.PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	kid, err := auth.KeyID(&key.PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	return KeyPair{Private: key, PublicPEM: pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}), KID: kid}
}

// Sign — ixtiyoriy claim'lar.
func (k KeyPair) Sign(t testing.TB, claims jwt.MapClaims) string {
	t.Helper()
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	tok.Header["kid"] = k.KID
	s, err := tok.SignedString(k.Private)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// UserClaims — standart user access token claim'lari.
func UserClaims(userID uuid.UUID, roles ...string) jwt.MapClaims {
	if len(roles) == 0 {
		roles = []string{"client"}
	}
	now := time.Now()
	rs := make([]any, len(roles))
	for i, r := range roles {
		rs[i] = r
	}
	return jwt.MapClaims{
		"sub": userID.String(), "role": roles[0], "roles": rs, "phone": "+998901234567",
		"iss": "medbron-auth", "aud": "medbron", "type": "access",
		"iat": now.Unix(), "exp": now.Add(15 * time.Minute).Unix(), "jti": uuid.NewString(),
	}
}

// AdminClaims — admin access token claim'lari.
func AdminClaims(adminID uuid.UUID, permissions ...string) jwt.MapClaims {
	now := time.Now()
	ps := make([]any, len(permissions))
	for i, p := range permissions {
		ps[i] = p
	}
	return jwt.MapClaims{
		"sub": adminID.String(), "permissions": ps, "iss": "medbron-admin", "aud": "admin", "type": "access",
		"iat": now.Unix(), "exp": now.Add(30 * time.Minute).Unix(), "jti": uuid.NewString(),
	}
}

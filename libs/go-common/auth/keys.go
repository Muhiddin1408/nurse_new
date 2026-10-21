// Package auth — stateless autentifikatsiya (BLUEPRINT 6): user/admin RS256 JWT va internal token.
// Python libs/py-common/security.py bilan bir xil qoidalar (kid, audience, xato kodlari).
package auth

import (
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
)

// KeyID — SPKI DER ning SHA-256 si, base64url, 16 belgi (Python key_id() bilan bir xil).
func KeyID(pub *rsa.PublicKey) (string, error) {
	der, err := x509.MarshalPKIXPublicKey(pub)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(der)
	return base64.URLEncoding.EncodeToString(sum[:])[:16], nil
}

// ParsePublicKeys — bir yoki bir nechta PEM "PUBLIC KEY" bloki (kalit rotatsiyasi).
func ParsePublicKeys(data []byte) (map[string]*rsa.PublicKey, error) {
	keys := map[string]*rsa.PublicKey{}
	for {
		var block *pem.Block
		block, data = pem.Decode(data)
		if block == nil {
			break
		}
		if block.Type != "PUBLIC KEY" {
			continue
		}
		parsed, err := x509.ParsePKIXPublicKey(block.Bytes)
		if err != nil {
			return nil, fmt.Errorf("public key: %w", err)
		}
		pub, ok := parsed.(*rsa.PublicKey)
		if !ok {
			return nil, errors.New("faqat RSA public key qo'llab-quvvatlanadi")
		}
		kid, err := KeyID(pub)
		if err != nil {
			return nil, err
		}
		keys[kid] = pub
	}
	if len(keys) == 0 {
		return nil, errors.New("PEM ichida PUBLIC KEY bloki topilmadi")
	}
	return keys, nil
}

// LoadPublicKeys — fayldan.
func LoadPublicKeys(path string) (map[string]*rsa.PublicKey, error) {
	data, err := os.ReadFile(path) //nolint:gosec // yo'l operator konfiguratsiyasidan (JWT_PUBLIC_KEY_PATH), foydalanuvchidan emas
	if err != nil {
		return nil, err
	}
	return ParsePublicKeys(data)
}

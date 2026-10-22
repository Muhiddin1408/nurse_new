// Package config — servis sozlamalari uchun asos (envconfig).
//
//	type Config struct {
//		config.Base
//		SlotCacheTTL time.Duration `envconfig:"SLOT_CACHE_TTL" default:"30s"`
//	}
//	var cfg Config
//	if err := config.Load(&cfg); err != nil { ... }
package config

import (
	"errors"
	"os"
	"strings"
	"time"

	"github.com/kelseyhightower/envconfig"
)

// Base — har servisda bo'ladigan maydonlar (Python ServiceSettings bilan bir xil nomlar).
type Base struct {
	ServiceName string `envconfig:"SERVICE_NAME" required:"true"`
	Environment string `envconfig:"ENVIRONMENT" default:"local"`
	LogLevel    string `envconfig:"LOG_LEVEL" default:"INFO"`
	HTTPAddr    string `envconfig:"HTTP_ADDR" default:":8080"`

	// BLUEPRINT 9: Docker ichida DATABASE_URL_DOCKER ustun keladi.
	DatabaseURL       string `envconfig:"DATABASE_URL"`
	DatabaseURLDocker string `envconfig:"DATABASE_URL_DOCKER"`
	RedisURL          string `envconfig:"REDIS_URL"`
	RabbitMQURL       string `envconfig:"RABBITMQ_URL"`
	ConsumerPrefetch  int    `envconfig:"CONSUMER_PREFETCH" default:"20"`

	JWTPublicKeyPath      string `envconfig:"JWT_PUBLIC_KEY_PATH" default:"/secrets/jwt-public.pem"`
	AdminJWTPublicKeyPath string `envconfig:"ADMIN_JWT_PUBLIC_KEY_PATH" default:"/secrets/admin-jwt-public.pem"`
	JWTIssuer             string `envconfig:"JWT_ISSUER" default:"medbron-auth"`
	JWTAudience           string `envconfig:"JWT_AUDIENCE" default:"medbron"`
	AdminJWTIssuer        string `envconfig:"ADMIN_JWT_ISSUER" default:"medbron-admin"`
	AdminJWTAudience      string `envconfig:"ADMIN_JWT_AUDIENCE" default:"admin"`

	// Shu servis BOSHQA servisga murojaat qilganda ko'rsatadigan token.
	InternalToken string `envconfig:"INTERNAL_TOKEN"`

	OTELEndpoint    string        `envconfig:"OTEL_EXPORTER_OTLP_ENDPOINT"`
	ShutdownTimeout time.Duration `envconfig:"SHUTDOWN_TIMEOUT" default:"15s"`
}

// EffectiveDatabaseURL — Docker ichida DATABASE_URL_DOCKER, aks holda DATABASE_URL.
func (b Base) EffectiveDatabaseURL() string {
	if b.DatabaseURLDocker != "" {
		return b.DatabaseURLDocker
	}
	return b.DatabaseURL
}

// IsProd — prod muhitmi.
func (b Base) IsProd() bool { return b.Environment == "prod" }

// Validate — umumiy talablar.
func (b Base) Validate() error {
	if b.IsProd() && b.EffectiveDatabaseURL() == "" {
		return errors.New("prod muhitida DATABASE_URL majburiy")
	}
	return nil
}

type validator interface{ Validate() error }

// Load muhit o'zgaruvchilaridan to'ldiradi va Validate() ni chaqiradi.
func Load(cfg any) error {
	if err := envconfig.Process("", cfg); err != nil {
		return err
	}
	if v, ok := cfg.(validator); ok {
		return v.Validate()
	}
	return nil
}

const internalTokenPrefix = "INTERNAL_TOKEN_"

// InboundInternalTokens — bu servisni chaqirishga ruxsat etilgan servislar tokenlari:
// INTERNAL_TOKEN_BOOKING_SERVICE=... → {"booking-service": "..."} (BLUEPRINT 6.4).
func InboundInternalTokens(environ []string) map[string]string {
	if environ == nil {
		environ = os.Environ()
	}
	tokens := map[string]string{}
	for _, kv := range environ {
		k, v, ok := strings.Cut(kv, "=")
		if !ok || v == "" || !strings.HasPrefix(strings.ToUpper(k), internalTokenPrefix) {
			continue
		}
		caller := strings.ReplaceAll(strings.ToLower(k[len(internalTokenPrefix):]), "_", "-")
		tokens[caller] = v
	}
	return tokens
}

// Package app — bog'liqliklarni yig'ish (redis, auth, hub, booking client). main'lar shu yerdan oladi.
package app

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/google/uuid"
	amqp "github.com/rabbitmq/amqp091-go"
	"github.com/redis/go-redis/v9"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
	gcconfig "github.com/Muhiddin1408/nurse_new/libs/go-common/config"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/health"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpclient"

	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/clients"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/config"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/realtime"
)

// BookingAuthz — `booking:<id>` obuna huquqi (testlarda soxtasi).
type BookingAuthz interface {
	CanWatch(ctx context.Context, bookingID, userID uuid.UUID, doctorID *uuid.UUID) (bool, error)
}

// App — servis bog'liqliklari. Postgres yo'q.
type App struct {
	Config  config.Config
	Redis   *redis.Client
	Auth    *auth.Authenticator
	Hub     *realtime.Hub
	Booking BookingAuthz
	Bus     realtime.Publisher
}

// New — konfiguratsiyadan. Redis URL bo'lmasa (lokal unit test) — LocalBus, kesh yo'q.
func New(_ context.Context, cfg config.Config) (*App, error) {
	a := &App{Config: cfg, Hub: realtime.NewHub(cfg.MaxConnsPerUser)}
	if cfg.RedisURL != "" {
		opts, err := redis.ParseURL(cfg.RedisURL)
		if err != nil {
			return nil, fmt.Errorf("redis: %w", err)
		}
		a.Redis = redis.NewClient(opts)
		a.Bus = &realtime.RedisBus{Redis: a.Redis}
	} else {
		a.Bus = &realtime.LocalBus{Hub: a.Hub}
	}
	a.Booking = &clients.Booking{
		HTTP:  httpclient.New("booking-service", cfg.BookingServiceURL, cfg.ServiceName, cfg.InternalToken),
		Redis: a.Redis,
		TTL:   cfg.AuthzCacheTTL,
	}
	a.Auth = &auth.Authenticator{InboundTokens: gcconfig.InboundInternalTokens(nil)}
	if keys, err := auth.LoadPublicKeys(cfg.JWTPublicKeyPath); err == nil {
		a.Auth.User = auth.NewVerifier(keys, cfg.JWTIssuer, cfg.JWTAudience)
	} else if cfg.IsProd() {
		return nil, fmt.Errorf("user jwt public key: %w", err)
	} else {
		slog.Warn("user jwt public key missing; /ws returns 503", slog.String("path", cfg.JWTPublicKeyPath))
	}
	return a, nil
}

// ReadinessChecks — /health/ready: Redis (pub/sub, kesh) va RabbitMQ (eventlar oqimi).
func (a *App) ReadinessChecks() map[string]health.Check {
	checks := map[string]health.Check{}
	if a.Redis != nil {
		checks["redis"] = func(ctx context.Context) error { return a.Redis.Ping(ctx).Err() }
	}
	if url := a.Config.RabbitMQURL; url != "" {
		checks["rabbitmq"] = func(context.Context) error {
			conn, err := amqp.DialConfig(url, amqp.Config{Dial: amqp.DefaultDial(2 * time.Second)})
			if err != nil {
				return err
			}
			return conn.Close()
		}
	}
	return checks
}

// Close — resurslarni yopadi.
func (a *App) Close() error {
	var errs []error
	if a.Redis != nil {
		errs = append(errs, a.Redis.Close())
	}
	return errors.Join(errs...)
}

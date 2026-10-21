// Package app — bog'liqliklarni yig'ish (pool, redis, auth). main'lar shu yerdan oladi.
package app

import (
	"context"
	"errors"
	"fmt"
	"log/slog"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
	gcconfig "github.com/Muhiddin1408/nurse_new/libs/go-common/config"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/health"

	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/config"
)

// App — servis bog'liqliklari.
type App struct {
	Config config.Config
	DB     *pgxpool.Pool
	Redis  *redis.Client
	Auth   *auth.Authenticator
}

// New — konfiguratsiyadan. DB/Redis URL bo'lmasa (lokal unit test) nil qoladi.
func New(ctx context.Context, cfg config.Config) (*App, error) {
	a := &App{Config: cfg}
	if url := cfg.EffectiveDatabaseURL(); url != "" {
		pool, err := pgxpool.New(ctx, url)
		if err != nil {
			return nil, fmt.Errorf("postgres: %w", err)
		}
		a.DB = pool
	}
	if cfg.RedisURL != "" {
		opts, err := redis.ParseURL(cfg.RedisURL)
		if err != nil {
			return nil, fmt.Errorf("redis: %w", err)
		}
		a.Redis = redis.NewClient(opts)
	}
	a.Auth = &auth.Authenticator{InboundTokens: gcconfig.InboundInternalTokens(nil)}
	if keys, err := auth.LoadPublicKeys(cfg.JWTPublicKeyPath); err == nil {
		a.Auth.User = auth.NewVerifier(keys, cfg.JWTIssuer, cfg.JWTAudience)
	} else if cfg.IsProd() {
		return nil, fmt.Errorf("user jwt public key: %w", err)
	} else {
		slog.Warn("user jwt public key missing; user endpoints return 503", slog.String("path", cfg.JWTPublicKeyPath))
	}
	if keys, err := auth.LoadPublicKeys(cfg.AdminJWTPublicKeyPath); err == nil {
		a.Auth.Admin = auth.NewVerifier(keys, cfg.AdminJWTIssuer, cfg.AdminJWTAudience)
	} else if cfg.IsProd() {
		return nil, fmt.Errorf("admin jwt public key: %w", err)
	}
	return a, nil
}

// ReadinessChecks — /health/ready uchun.
func (a *App) ReadinessChecks() map[string]health.Check {
	checks := map[string]health.Check{}
	if a.DB != nil {
		checks["db"] = a.DB.Ping
	}
	if a.Redis != nil {
		checks["redis"] = func(ctx context.Context) error { return a.Redis.Ping(ctx).Err() }
	}
	return checks
}

// Close — resurslarni yopadi.
func (a *App) Close() error {
	var errs []error
	if a.Redis != nil {
		errs = append(errs, a.Redis.Close())
	}
	if a.DB != nil {
		a.DB.Close()
	}
	return errors.Join(errs...)
}

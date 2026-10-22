package config

import (
	"testing"
)

func TestInboundTokens(t *testing.T) {
	got := InboundInternalTokens([]string{"INTERNAL_TOKEN_BOOKING_SERVICE=abc", "INTERNAL_TOKEN_WS_GATEWAY=d", "INTERNAL_TOKEN=self", "OTHER=x"})
	if got["booking-service"] != "abc" || got["ws-gateway"] != "d" || len(got) != 2 {
		t.Fatal(got)
	}
}

func TestLoad(t *testing.T) {
	t.Setenv("SERVICE_NAME", "schedule-service")
	t.Setenv("DATABASE_URL", "postgres://h/db")
	t.Setenv("DATABASE_URL_DOCKER", "postgres://d/db")
	var cfg struct{ Base }
	if err := Load(&cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.ServiceName != "schedule-service" || cfg.HTTPAddr != ":8080" || cfg.EffectiveDatabaseURL() != "postgres://d/db" {
		t.Fatal(cfg)
	}
	t.Setenv("ENVIRONMENT", "prod")
	t.Setenv("DATABASE_URL", "")
	t.Setenv("DATABASE_URL_DOCKER", "")
	var prod Base
	if err := Load(&prod); err == nil {
		t.Fatal("prod without DATABASE_URL must fail")
	}
}

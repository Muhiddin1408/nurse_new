// Package config — schedule-service sozlamalari (.env.example da hammasi izoh bilan).
package config

import (
	"time"

	gcconfig "github.com/Muhiddin1408/nurse_new/libs/go-common/config"
)

// Config — umumiy Base + servisga xos maydonlar.
type Config struct {
	gcconfig.Base
	WorkerMetricsAddr string `envconfig:"WORKER_METRICS_ADDR" default:":9100"`

	CatalogServiceURL string `envconfig:"CATALOG_SERVICE_URL" default:"http://catalog-service:8080"`
	AdminServiceURL   string `envconfig:"ADMIN_SERVICE_URL" default:"http://admin-service:8080"`

	// Scheduler jadvali: generatsiya — har kuni shu soatda (Asia/Tashkent); hold'lar — har N; purge — har kuni.
	GenerateHour      int           `envconfig:"GENERATE_HOUR" default:"2"`
	ExpireInterval    time.Duration `envconfig:"EXPIRE_INTERVAL" default:"1m"`
	ExpireBatchSize   int           `envconfig:"EXPIRE_BATCH_SIZE" default:"1000"`
	SlotRetentionDays int           `envconfig:"SLOT_RETENTION_DAYS" default:"90"`
}

// Load — muhitdan.
func Load() (Config, error) {
	var c Config
	err := gcconfig.Load(&c)
	return c, err
}

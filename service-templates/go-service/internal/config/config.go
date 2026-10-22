// Package config — __SERVICE_NAME__ sozlamalari (.env.example da hammasi izoh bilan).
package config

import gcconfig "github.com/Muhiddin1408/nurse_new/libs/go-common/config"

// Config — umumiy Base + servisga xos maydonlar.
type Config struct {
	gcconfig.Base
	WorkerMetricsAddr string `envconfig:"WORKER_METRICS_ADDR" default:":9100"`
	// Servisga xos sozlamalarni shu yerga qo'shing (va .env.example ga ham!).
}

// Load — muhitdan.
func Load() (Config, error) {
	var c Config
	err := gcconfig.Load(&c)
	return c, err
}

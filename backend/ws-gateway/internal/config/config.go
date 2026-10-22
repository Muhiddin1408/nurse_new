// Package config — ws-gateway sozlamalari (.env.example da hammasi izoh bilan).
package config

import (
	"errors"
	"strings"
	"time"

	gcconfig "github.com/Muhiddin1408/nurse_new/libs/go-common/config"
)

// Config — umumiy Base + servisga xos maydonlar. Postgres YO'Q (reestr): ws-gateway ma'lumot saqlamaydi,
// event idempotentligi — Redis (`DedupTTL`).
type Config struct {
	gcconfig.Base
	WorkerMetricsAddr string `envconfig:"WORKER_METRICS_ADDR" default:":9100"`

	BookingServiceURL string `envconfig:"BOOKING_SERVICE_URL" default:"http://booking-service:8080"`
	// Vergul bilan. Bo'sh `Origin` (mobil ilova, server klient) — har doim ruxsat; brauzer — faqat ro'yxatdagi.
	AllowedOrigins string `envconfig:"WS_ALLOWED_ORIGINS" default:"http://localhost:5173,http://127.0.0.1:5173"`
	// Bitta foydalanuvchining (barcha qurilma/tab) bir vaqtdagi ulanishlari — oshsa 429 (yangisi rad etiladi)
	MaxConnsPerUser int `envconfig:"WS_MAX_CONNS_PER_USER" default:"5"`
	// Bitta ulanishdagi `booking:<id>` / `doctor:<id>:schedule` obunalari
	MaxSubscriptions int `envconfig:"WS_MAX_SUBSCRIPTIONS" default:"50"`
	// Yuborish navbati; to'lsa — sekin klient uziladi (backpressure, boshqalarni to'xtatmaydi)
	SendBuffer      int           `envconfig:"WS_SEND_BUFFER" default:"64"`
	MaxMessageBytes int64         `envconfig:"WS_MAX_MESSAGE_BYTES" default:"4096"`
	WriteTimeout    time.Duration `envconfig:"WS_WRITE_TIMEOUT" default:"10s"`
	PongWait        time.Duration `envconfig:"WS_PONG_WAIT" default:"60s"`
	// booking `/authz` natijasi Redis'da (reestr: 60 s)
	AuthzCacheTTL time.Duration `envconfig:"AUTHZ_CACHE_TTL" default:"60s"`
	// Event takrorini (at-least-once yetkazish) shu muddat ichida tanib oladi
	DedupTTL time.Duration `envconfig:"EVENT_DEDUP_TTL" default:"24h"`
}

// PingPeriod — PongWait dan qisqa (javob kelishiga vaqt qolsin).
func (c Config) PingPeriod() time.Duration { return c.PongWait * 9 / 10 }

// Origins — ro'yxat.
func (c Config) Origins() []string {
	var out []string
	for _, o := range strings.Split(c.AllowedOrigins, ",") {
		if o = strings.TrimSpace(o); o != "" {
			out = append(out, o)
		}
	}
	return out
}

// Validate — Base talablari o'rniga (Postgres majburiy emas) servisniki.
func (c Config) Validate() error {
	if c.IsProd() && c.RedisURL == "" {
		return errors.New("prod muhitida REDIS_URL majburiy (pub/sub, idempotentlik)")
	}
	if c.MaxConnsPerUser < 1 || c.SendBuffer < 1 || c.PongWait <= 0 {
		return errors.New("WS_MAX_CONNS_PER_USER, WS_SEND_BUFFER, WS_PONG_WAIT musbat bo'lishi kerak")
	}
	return nil
}

// Load — muhitdan.
func Load() (Config, error) {
	var c Config
	err := gcconfig.Load(&c)
	return c, err
}

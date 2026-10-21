// Package clients — boshqa servislarning internal API'si (go-common httpclient: timeout, retry, circuit breaker).
//
//   - admin-service `GET /internal/v1/settings` — hold TTL, lead time, generatsiya gorizonti (Redis 60 s;
//     `admin.settings_changed` da tashlanadi; admin ishlamasa — oxirgi ma'lum qiymat, u ham yo'q bo'lsa —
//     admin-service standartlari bilan bir xil zaxira qiymatlar: slot ro'yxati admin uchun to'xtamasin).
//   - catalog-service — `access` (klinika admini, 60 s kesh), `rooms/{id}`, `doctors/{id}/bookability`.
package clients

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sync/atomic"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpclient"
)

// ScheduleSettings — admin-service `schedule` bo'limi.
type ScheduleSettings struct {
	HoldTTLMinutes      int `json:"hold_ttl_minutes"`
	MinLeadTimeMinutes  int `json:"min_lead_time_minutes"`
	GenerationDaysAhead int `json:"generation_days_ahead"`
}

// DefaultSchedule — admin-service `PlatformSettings.schedule` standartlari bilan bir xil.
var DefaultSchedule = ScheduleSettings{HoldTTLMinutes: 10, MinLeadTimeMinutes: 60, GenerationDaysAhead: 30}

const (
	settingsKey     = "admin:settings:v1"
	settingsLastKey = "admin:settings:v1:last"
	settingsTTL     = 60 * time.Second
	accessTTL       = 60 * time.Second
)

type settingsSnapshot struct {
	Version int `json:"version"`
	Values  struct {
		Schedule ScheduleSettings `json:"schedule"`
	} `json:"values"`
}

// Settings — admin sozlamalari o'qiydigan client.
type Settings struct {
	HTTP  *httpclient.Client
	Redis *redis.Client
	local atomic.Pointer[settingsSnapshot] // Redis yo'q bo'lsa oxirgi ma'lum qiymat (parallel so'rovlar)
}

// Schedule — joriy `schedule` bo'limi.
func (s *Settings) Schedule(ctx context.Context) ScheduleSettings {
	if snap := s.cached(ctx, settingsKey); snap != nil {
		return snap.Values.Schedule
	}
	var fresh settingsSnapshot
	if s.HTTP != nil {
		err := s.HTTP.Do(ctx, httpclient.Request{Method: "GET", Path: "/internal/v1/settings", Out: &fresh})
		if err == nil {
			s.store(ctx, &fresh)
			return fresh.Values.Schedule
		}
		slog.WarnContext(ctx, "admin settings unavailable", slog.String("error", err.Error()))
	}
	if last := s.cached(ctx, settingsLastKey); last != nil {
		return last.Values.Schedule
	}
	return DefaultSchedule
}

func (s *Settings) cached(ctx context.Context, key string) *settingsSnapshot {
	if s.Redis == nil {
		if key == settingsLastKey {
			return s.local.Load()
		}
		return nil
	}
	raw, err := s.Redis.Get(ctx, key).Bytes()
	if err != nil {
		return nil
	}
	var snap settingsSnapshot
	if json.Unmarshal(raw, &snap) != nil {
		return nil
	}
	return &snap
}

func (s *Settings) store(ctx context.Context, snap *settingsSnapshot) {
	s.local.Store(snap)
	if s.Redis == nil {
		return
	}
	raw, _ := json.Marshal(snap)
	_ = s.Redis.Set(ctx, settingsKey, raw, settingsTTL).Err()
	_ = s.Redis.Set(ctx, settingsLastKey, raw, 0).Err()
}

// Invalidate — `admin.settings_changed` handleridan.
func (s *Settings) Invalidate(ctx context.Context) {
	if s.Redis != nil {
		_ = s.Redis.Del(ctx, settingsKey).Err()
	}
}

// Access — resurs darajasidagi ruxsat (catalog).
type Access struct {
	Allowed   bool        `json:"allowed"`
	Role      *string     `json:"role"`
	ClinicIDs []uuid.UUID `json:"clinic_ids"`
}

// Room — klinika xonasi.
type Room struct {
	ID       uuid.UUID `json:"id"`
	ClinicID uuid.UUID `json:"clinic_id"`
	Name     string    `json:"name"`
	IsActive bool      `json:"is_active"`
}

// Bookability — shifokor sozlamalari (qoida tekshiruvi uchun).
type Bookability struct {
	DoctorID          uuid.UUID   `json:"doctor_id"`
	Status            string      `json:"status"`
	Bookable          bool        `json:"bookable"`
	AcceptsHomeVisits bool        `json:"accepts_home_visits"`
	ClinicIDs         []uuid.UUID `json:"clinic_ids"`
}

// Catalog — catalog-service client.
type Catalog struct {
	HTTP  *httpclient.Client
	Redis *redis.Client
}

// ErrNotFound — catalog 404.
var ErrNotFound = errors.New("catalog: not found")

func (c *Catalog) get(ctx context.Context, path string, out any) error {
	if c.HTTP == nil {
		return httpclient.ErrUnavailable
	}
	err := c.HTTP.Do(ctx, httpclient.Request{Method: "GET", Path: path, Out: out})
	var ue *httpclient.UpstreamError
	if errors.As(err, &ue) && ue.Status == 404 {
		return ErrNotFound
	}
	return err
}

// ClinicAccess — user shu klinikaning adminimi (60 s Redis kesh).
func (c *Catalog) ClinicAccess(ctx context.Context, userID, clinicID uuid.UUID) (bool, error) {
	key := fmt.Sprintf("catalog:access:%s:%s", userID, clinicID)
	if c.Redis != nil {
		if v, err := c.Redis.Get(ctx, key).Result(); err == nil {
			return v == "1", nil
		}
	}
	var a Access
	if err := c.get(ctx, fmt.Sprintf("/internal/v1/access?user_id=%s&clinic_id=%s", userID, clinicID), &a); err != nil {
		return false, err
	}
	ok := a.Allowed && a.Role != nil && *a.Role == "clinic_admin"
	if c.Redis != nil {
		v := "0"
		if ok {
			v = "1"
		}
		_ = c.Redis.Set(ctx, key, v, accessTTL).Err()
	}
	return ok, nil
}

// Room — xona.
func (c *Catalog) Room(ctx context.Context, id uuid.UUID) (Room, error) {
	var r Room
	err := c.get(ctx, "/internal/v1/rooms/"+id.String(), &r)
	return r, err
}

// Bookability — shifokor.
func (c *Catalog) Bookability(ctx context.Context, doctorID uuid.UUID) (Bookability, error) {
	var b Bookability
	err := c.get(ctx, "/internal/v1/doctors/"+doctorID.String()+"/bookability", &b)
	return b, err
}

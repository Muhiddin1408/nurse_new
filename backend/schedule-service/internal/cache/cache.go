// Package cache — ommaviy slot ro'yxati uchun Redis cache-aside (kalit `slots:v1:<doctor>:<sana>`, 30 s) va
// stampede qulfi: kesh bo'sh bo'lganda faqat BITTA so'rov bazaga boradi, qolganlari qisqa kutib keshdan o'qiydi.
package cache

import (
	"context"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/domain"
)

// TTL — kesh muddati (invalidatsiya yo'qolsa ham xavfsizlik to'ri).
const (
	TTL      = 30 * time.Second
	lockTTL  = 10 * time.Second
	waitStep = 50 * time.Millisecond
	waitMax  = 6
)

// Slots — nil Redis bilan ham ishlaydi (kesh o'chiq).
type Slots struct{ Redis *redis.Client }

// Key — kesh kaliti.
func Key(doctorID uuid.UUID, d domain.Date) string {
	return fmt.Sprintf("slots:v1:%s:%s", doctorID, d.Format(time.DateOnly))
}

// GetOrLoad — keshdan yoki `load` dan (natija keshga yoziladi).
func (c *Slots) GetOrLoad(ctx context.Context, key string, load func() ([]byte, error)) ([]byte, error) {
	if c == nil || c.Redis == nil {
		return load()
	}
	for attempt := 0; ; attempt++ {
		if raw, err := c.Redis.Get(ctx, key).Bytes(); err == nil {
			return raw, nil
		}
		ok, err := c.Redis.SetNX(ctx, "lock:"+key, "1", lockTTL).Result()
		if err != nil || ok || attempt >= waitMax {
			raw, lerr := load()
			if lerr == nil {
				_ = c.Redis.Set(ctx, key, raw, TTL).Err()
			}
			if ok {
				_ = c.Redis.Del(ctx, "lock:"+key).Err()
			}
			return raw, lerr
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(waitStep):
		}
	}
}

// Invalidate — shifokorning berilgan kunlari.
func (c *Slots) Invalidate(ctx context.Context, doctorID uuid.UUID, dates ...domain.Date) {
	if c == nil || c.Redis == nil || len(dates) == 0 {
		return
	}
	keys := make([]string, 0, len(dates))
	for _, d := range dates {
		keys = append(keys, Key(doctorID, d))
	}
	_ = c.Redis.Del(ctx, keys...).Err()
}

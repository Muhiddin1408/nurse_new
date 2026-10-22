// Package clients — booking-service internal API (go-common httpclient: timeout, retry, circuit breaker).
//
//   - `GET /internal/v1/bookings/{id}/authz?user_id=&doctor_id=` — `booking:<id>` kanaliga obuna huquqi
//     (mijozning o'zi yoki shu bronning shifokori). Natija Redis'da `AUTHZ_CACHE_TTL` (60 s): ruxsat ham,
//     rad ham keshlanadi — bitta klient qayta-qayta obuna so'rab booking'ni bosmaydi.
package clients

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/url"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpclient"
)

// Booking — booking-service client.
type Booking struct {
	HTTP  *httpclient.Client
	Redis *redis.Client
	TTL   time.Duration
}

func authzKey(bookingID, userID uuid.UUID, doctorID *uuid.UUID) string {
	d := "-"
	if doctorID != nil {
		d = doctorID.String()
	}
	return fmt.Sprintf("ws:authz:%s:%s:%s", bookingID, userID, d)
}

// CanWatch — shu foydalanuvchi (va, bo'lsa, uning shifokor profili) bronni kuzata oladimi.
func (b *Booking) CanWatch(ctx context.Context, bookingID, userID uuid.UUID, doctorID *uuid.UUID) (bool, error) {
	key := authzKey(bookingID, userID, doctorID)
	if b.Redis != nil {
		if v, err := b.Redis.Get(ctx, key).Result(); err == nil {
			return v == "1", nil
		} else if !errors.Is(err, redis.Nil) {
			slog.WarnContext(ctx, "authz cache read failed", slog.String("error", err.Error()))
		}
	}
	q := url.Values{"user_id": {userID.String()}}
	if doctorID != nil {
		q.Set("doctor_id", doctorID.String())
	}
	var out struct {
		Allowed bool `json:"allowed"`
	}
	path := fmt.Sprintf("/internal/v1/bookings/%s/authz?%s", bookingID, q.Encode())
	if err := b.HTTP.Do(ctx, httpclient.Request{Method: "GET", Path: path, Out: &out}); err != nil {
		return false, err
	}
	if b.Redis != nil {
		v := "0"
		if out.Allowed {
			v = "1"
		}
		if err := b.Redis.Set(ctx, key, v, b.TTL).Err(); err != nil {
			slog.WarnContext(ctx, "authz cache write failed", slog.String("error", err.Error()))
		}
	}
	return out.Allowed, nil
}

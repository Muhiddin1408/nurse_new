package realtime

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/domain"
)

// ChannelPrefix — Redis pub/sub kanallari: `rtws:<kanal>`.
const ChannelPrefix = "rtws:"

// wire — nusxalararo format: xabar + boshqaruv belgisi.
type wire struct {
	Message domain.Message `json:"m"`
	Kick    bool           `json:"k,omitempty"`
}

// Publisher — consumer'dan (yoki testdan) kanallarga yuborish.
type Publisher interface {
	Publish(ctx context.Context, d domain.Delivery) error
}

// RedisBus — Redis pub/sub. Publish — consumer konteynerida, Listen — har API nusxasida.
type RedisBus struct {
	Redis *redis.Client
}

// Publish — har kanalga bitta PUBLISH (pipeline). Xato — consumer retry qiladi.
func (b *RedisBus) Publish(ctx context.Context, d domain.Delivery) error {
	payload, err := json.Marshal(wire{Message: d.Message, Kick: d.Message.Type == domain.TypeKick})
	if err != nil {
		return err
	}
	pipe := b.Redis.Pipeline()
	for _, ch := range d.Channels {
		pipe.Publish(ctx, ChannelPrefix+ch, payload)
	}
	if _, err := pipe.Exec(ctx); err != nil {
		published.WithLabelValues("error").Inc()
		return fmt.Errorf("redis publish: %w", err)
	}
	published.WithLabelValues("ok").Add(float64(len(d.Channels)))
	return nil
}

// Listen — `rtws:*` obunasi → Hub. ctx tugaguncha; uzilsa qayta ulanadi (go-redis o'zi).
// `ready` — obuna tasdiqlanganda yopiladi (testlar va readiness uchun).
func (b *RedisBus) Listen(ctx context.Context, hub *Hub, ready chan<- struct{}) error {
	sub := b.Redis.PSubscribe(ctx, ChannelPrefix+"*")
	defer func() { _ = sub.Close() }()
	if _, err := sub.Receive(ctx); err != nil {
		return fmt.Errorf("redis psubscribe: %w", err)
	}
	if ready != nil {
		close(ready)
	}
	ch := sub.Channel(redis.WithChannelHealthCheckInterval(30 * time.Second))
	for {
		select {
		case <-ctx.Done():
			return nil
		case m, ok := <-ch:
			if !ok {
				return nil
			}
			dispatch(hub, strings.TrimPrefix(m.Channel, ChannelPrefix), []byte(m.Payload))
		}
	}
}

func dispatch(hub *Hub, channel string, raw []byte) {
	var w wire
	if err := json.Unmarshal(raw, &w); err != nil {
		slog.Warn("bad bus payload", slog.String("channel", channel), slog.String("error", err.Error()))
		return
	}
	out, err := json.Marshal(w.Message)
	if err != nil {
		return
	}
	delivered.Add(float64(hub.Deliver(channel, out, w.Kick)))
}

// LocalBus — Redis'siz (lokal unit test, bitta jarayon): to'g'ridan-to'g'ri Hub'ga.
type LocalBus struct {
	Hub *Hub
}

// Publish — sinxron tarqatish.
func (b *LocalBus) Publish(_ context.Context, d domain.Delivery) error {
	payload, err := json.Marshal(wire{Message: d.Message, Kick: d.Message.Type == domain.TypeKick})
	if err != nil {
		return err
	}
	for _, ch := range d.Channels {
		dispatch(b.Hub, ch, payload)
	}
	return nil
}

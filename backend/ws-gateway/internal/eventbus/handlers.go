package eventbus

import (
	"context"

	gcbus "github.com/Muhiddin1408/nurse_new/libs/go-common/eventbus"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/events"

	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/domain"
	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/realtime"
)

// Register — katalogdagi HAR consume uchun handler (scripts/check-contracts.py tekshiradi). Hammasi bir xil:
// event → kanallar + minimal xabar (domain.Route) → Redis pub/sub. Yo'naltirib bo'lmaydigan event (ID yo'q)
// — doimiy xato (DLQ); pub/sub xatosi — retry.
func Register(c *Consumer, pub realtime.Publisher) {
	h := forward(pub)
	c.MustOn(events.UserDeactivated, h) // ulanishlar yopiladi (domain.TypeKick)
	c.MustOn(events.ScheduleAvailabilityChanged, h)
	c.MustOn(events.BookingCreated, h)
	c.MustOn(events.BookingConfirmed, h)
	c.MustOn(events.BookingCancelled, h)
	c.MustOn(events.BookingExpired, h)
	c.MustOn(events.BookingRescheduled, h)
	c.MustOn(events.BookingStarted, h)
	c.MustOn(events.BookingCompleted, h)
	c.MustOn(events.BookingNoShow, h)
	c.MustOn(events.BookingConfirmationFailed, h)
	c.MustOn(events.WaitlistSlotOffered, h)
	c.MustOn(events.PaymentCaptured, h) // UI: "To'lov tekshirilmoqda" → keyin booking.confirmed
	c.MustOn(events.PaymentFailed, h)
}

func forward(pub realtime.Publisher) Handler {
	return func(ctx context.Context, ev gcbus.Envelope) error {
		d, err := domain.Route(ev.EventType, ev.OccurredAt, ev.Data)
		if err != nil {
			return gcbus.Permanent(err)
		}
		return pub.Publish(ctx, d)
	}
}

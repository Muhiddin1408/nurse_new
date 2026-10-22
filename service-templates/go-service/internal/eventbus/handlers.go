// Package eventbus — event handlerlar. Har handler (ctx, tx, event): biznes o'zgarishi va
// idempotentlik belgisi BITTA tranzaksiyada (consumer o'zi ochadi va commit qiladi).
//
//	c.MustOn(events.BookingCancelled, func(ctx context.Context, tx pgx.Tx, ev gcbus.Envelope) error {
//		var d BookingCancelled
//		if err := ev.Decode(&d); err != nil {
//			return gcbus.Permanent(err)
//		}
//		return svc.ReleaseSlot(ctx, tx, d.SlotID)
//	})
//
// Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart
// (scripts/check-contracts.py tekshiradi).
package eventbus

import (
	gcbus "github.com/Muhiddin1408/nurse_new/libs/go-common/eventbus"

	"github.com/Muhiddin1408/nurse_new/backend/__SERVICE_NAME__/internal/app"
)

// Register — handlerlarni ro'yxatdan o'tkazadi.
func Register(c *gcbus.Consumer, a *app.App) {
	_ = a
	_ = c
}

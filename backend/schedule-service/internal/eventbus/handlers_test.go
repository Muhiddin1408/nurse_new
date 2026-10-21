package eventbus_test

import (
	"slices"
	"testing"

	gcbus "github.com/Muhiddin1408/nurse_new/libs/go-common/eventbus"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/events"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/eventbus"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/service"
)

// TestRegisterCoversCatalog — katalogda schedule-service consumer bo'lgan har event uchun handler bor
// (ortiqchasi ham yo'q: MustOn katalogda yo'q eventga panic qiladi).
func TestRegisterCoversCatalog(t *testing.T) {
	c := gcbus.NewConsumer("schedule-service", "amqp://unused", nil)
	svc := &service.Service{}
	eventbus.Register(c, svc)
	var want []string
	for et, spec := range events.Catalog {
		if spec.HasConsumer("schedule-service") {
			want = append(want, et)
		}
	}
	slices.Sort(want)
	if got := c.RoutingKeys(); !slices.Equal(got, want) {
		t.Fatalf("registered %v, catalog %v", got, want)
	}
	for _, et := range want {
		if eventbus.Handler(svc, et) == nil {
			t.Fatal("Handler map missing", et)
		}
	}
}

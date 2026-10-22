package circuit

import (
	"testing"
	"time"
)

func TestBreakerLifecycle(t *testing.T) {
	now := time.Unix(0, 0)
	b := &Breaker{Name: "t", FailureThreshold: 3, RecoveryTimeout: 30 * time.Second, Now: func() time.Time { return now }}
	for range 2 {
		if !b.Allow() {
			t.Fatal("closed should allow")
		}
		b.Failure()
	}
	if b.State() != Closed {
		t.Fatal("still closed")
	}
	b.Failure()
	if b.State() != Open || b.Allow() {
		t.Fatal("should be open")
	}
	now = now.Add(30 * time.Second)
	if b.State() != HalfOpen || !b.Allow() || b.Allow() {
		t.Fatal("half-open should allow exactly one probe")
	}
	b.Failure()
	if b.State() != Open {
		t.Fatal("failed probe reopens")
	}
	now = now.Add(30 * time.Second)
	b.Allow()
	b.Success()
	if b.State() != Closed || !b.Allow() {
		t.Fatal("success closes")
	}
}

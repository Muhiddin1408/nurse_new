// Package circuit — circuit breaker (BLUEPRINT 7.2): N ketma-ket xato → open → recovery'dan
// keyin half_open (bitta sinov) → muvaffaqiyat bo'lsa closed.
package circuit

import (
	"sync"
	"time"
)

// State — breaker holati.
type State string

const (
	Closed   State = "closed"
	Open     State = "open"
	HalfOpen State = "half_open"
)

// Breaker — goroutine-safe circuit breaker.
type Breaker struct {
	Name             string
	FailureThreshold int
	RecoveryTimeout  time.Duration
	Now              func() time.Time
	OnStateChange    func(name string, open bool)

	mu       sync.Mutex
	state    State
	failures int
	openedAt time.Time
	probing  bool
}

// New — standart: 5 xato, 30s.
func New(name string) *Breaker {
	return &Breaker{Name: name, FailureThreshold: 5, RecoveryTimeout: 30 * time.Second}
}

func (b *Breaker) now() time.Time {
	if b.Now != nil {
		return b.Now()
	}
	return time.Now()
}

func (b *Breaker) currentLocked() State {
	if b.state == "" {
		b.state = Closed
	}
	if b.state == Open && b.now().Sub(b.openedAt) >= b.RecoveryTimeout {
		b.state = HalfOpen
		b.probing = false
	}
	return b.state
}

// State — joriy holat.
func (b *Breaker) State() State {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.currentLocked()
}

// Allow — so'rov yuborish mumkinmi.
func (b *Breaker) Allow() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	switch b.currentLocked() {
	case Closed:
		return true
	case HalfOpen:
		if !b.probing {
			b.probing = true
			return true
		}
	}
	return false
}

// Success — muvaffaqiyatli javob.
func (b *Breaker) Success() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.failures = 0
	b.probing = false
	if b.currentLocked() != Closed {
		b.state = Closed
		b.notify(false)
	}
}

// Failure — xato.
func (b *Breaker) Failure() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.probing = false
	if b.currentLocked() == HalfOpen {
		b.openLocked()
		return
	}
	b.failures++
	threshold := b.FailureThreshold
	if threshold < 1 {
		threshold = 1
	}
	if b.failures >= threshold {
		b.openLocked()
	}
}

func (b *Breaker) openLocked() {
	b.state = Open
	b.openedAt = b.now()
	b.failures = 0
	b.notify(true)
}

func (b *Breaker) notify(open bool) {
	if b.OnStateChange != nil {
		b.OnStateChange(b.Name, open)
	}
}

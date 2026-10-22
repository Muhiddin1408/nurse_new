// Package realtime — shu nusxadagi WebSocket ulanishlari (Hub), klient sikli va nusxalararo tarqatish (Bus).
//
// Gorizontal masshtab: consumer event'ni Redis `rtws:<kanal>` ga publish qiladi, HAR API nusxasi
// `rtws:*` ga obuna va xabarni faqat O'Z ulanishlariga yuboradi. Nusxalar bir-birini bilmaydi.
package realtime

import (
	"errors"
	"sync"

	"github.com/google/uuid"

	"github.com/Muhiddin1408/nurse_new/backend/ws-gateway/internal/domain"
)

// ErrTooManyConnections — foydalanuvchining ulanish limiti (shu nusxada).
var ErrTooManyConnections = errors.New("ulanishlar limiti")

// Hub — kanal → klientlar va foydalanuvchi → klientlar indekslari.
type Hub struct {
	mu         sync.RWMutex
	channels   map[string]map[*Client]struct{}
	users      map[uuid.UUID]map[*Client]struct{}
	maxPerUser int
}

// NewHub — konstruktor.
func NewHub(maxPerUser int) *Hub {
	return &Hub{
		channels:   map[string]map[*Client]struct{}{},
		users:      map[uuid.UUID]map[*Client]struct{}{},
		maxPerUser: maxPerUser,
	}
}

// CanAccept — upgrade'dan OLDIN tekshiruv (limitda — 429). Yakuniy tekshiruv Register'da.
func (h *Hub) CanAccept(userID uuid.UUID) bool {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.users[userID]) < h.maxPerUser
}

// Register — ulanishni qo'shadi va `user:<id>` ga avtomatik obuna qiladi.
func (h *Hub) Register(c *Client) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.users[c.UserID]) >= h.maxPerUser {
		return ErrTooManyConnections
	}
	if h.users[c.UserID] == nil {
		h.users[c.UserID] = map[*Client]struct{}{}
	}
	h.users[c.UserID][c] = struct{}{}
	h.subscribeLocked(c, domain.UserChannel(c.UserID))
	connections.Inc()
	return nil
}

// Unregister — barcha obunalari bilan olib tashlaydi (idempotent).
func (h *Hub) Unregister(c *Client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if _, ok := h.users[c.UserID][c]; !ok {
		return
	}
	delete(h.users[c.UserID], c)
	if len(h.users[c.UserID]) == 0 {
		delete(h.users, c.UserID)
	}
	for ch := range c.subs {
		h.unsubscribeLocked(c, ch)
	}
	connections.Dec()
}

// Subscribe — kanalga qo'shadi. Qaytaradi: false — obunalar limiti.
func (h *Hub) Subscribe(c *Client, channel string, limit int) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	if _, already := c.subs[channel]; already {
		return true
	}
	if len(c.subs) >= limit+1 { // +1 — avtomatik user kanali
		return false
	}
	h.subscribeLocked(c, channel)
	return true
}

// Unsubscribe — kanaldan chiqaradi (user kanalidan chiqib bo'lmaydi).
func (h *Hub) Unsubscribe(c *Client, channel string) {
	if channel == domain.UserChannel(c.UserID) {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	h.unsubscribeLocked(c, channel)
}

func (h *Hub) subscribeLocked(c *Client, channel string) {
	if h.channels[channel] == nil {
		h.channels[channel] = map[*Client]struct{}{}
	}
	h.channels[channel][c] = struct{}{}
	c.subs[channel] = struct{}{}
}

func (h *Hub) unsubscribeLocked(c *Client, channel string) {
	delete(h.channels[channel], c)
	if len(h.channels[channel]) == 0 {
		delete(h.channels, channel)
	}
	delete(c.subs, channel)
}

// Deliver — kanal obunachilariga xabar. `kick` — yuborilgandan keyin ulanishlar yopiladi.
// Qaytaradi: nechta klientga navbatga qo'yildi.
func (h *Hub) Deliver(channel string, payload []byte, kick bool) int {
	h.mu.RLock()
	targets := make([]*Client, 0, len(h.channels[channel]))
	for c := range h.channels[channel] {
		targets = append(targets, c)
	}
	h.mu.RUnlock()
	for _, c := range targets {
		c.Enqueue(payload)
		if kick {
			c.Close(CloseDeactivated, "account deactivated")
		}
	}
	return len(targets)
}

// Stats — testlar va diagnostika uchun.
func (h *Hub) Stats() (users, channels int) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.users), len(h.channels)
}

// CloseAll — graceful shutdown: hamma klientga "qayta ulaning" kodi.
func (h *Hub) CloseAll() {
	h.mu.RLock()
	var all []*Client
	for _, set := range h.users {
		for c := range set {
			all = append(all, c)
		}
	}
	h.mu.RUnlock()
	for _, c := range all {
		c.Close(CloseGoingAway, "server shutting down")
	}
}

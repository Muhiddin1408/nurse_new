package realtime

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"
)

// WebSocket yopish kodlari (4000–4999 — ilova kodlari; klient shunga qarab qayta ulanadi yoki to'xtaydi).
const (
	CloseGoingAway    = websocket.CloseGoingAway // 1001: server to'xtayapti — qayta ulaning
	CloseTokenExpired = 4001                     // token muddati tugadi — yangilab qayta ulaning
	CloseDeactivated  = 4003                     // akkaunt bloklandi — qayta ulanmang
	CloseSlowConsumer = 4008                     // navbat to'ldi — qayta ulanib REST'dan holatni oling
)

// Limits — ulanish parametrlari (config'dan).
type Limits struct {
	SendBuffer       int
	MaxMessageBytes  int64
	WriteTimeout     time.Duration
	PongWait         time.Duration
	PingPeriod       time.Duration
	MaxSubscriptions int
}

// Command — klientdan keladigan buyruq.
type Command struct {
	Action  string `json:"action"` // subscribe | unsubscribe | ping
	Channel string `json:"channel"`
}

// Reply — buyruqqa javob.
type Reply struct {
	Type    string `json:"type"` // subscribed | unsubscribed | pong | error
	Channel string `json:"channel,omitempty"`
	Code    string `json:"code,omitempty"`
}

// CommandHandler — buyruqni bajaradi (obuna huquqi — httpapi/ws'da, booking authz bilan).
type CommandHandler func(ctx context.Context, c *Client, cmd Command) Reply

// Client — bitta WebSocket ulanishi.
type Client struct {
	UserID   uuid.UUID
	DoctorID *uuid.UUID

	conn   *websocket.Conn
	send   chan []byte
	limits Limits
	subs   map[string]struct{} // faqat Hub qulfi ostida

	closeOnce sync.Once
	done      chan struct{}
	mu        sync.Mutex
	closeCode int
	closeText string
}

// NewClient — ulanish (upgrade'dan keyin).
func NewClient(conn *websocket.Conn, userID uuid.UUID, doctorID *uuid.UUID, limits Limits) *Client {
	return &Client{
		UserID: userID, DoctorID: doctorID, conn: conn, limits: limits,
		send: make(chan []byte, limits.SendBuffer), subs: map[string]struct{}{}, done: make(chan struct{}),
	}
}

// Enqueue — yuborish navbatiga (bloklanmaydi). To'lsa — sekin klient uziladi: bitta klient butun
// tarqatishni (boshqa ulanishlar, Redis obunasi) to'xtatib qo'ymasligi kerak.
func (c *Client) Enqueue(payload []byte) {
	select {
	case <-c.done:
		return
	default:
	}
	select {
	case c.send <- payload:
		messagesQueued.Inc()
	default:
		disconnects.WithLabelValues("slow_consumer").Inc()
		c.Close(CloseSlowConsumer, "send buffer full")
	}
}

// Close — yopish kodi bilan (birinchi chaqiruv yutadi). Haqiqiy yopish writePump'da.
func (c *Client) Close(code int, text string) {
	c.closeOnce.Do(func() {
		c.mu.Lock()
		c.closeCode, c.closeText = code, text
		c.mu.Unlock()
		close(c.done)
	})
}

// Done — yopilganini bildiradi.
func (c *Client) Done() <-chan struct{} { return c.done }

// Run — o'qish va yozish sikllari; ulanish yopilguncha bloklaydi. `expires` — token muddati.
func (c *Client) Run(ctx context.Context, expires time.Time, handle CommandHandler) {
	writerDone := make(chan struct{})
	go func() {
		defer close(writerDone)
		c.writePump(expires)
	}()
	c.readPump(ctx, handle)
	c.Close(websocket.CloseNormalClosure, "")
	<-writerDone
}

func (c *Client) readPump(ctx context.Context, handle CommandHandler) {
	c.conn.SetReadLimit(c.limits.MaxMessageBytes)
	_ = c.conn.SetReadDeadline(time.Now().Add(c.limits.PongWait))
	c.conn.SetPongHandler(func(string) error {
		return c.conn.SetReadDeadline(time.Now().Add(c.limits.PongWait))
	})
	for {
		_, raw, err := c.conn.ReadMessage()
		if err != nil {
			if !websocket.IsCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) &&
				!errors.Is(err, websocket.ErrCloseSent) {
				disconnects.WithLabelValues("read_error").Inc()
			} else {
				disconnects.WithLabelValues("client_closed").Inc()
			}
			return
		}
		var cmd Command
		reply := Reply{Type: "error", Code: "BAD_COMMAND"}
		if json.Unmarshal(raw, &cmd) == nil {
			reply = handle(ctx, c, cmd)
		}
		if out, err := json.Marshal(reply); err == nil {
			c.Enqueue(out)
		}
		select {
		case <-c.done:
			return
		default:
		}
	}
}

func (c *Client) writePump(expires time.Time) {
	ping := time.NewTicker(c.limits.PingPeriod)
	defer ping.Stop()
	var expiry <-chan time.Time
	if !expires.IsZero() {
		t := time.NewTimer(time.Until(expires))
		defer t.Stop()
		expiry = t.C
	}
	defer func() { _ = c.conn.Close() }()
	for {
		select {
		case msg := <-c.send:
			if err := c.write(websocket.TextMessage, msg); err != nil {
				c.Close(websocket.CloseAbnormalClosure, "")
				return
			}
			messagesSent.Inc()
		case <-ping.C:
			if err := c.write(websocket.PingMessage, nil); err != nil {
				c.Close(websocket.CloseAbnormalClosure, "")
				return
			}
		case <-expiry:
			disconnects.WithLabelValues("token_expired").Inc()
			c.Close(CloseTokenExpired, "token expired")
		case <-c.done:
			// Navbatdagi xabarlar (masalan, akkaunt bloklangani haqidagi) yopishdan oldin yuboriladi
			c.drain()
			c.mu.Lock()
			code, text := c.closeCode, c.closeText
			c.mu.Unlock()
			if code != websocket.CloseAbnormalClosure {
				_ = c.write(websocket.CloseMessage, websocket.FormatCloseMessage(code, text))
			}
			return
		}
	}
}

func (c *Client) drain() {
	for {
		select {
		case msg := <-c.send:
			if c.write(websocket.TextMessage, msg) != nil {
				return
			}
			messagesSent.Inc()
		default:
			return
		}
	}
}

func (c *Client) write(kind int, data []byte) error {
	if err := c.conn.SetWriteDeadline(time.Now().Add(c.limits.WriteTimeout)); err != nil {
		return err
	}
	err := c.conn.WriteMessage(kind, data)
	if err != nil && kind != websocket.CloseMessage {
		slog.Debug("ws write failed", slog.String("error", err.Error()))
	}
	return err
}

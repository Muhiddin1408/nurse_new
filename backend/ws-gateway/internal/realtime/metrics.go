package realtime

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	connections = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "ws_connections", Help: "Shu nusxadagi ochiq WebSocket ulanishlari",
	})
	messagesQueued = promauto.NewCounter(prometheus.CounterOpts{
		Name: "ws_messages_queued_total", Help: "Klient navbatiga qo'yilgan xabarlar",
	})
	messagesSent = promauto.NewCounter(prometheus.CounterOpts{
		Name: "ws_messages_sent_total", Help: "Klientga yozilgan xabarlar",
	})
	disconnects = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "ws_disconnects_total", Help: "Uzilishlar sababi bo'yicha",
	}, []string{"reason"})
	// Rejected — ulanish/obuna rad etilishi (httpapi'dan ham ishlatiladi).
	Rejected = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "ws_rejected_total", Help: "Rad etilgan ulanish va obunalar",
	}, []string{"what", "reason"})
	published = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "ws_bus_published_total", Help: "Redis pub/sub'ga yuborilgan xabarlar",
	}, []string{"result"})
	delivered = promauto.NewCounter(prometheus.CounterOpts{
		Name: "ws_bus_delivered_total", Help: "Pub/sub'dan kelib, shu nusxa klientlariga tarqatilgan (klient soni)",
	})
)

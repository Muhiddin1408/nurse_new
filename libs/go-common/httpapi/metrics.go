package httpapi

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Metrikalar nomlari Python servislari bilan bir xil (Grafana dashboardlari umumiy).
var (
	HTTPRequests = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "http_requests_total", Help: "HTTP so'rovlar soni",
	}, []string{"method", "route", "status"})
	HTTPDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "http_request_duration_seconds",
		Help:    "HTTP so'rov davomiyligi",
		Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10},
	}, []string{"method", "route"})
)

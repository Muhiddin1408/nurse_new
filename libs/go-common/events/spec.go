// Package events — event kontraktlari: konstantalar va Catalog (catalog_gen.go, generatsiya
// qilingan) hamda contracts/events JSON Schema'lari bo'yicha validatsiya.
package events

import "slices"

// Spec — katalogdagi bitta event.
type Spec struct {
	EventType  string
	Version    int
	Publishers []string
	Consumers  []string
	Schema     string
	Aggregate  string
}

// Lookup — katalogdan.
func Lookup(eventType string) (Spec, bool) {
	s, ok := Catalog[eventType]
	return s, ok
}

// HasPublisher — servis bu eventni publish qila oladimi.
func (s Spec) HasPublisher(service string) bool { return slices.Contains(s.Publishers, service) }

// HasConsumer — servis katalogda consumer sifatida e'lon qilinganmi.
func (s Spec) HasConsumer(service string) bool { return slices.Contains(s.Consumers, service) }

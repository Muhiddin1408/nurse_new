package domain

import (
	"sort"
	"time"

	"github.com/google/uuid"
)

// Interval — [Start, End).
type Interval struct{ Start, End time.Time }

func (i Interval) overlaps(start, end time.Time) bool {
	return i.Start.Before(end) && start.Before(i.End)
}

// Candidate — yaratilishi kerak bo'lgan slot.
type Candidate struct {
	DoctorID uuid.UUID
	ClinicID *uuid.UUID
	RoomID   *uuid.UUID
	Start    time.Time
	End      time.Time
}

// ClosureKey — klinika yopiq kuni.
type ClosureKey struct {
	ClinicID uuid.UUID
	Date     Date
}

// GenInput — bitta shifokor uchun generatsiya kirishi (store yig'adi).
type GenInput struct {
	DoctorID uuid.UUID
	Rules    []Rule
	From     Date // odatda bugun (mahalliy)
	Days     int  // gorizont: From..From+Days (ikkala chet ham)
	// ClinicAllowed — klinika qoidasi uchun: faol hamkorlik + faol klinika (ADR-0011 #6).
	ClinicAllowed func(clinicID uuid.UUID) bool
	// HomeAllowed — uy chaqiruvi qoidalari (shifokor faol).
	HomeAllowed bool
	Exceptions  map[Date]bool       // shifokor dam olish kunlari
	Closures    map[ClosureKey]bool // klinika yopiq kunlari (uy qoidalariga ta'sir qilmaydi)
	// Busy — shifokorning mavjud slotlari (har qanday holat) va ta'tillari: ular bilan KESISHGAN nomzod
	// yaratilmaydi (masalan, 30→20 daqiqaga o'tganda 10:20 band 10:00–10:30 ichiga tushmasin).
	Busy []Interval
	// ExistingStarts — aynan shu boshlanishdagi mavjud slot (UNIQUE ziddiyat — o'tkazib yuboriladi).
	ExistingStarts map[time.Time]bool
	// RoomBusy — xonalarning BOSHQA shifokorlar slotlari (D8).
	RoomBusy map[uuid.UUID][]Interval
}

// Generate — qoidalardan yangi slot nomzodlari. Idempotent: mavjud slotlar takrorlanmaydi.
func Generate(in GenInput) []Candidate {
	busy := append([]Interval(nil), in.Busy...)
	var out []Candidate
	for day := 0; day <= in.Days; day++ {
		d := in.From.AddDate(0, 0, day)
		if in.Exceptions[d] {
			continue
		}
		for _, r := range in.Rules {
			if !r.ActiveOn(d) {
				continue
			}
			if r.ClinicID == nil {
				if !in.HomeAllowed {
					continue
				}
			} else if in.Closures[ClosureKey{*r.ClinicID, d}] || (in.ClinicAllowed != nil && !in.ClinicAllowed(*r.ClinicID)) {
				continue
			}
			step := time.Duration(r.SlotMinutes) * time.Minute
			end := At(d, int(r.End))
			for cur := At(d, int(r.Start)); !cur.Add(step).After(end); cur = cur.Add(step) {
				next := cur.Add(step)
				if in.ExistingStarts[cur] || overlapsAny(busy, cur, next) {
					continue
				}
				if r.RoomID != nil && overlapsAny(in.RoomBusy[*r.RoomID], cur, next) {
					continue
				}
				out = append(out, Candidate{DoctorID: in.DoctorID, ClinicID: r.ClinicID, RoomID: r.RoomID, Start: cur, End: next})
				busy = append(busy, Interval{cur, next})
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Start.Before(out[j].Start) })
	return out
}

func overlapsAny(list []Interval, start, end time.Time) bool {
	for _, iv := range list {
		if iv.overlaps(start, end) {
			return true
		}
	}
	return false
}

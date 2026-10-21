package domain

import (
	"testing"
	"time"

	"github.com/google/uuid"
)

func rule(weekday int, start, end string, minutes int, clinic *uuid.UUID) Rule {
	s, _ := ParseClock(start)
	e, _ := ParseClock(end)
	return Rule{ID: uuid.New(), Weekday: weekday, Start: s, End: e, SlotMinutes: minutes,
		ValidFrom: NewDate(2026, 1, 1), ClinicID: clinic}
}

func TestLocalTimeAndWeekday(t *testing.T) {
	// 2026-10-26 — dushanba
	d := NewDate(2026, 10, 26)
	if Weekday(d) != 0 {
		t.Fatal(Weekday(d))
	}
	at := At(d, 9*60)
	if at.Hour() != 4 || at.Location() != time.UTC {
		t.Fatalf("09:00 Toshkent = 04:00 UTC, got %v", at)
	}
	// 03:00 Toshkent — UTC bo'yicha oldingi kun
	early := At(d, 3*60)
	if early.Day() != 25 || LocalDate(early) != d {
		t.Fatal(early, LocalDate(early))
	}
	s, e := DayBounds(d)
	if e.Sub(s) != 24*time.Hour || s.Hour() != 19 {
		t.Fatal(s, e)
	}
}

func TestClock(t *testing.T) {
	c, err := ParseClock("09:30:00")
	if err != nil || c != 570 || c.String() != "09:30" {
		t.Fatal(c, err)
	}
	if _, err := ParseClock("25:00"); err == nil {
		t.Fatal("xato kutilgan")
	}
	var v Clock
	if err := v.UnmarshalJSON([]byte(`"18:15"`)); err != nil || v != 18*60+15 {
		t.Fatal(v, err)
	}
	b, _ := v.MarshalJSON()
	if string(b) != `"18:15"` {
		t.Fatal(string(b))
	}
}

func TestRuleValidateAndOverlap(t *testing.T) {
	clinic := uuid.New()
	r := rule(0, "09:00", "12:00", 30, &clinic)
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	for _, bad := range []Rule{rule(7, "09:00", "12:00", 30, nil), rule(0, "12:00", "09:00", 30, nil),
		rule(0, "09:00", "12:00", 25, nil), rule(0, "09:00", "09:20", 30, nil)} {
		if bad.Validate() == nil {
			t.Fatalf("rad etilishi kerak: %+v", bad)
		}
	}
	other := rule(0, "11:00", "14:00", 30, nil)
	if !r.Overlaps(other) {
		t.Fatal("kesishadi (klinikadan qat'i nazar)")
	}
	later := rule(0, "11:00", "14:00", 30, nil)
	to := NewDate(2026, 3, 1)
	r.ValidTo = &to
	later.ValidFrom = NewDate(2026, 4, 1)
	if r.Overlaps(later) || r.Overlaps(rule(1, "09:00", "12:00", 30, nil)) {
		t.Fatal("kesishmaydi")
	}
}

func TestGenerateBasicsExceptionsClosures(t *testing.T) {
	clinic := uuid.New()
	monday := NewDate(2026, 10, 26)
	in := GenInput{
		DoctorID: uuid.New(),
		Rules:    []Rule{rule(0, "09:00", "10:45", 30, &clinic), rule(1, "18:00", "19:00", 60, nil)},
		From:     monday, Days: 7, HomeAllowed: true,
		ClinicAllowed: func(uuid.UUID) bool { return true },
	}
	got := Generate(in)
	// dushanba: 09:00, 09:30, 10:00 (10:30–11:00 sig'maydi) ×2 (26 va 2-noyabr) + seshanba 18:00
	if len(got) != 7 {
		t.Fatalf("7 kutilgan, %d", len(got))
	}
	if got[0].Start != At(monday, 9*60) || got[0].End.Sub(got[0].Start) != 30*time.Minute {
		t.Fatal(got[0])
	}
	if got[3].ClinicID != nil {
		t.Fatal("seshanba — uy chaqiruvi")
	}

	in.Exceptions = map[Date]bool{monday: true}
	in.Closures = map[ClosureKey]bool{{clinic, monday.AddDate(0, 0, 7)}: true}
	if n := len(Generate(in)); n != 1 {
		t.Fatalf("dam olish va yopilish — faqat seshanba qoladi, %d", n)
	}
	in.Exceptions, in.Closures = nil, nil
	in.HomeAllowed = false
	in.ClinicAllowed = func(uuid.UUID) bool { return false }
	if n := len(Generate(in)); n != 0 {
		t.Fatalf("hamkorlik/uy ruxsati yo'q — slot yo'q (ADR-0011 #6), %d", n)
	}
}

func TestGenerateIdempotentAndOverlapFilter(t *testing.T) {
	monday := NewDate(2026, 10, 26)
	r := rule(0, "10:00", "11:00", 20, nil)
	booked := Interval{At(monday, 10*60), At(monday, 10*60+30)} // eski 30 daqiqalik band slot
	in := GenInput{DoctorID: uuid.New(), Rules: []Rule{r}, From: monday, Days: 0, HomeAllowed: true,
		Busy: []Interval{booked}, ExistingStarts: map[time.Time]bool{booked.Start: true}}
	got := Generate(in)
	// 10:00 — mavjud, 10:20 — band ichida, 10:40 — bo'sh
	if len(got) != 1 || got[0].Start != At(monday, 10*60+40) {
		t.Fatalf("%+v", got)
	}
	room := uuid.New()
	clinic := uuid.New()
	rr := rule(0, "10:00", "11:00", 30, &clinic)
	rr.RoomID = &room
	in = GenInput{DoctorID: uuid.New(), Rules: []Rule{rr}, From: monday, ClinicAllowed: func(uuid.UUID) bool { return true },
		RoomBusy: map[uuid.UUID][]Interval{room: {{At(monday, 10*60+15), At(monday, 10*60+45)}}}}
	if n := len(Generate(in)); n != 0 {
		t.Fatalf("xona boshqa shifokorda band (D8), %d", n)
	}
}

func TestCovers(t *testing.T) {
	clinic := uuid.New()
	monday := NewDate(2026, 10, 26)
	r := rule(0, "09:00", "12:00", 30, &clinic)
	s := At(monday, 9*60+30)
	if !r.Covers(&clinic, s, s.Add(30*time.Minute), true) {
		t.Fatal("aniq mos")
	}
	if r.Covers(&clinic, s, s.Add(20*time.Minute), true) || !r.Covers(&clinic, s, s.Add(20*time.Minute), false) {
		t.Fatal("davomiylik farq qiladi — faqat exact=false da qoplanadi")
	}
	if r.Covers(nil, s, s.Add(30*time.Minute), false) || r.Covers(&clinic, At(monday, 11*60+45), At(monday, 12*60+15), false) {
		t.Fatal("boshqa joy / oynadan tashqari")
	}
	if !CoveredByAny([]Rule{rule(1, "09:00", "10:00", 30, nil), r}, &clinic, s, s.Add(30*time.Minute), true) {
		t.Fatal("any")
	}
}

func TestPriceRules(t *testing.T) {
	mk := func(days, start, end string, pct, prio int) PriceRule {
		s, _ := ParseClock(start)
		e, _ := ParseClock(end)
		return PriceRule{Weekdays: days, Start: s, End: e, Percent: pct, Priority: prio, IsActive: true}
	}
	monday := NewDate(2026, 10, 26)
	morning := At(monday, 8*60)
	rules := []PriceRule{mk("", "07:00", "10:00", -20, 100), mk("0", "07:00", "09:00", -10, 100), mk("", "22:00", "06:00", 30, 100)}
	if p := PercentFor(rules, morning); p != -20 {
		t.Fatalf("teng priority — mijoz uchun qulayi: %d", p)
	}
	rules = append(rules, mk("0", "07:00", "09:00", 15, 50))
	if p := PercentFor(rules, morning); p != 15 {
		t.Fatalf("kichik priority ustun: %d", p)
	}
	if p := PercentFor(rules, At(monday, 23*60)); p != 30 {
		t.Fatalf("yarim tundan o'tuvchi oraliq: %d", p)
	}
	if p := PercentFor(rules, At(monday, 2*60)); p != 30 {
		t.Fatal(p)
	}
	if p := PercentFor(rules, At(monday, 12*60)); p != 0 {
		t.Fatal(p)
	}
	inactive := mk("", "00:00", "23:59", 50, 1)
	inactive.IsActive = false
	if PercentFor([]PriceRule{inactive}, morning) != 0 {
		t.Fatal("faol emas")
	}
	for _, bad := range []PriceRule{mk("", "08:00", "08:00", 10, 1), mk("7", "08:00", "09:00", 10, 1), mk("", "08:00", "09:00", 101, 1)} {
		if bad.Validate() == nil {
			t.Fatalf("rad etilishi kerak %+v", bad)
		}
	}
	if mk("012", "08:00", "09:00", -90, 5).Validate() != nil {
		t.Fatal("to'g'ri qoida")
	}
}

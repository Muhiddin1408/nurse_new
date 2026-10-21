// Package domain — schedule-service'ning sof (I/O siz) biznes qoidalari: slot generatsiyasi, qoidaga
// moslik, narx foizi (C12), vaqt zonasi. Barcha "kun" tushunchalari — Asia/Tashkent (C8).
package domain

import (
	"fmt"
	"strings"
	"time"
	_ "time/tzdata" // distroless image'da tizim tzdata yo'q — Asia/Tashkent binar ichida

	"github.com/google/uuid"
)

// LocalTZ — qoida soatlari, kun chegaralari, kesh kalitlari shu zonada (UTC+5, DST yo'q).
var LocalTZ = mustLoad("Asia/Tashkent")

func mustLoad(name string) *time.Location {
	loc, err := time.LoadLocation(name)
	if err != nil {
		panic(err)
	}
	return loc
}

// Slot holatlari.
const (
	StatusFree    = "free"
	StatusHeld    = "held"
	StatusBooked  = "booked"
	StatusBlocked = "blocked"
)

// Blok sabablari — har sabab FAQAT o'zini ochadi (boshqa sabab bilan yopilgan slotga tegilmaydi).
const (
	BlockManual            = "manual"
	BlockTimeOff           = "time_off"
	BlockDayOff            = "day_off"
	BlockClinicClosed      = "clinic_closed"
	BlockTravelBuffer      = "travel_buffer"
	BlockAffiliationPaused = "affiliation_paused"
	BlockRuleChanged       = "rule_changed"
	BlockDoctorSuspended   = "doctor_suspended"
	BlockClinicSuspended   = "clinic_suspended"
	BlockRoomBusy          = "room_busy"
)

// Joy.
const (
	PlaceClinic = "clinic"
	PlaceHome   = "home"
)

// AllowedSlotMinutes — monolit bilan bir xil (D3).
var AllowedSlotMinutes = map[int]bool{10: true, 15: true, 20: true, 30: true, 40: true, 45: true, 60: true, 90: true, 120: true}

// DefaultTravelBufferMinutes — masofa noma'lum bo'lsa (C4).
const DefaultTravelBufferMinutes = 30

// Date — kalendar kuni (vaqt qismi yo'q, UTC yarim tun). pgx DATE ni shu ko'rinishda o'qiydi.
type Date = time.Time

// NewDate — y-m-d.
func NewDate(y int, m time.Month, d int) Date { return time.Date(y, m, d, 0, 0, 0, 0, time.UTC) }

// ParseDate — "2006-01-02".
func ParseDate(s string) (Date, error) { return time.ParseInLocation(time.DateOnly, s, time.UTC) }

// LocalDate — vaqt nuqtasining Toshkentdagi sanasi.
func LocalDate(t time.Time) Date {
	l := t.In(LocalTZ)
	return NewDate(l.Year(), l.Month(), l.Day())
}

// Today — Toshkentdagi bugungi sana.
func Today(now time.Time) Date { return LocalDate(now) }

// DayBounds — mahalliy kunning [boshi, keyingi kun boshi) UTC'da.
func DayBounds(d Date) (time.Time, time.Time) {
	start := time.Date(d.Year(), d.Month(), d.Day(), 0, 0, 0, 0, LocalTZ)
	return start.UTC(), start.AddDate(0, 0, 1).UTC()
}

// At — mahalliy sana + daqiqa (yarim tundan) → UTC vaqt nuqtasi.
func At(d Date, minute int) time.Time {
	return time.Date(d.Year(), d.Month(), d.Day(), 0, minute, 0, 0, LocalTZ).UTC()
}

// Weekday — 0 = dushanba … 6 = yakshanba (Go'da yakshanba = 0).
func Weekday(d Date) int { return (int(d.Weekday()) + 6) % 7 }

// Clock — kun ichidagi daqiqa (0..1439). JSON/SQL ko'rinishi "HH:MM".
type Clock int

// ParseClock — "09:30" yoki "09:30:00".
func ParseClock(s string) (Clock, error) {
	t, err := time.Parse("15:04", s)
	if err != nil {
		t, err = time.Parse("15:04:05", s)
	}
	if err != nil {
		return 0, fmt.Errorf("vaqt formati HH:MM bo'lishi kerak: %q", s)
	}
	return Clock(t.Hour()*60 + t.Minute()), nil
}

func (c Clock) String() string { return fmt.Sprintf("%02d:%02d", int(c)/60, int(c)%60) }

// MarshalJSON — "HH:MM".
func (c Clock) MarshalJSON() ([]byte, error) { return []byte(`"` + c.String() + `"`), nil }

// UnmarshalJSON — "HH:MM".
func (c *Clock) UnmarshalJSON(b []byte) error {
	v, err := ParseClock(strings.Trim(string(b), `"`))
	if err != nil {
		return err
	}
	*c = v
	return nil
}

// Rule — haftalik ish qoidasi.
type Rule struct {
	ID          uuid.UUID  `json:"id"`
	DoctorID    uuid.UUID  `json:"doctor_id"`
	ClinicID    *uuid.UUID `json:"clinic_id"`
	RoomID      *uuid.UUID `json:"room_id"`
	Weekday     int        `json:"weekday"`
	Start       Clock      `json:"start_time"`
	End         Clock      `json:"end_time"`
	SlotMinutes int        `json:"slot_minutes"`
	ValidFrom   Date       `json:"valid_from"`
	ValidTo     *Date      `json:"valid_to"`
}

// ActiveOn — sana qoidaning amal muddati va hafta kuniga to'g'ri keladimi.
func (r Rule) ActiveOn(d Date) bool {
	if Weekday(d) != r.Weekday || d.Before(r.ValidFrom) {
		return false
	}
	return r.ValidTo == nil || !d.After(*r.ValidTo)
}

// SameClinic — ikki nullable klinika ID tengmi.
func SameClinic(a, b *uuid.UUID) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == *b
}

// Overlaps — ikki qoida bir hafta kunida vaqt va amal muddati bo'yicha kesishadimi (klinikadan qat'i nazar:
// shifokor bir vaqtda bir joyda).
func (r Rule) Overlaps(o Rule) bool {
	if r.Weekday != o.Weekday || r.Start >= o.End || o.Start >= r.End {
		return false
	}
	rEnd, oEnd := farFuture, farFuture
	if r.ValidTo != nil {
		rEnd = *r.ValidTo
	}
	if o.ValidTo != nil {
		oEnd = *o.ValidTo
	}
	return !r.ValidFrom.After(oEnd) && !o.ValidFrom.After(rEnd)
}

var farFuture = NewDate(9999, 12, 31)

// Validate — qoida maydonlari (biznes qoidalari: hamkorlik, uy chaqiruvi — service'da).
func (r Rule) Validate() error {
	switch {
	case r.Weekday < 0 || r.Weekday > 6:
		return fmt.Errorf("weekday 0..6 bo'lishi kerak")
	case r.Start >= r.End:
		return fmt.Errorf("start_time end_time dan oldin bo'lishi kerak")
	case !AllowedSlotMinutes[r.SlotMinutes]:
		return fmt.Errorf("slot_minutes ruxsat etilganlardan biri bo'lishi kerak: 10,15,20,30,40,45,60,90,120")
	case int(r.End-r.Start) < r.SlotMinutes:
		return fmt.Errorf("oraliq kamida bitta slotga yetishi kerak")
	case r.ValidTo != nil && r.ValidTo.Before(r.ValidFrom):
		return fmt.Errorf("valid_to valid_from dan oldin bo'lmasligi kerak")
	}
	return nil
}

// Covers — slot shu qoidadan chiqqanmi. exact=true: davomiylik va qadam ham mos (regeneratsiya — eski
// uzunlikdagi bo'sh slotlar o'chiriladi); exact=false: slot qoida oynasi ichida (bron saqlanadimi).
func (r Rule) Covers(clinicID *uuid.UUID, start, end time.Time, exact bool) bool {
	if !SameClinic(r.ClinicID, clinicID) {
		return false
	}
	d := LocalDate(start)
	if !r.ActiveOn(d) || LocalDate(end.Add(-time.Nanosecond)) != d {
		return false
	}
	from, to := At(d, int(r.Start)), At(d, int(r.End))
	if start.Before(from) || end.After(to) {
		return false
	}
	if !exact {
		return true
	}
	if int(end.Sub(start).Minutes()) != r.SlotMinutes {
		return false
	}
	return int(start.Sub(from).Minutes())%r.SlotMinutes == 0
}

// CoveredByAny — qoidalardan birortasi slotni qoplaydimi.
func CoveredByAny(rules []Rule, clinicID *uuid.UUID, start, end time.Time, exact bool) bool {
	for _, r := range rules {
		if r.Covers(clinicID, start, end, exact) {
			return true
		}
	}
	return false
}

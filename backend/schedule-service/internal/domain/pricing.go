package domain

import (
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
)

// PriceRule — vaqtga qarab narx foizi (C12). Chegirma EMAS: chegirmalardan oldin qo'llanadi (pricing).
type PriceRule struct {
	ID        uuid.UUID `json:"id"`
	DoctorID  uuid.UUID `json:"doctor_id"`
	Name      string    `json:"name"`
	Weekdays  string    `json:"weekdays"`
	Start     Clock     `json:"start_time"`
	End       Clock     `json:"end_time"`
	Percent   int       `json:"percent"`
	Priority  int       `json:"priority"`
	IsActive  bool      `json:"is_active"`
	CreatedAt time.Time `json:"created_at"`
}

// Validate — maydonlar.
func (p PriceRule) Validate() error {
	switch {
	case p.Percent < -90 || p.Percent > 100:
		return errString("percent −90..100 bo'lishi kerak")
	case p.Start == p.End:
		return errString("start_time va end_time teng bo'lmasligi kerak")
	case len(p.Weekdays) > 7 || strings.Trim(p.Weekdays, "0123456") != "":
		return errString("weekdays — 0..6 raqamlari (0 = dushanba), bo'sh — har kuni")
	case p.Priority < 0 || p.Priority > 1000:
		return errString("priority 0..1000")
	}
	return nil
}

type errString string

func (e errString) Error() string { return string(e) }

// Matches — mahalliy vaqt qoidaga tushadimi. [start, end); start > end — yarim tundan o'tadi.
func (p PriceRule) Matches(local time.Time) bool {
	if !p.IsActive {
		return false
	}
	if p.Weekdays != "" && !strings.Contains(p.Weekdays, strconv.Itoa((int(local.Weekday())+6)%7)) {
		return false
	}
	m := Clock(local.Hour()*60 + local.Minute())
	if p.Start < p.End {
		return m >= p.Start && m < p.End
	}
	return m >= p.Start || m < p.End
}

// PercentFor — slot boshlanishi uchun foiz: mos qoidalar ichida eng kichik `priority`; teng bo'lsa — mijoz
// uchun qulayi (eng kichik foiz). Hech biri mos kelmasa — 0. Natija [−90, 100].
func PercentFor(rules []PriceRule, start time.Time) int {
	local := start.In(LocalTZ)
	best, found := 0, false
	bestPriority := 0
	for _, r := range rules {
		if !r.Matches(local) {
			continue
		}
		switch {
		case !found || r.Priority < bestPriority:
			best, bestPriority, found = r.Percent, r.Priority, true
		case r.Priority == bestPriority && r.Percent < best:
			best = r.Percent
		}
	}
	return min(max(best, -90), 100)
}

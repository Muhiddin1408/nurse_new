package service

import (
	"context"
	"errors"
	"slices"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpclient"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/clients"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/domain"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/store"
)

// RequireActiveDoctor — doctor endpointlari uchun: proyeksiyada approved.
func (s *Service) RequireActiveDoctor(ctx context.Context, doctorID uuid.UUID) error {
	d, err := store.GetDoctor(ctx, s.Pool, doctorID)
	if isNotFound(err) || (err == nil && d.Status != "approved") {
		return ErrDoctorInactive
	}
	return err
}

// ChangeResult — jadval o'zgarishi natijasi.
type ChangeResult struct {
	Removed      int `json:"removed"`
	Created      int `json:"created"`
	KeptBookings int `json:"kept_bookings"`
}

func badRequest(err error) error { return gchttp.BadRequest("VALIDATION_ERROR", err.Error()) }

// checkRuleAllowed — catalog'dan (manba): klinika qoidasi — faol hamkorlik; uy — accepts_home_visits.
func (s *Service) checkRuleAllowed(ctx context.Context, r domain.Rule) error {
	b, err := s.Catalog.Bookability(ctx, r.DoctorID)
	if err != nil {
		if errors.Is(err, clients.ErrNotFound) {
			return ErrDoctorInactive
		}
		if httpclient.IsUnavailable(err) {
			return gchttp.Unavailable("CATALOG_UNAVAILABLE", "Katalog vaqtincha mavjud emas")
		}
		return err
	}
	if r.ClinicID == nil {
		if !b.AcceptsHomeVisits {
			return gchttp.BadRequest("HOME_VISITS_DISABLED", "Uy chaqiruvi profilingizda yoqilmagan")
		}
		return nil
	}
	if !slices.Contains(b.ClinicIDs, *r.ClinicID) {
		return gchttp.BadRequest("NO_ACTIVE_AFFILIATION", "Bu klinika bilan faol hamkorlik yo'q")
	}
	return nil
}

func checkOverlap(rules []domain.Rule, r domain.Rule) error {
	for _, o := range rules {
		if o.ID != r.ID && o.Overlaps(r) {
			return gchttp.Conflict("RULE_OVERLAP", "Bu vaqt boshqa ish qoidasi bilan kesishadi").With("rule_id", o.ID)
		}
	}
	return nil
}

// ListRules — shifokor qoidalari.
func (s *Service) ListRules(ctx context.Context, doctorID uuid.UUID) ([]domain.Rule, error) {
	return store.Rules(ctx, s.Pool, doctorID)
}

// CreateRule — yangi qoida (faol bronlarga tegmaydi) + regeneratsiya.
func (s *Service) CreateRule(ctx context.Context, r domain.Rule) (domain.Rule, ChangeResult, error) {
	r.ID, r.RoomID = uuid.New(), nil // xona — faqat klinika admini (D8)
	if err := r.Validate(); err != nil {
		return r, ChangeResult{}, badRequest(err)
	}
	if err := s.checkRuleAllowed(ctx, r); err != nil {
		return r, ChangeResult{}, err
	}
	var res ChangeResult
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		if err := lockDoctor(ctx, tx, r.DoctorID); err != nil {
			return err
		}
		existing, err := store.Rules(ctx, tx, r.DoctorID)
		if err != nil {
			return err
		}
		if err := checkOverlap(existing, r); err != nil {
			return err
		}
		if err := store.SaveRule(ctx, tx, r); err != nil {
			return err
		}
		res.Removed, res.Created, err = s.regenerate(ctx, tx, ch, r.DoctorID)
		return err
	})
	return r, res, err
}

// RulePatch — qisman o'zgartirish.
type RulePatch struct {
	ClinicID    *uuid.UUID
	ClinicSet   bool
	Weekday     *int
	Start, End  *domain.Clock
	SlotMinutes *int
	ValidFrom   *domain.Date
	ValidTo     *domain.Date
	ValidToSet  bool
}

// UpdateRule — o'zgartirish; endi qoplanmaydigan faol bronlar bo'lsa — 409 (keep_bookings bilan davom).
func (s *Service) UpdateRule(ctx context.Context, doctorID, ruleID uuid.UUID, p RulePatch, resolution string) (domain.Rule, ChangeResult, error) {
	var out domain.Rule
	var res ChangeResult
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		if err := lockDoctor(ctx, tx, doctorID); err != nil {
			return err
		}
		old, err := store.GetRule(ctx, tx, ruleID)
		if isNotFound(err) || (err == nil && old.DoctorID != doctorID) {
			return ErrRuleNotFound
		}
		if err != nil {
			return err
		}
		r := old
		if p.ClinicSet {
			r.ClinicID, r.RoomID = p.ClinicID, nil
		}
		if p.Weekday != nil {
			r.Weekday = *p.Weekday
		}
		if p.Start != nil {
			r.Start = *p.Start
		}
		if p.End != nil {
			r.End = *p.End
		}
		if p.SlotMinutes != nil {
			r.SlotMinutes = *p.SlotMinutes
		}
		if p.ValidFrom != nil {
			r.ValidFrom = *p.ValidFrom
		}
		if p.ValidToSet {
			r.ValidTo = p.ValidTo
		}
		if err := r.Validate(); err != nil {
			return badRequest(err)
		}
		if p.ClinicSet {
			if err := s.checkRuleAllowed(ctx, r); err != nil {
				return err
			}
		}
		rules, err := store.Rules(ctx, tx, doctorID)
		if err != nil {
			return err
		}
		if err := checkOverlap(rules, r); err != nil {
			return err
		}
		after := replaceRule(rules, r)
		kept, err := s.uncoveredLive(ctx, tx, doctorID, old, after, resolution)
		if err != nil {
			return err
		}
		if err := store.SaveRule(ctx, tx, r); err != nil {
			return err
		}
		res.KeptBookings = kept
		res.Removed, res.Created, err = s.regenerate(ctx, tx, ch, doctorID)
		out = r
		return err
	})
	return out, res, err
}

// DeleteRule — o'chirish; faol bronlar — 409 (keep_bookings bilan davom).
func (s *Service) DeleteRule(ctx context.Context, doctorID, ruleID uuid.UUID, resolution string) (ChangeResult, error) {
	var res ChangeResult
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		if err := lockDoctor(ctx, tx, doctorID); err != nil {
			return err
		}
		old, err := store.GetRule(ctx, tx, ruleID)
		if isNotFound(err) || (err == nil && old.DoctorID != doctorID) {
			return ErrRuleNotFound
		}
		if err != nil {
			return err
		}
		rules, err := store.Rules(ctx, tx, doctorID)
		if err != nil {
			return err
		}
		after := slices.DeleteFunc(slices.Clone(rules), func(x domain.Rule) bool { return x.ID == ruleID })
		kept, err := s.uncoveredLive(ctx, tx, doctorID, old, after, resolution)
		if err != nil {
			return err
		}
		if err := store.DeleteRule(ctx, tx, ruleID); err != nil {
			return err
		}
		res.KeptBookings = kept
		res.Removed, res.Created, err = s.regenerate(ctx, tx, ch, doctorID)
		return err
	})
	return res, err
}

func replaceRule(rules []domain.Rule, r domain.Rule) []domain.Rule {
	out := make([]domain.Rule, 0, len(rules))
	for _, x := range rules {
		if x.ID != r.ID {
			out = append(out, x)
		}
	}
	return append(out, r)
}

// uncoveredLive — eski qoida qoplagan, yangi qoidalar to'plami qoplamaydigan faol bronli slotlar (D3).
func (s *Service) uncoveredLive(ctx context.Context, tx pgx.Tx, doctorID uuid.UUID, old domain.Rule, after []domain.Rule,
	resolution string) (int, error) {
	live, err := s.liveSlots(ctx, tx, `doctor_id = $1`, doctorID)
	if err != nil {
		return 0, err
	}
	var hit []domain.Slot
	for _, sl := range live {
		if old.Covers(sl.ClinicID, sl.StartAt, sl.EndAt, false) && !domain.CoveredByAny(after, sl.ClinicID, sl.StartAt, sl.EndAt, false) {
			hit = append(hit, sl)
		}
	}
	if len(hit) > 0 && resolution != ResolutionKeepBookings {
		return 0, liveConflict(hit)
	}
	return len(hit), nil
}

// Regenerate — qo'lda (`POST /v1/doctor/schedule/regenerate`).
func (s *Service) Regenerate(ctx context.Context, doctorID uuid.UUID) (ChangeResult, error) {
	var res ChangeResult
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		var err error
		res.Removed, res.Created, err = s.regenerate(ctx, tx, ch, doctorID)
		return err
	})
	return res, err
}

// --- ta'til (D3) --------------------------------------------------------------------------------

// MaxTimeOffDays — bitta ta'til uzunligi.
const MaxTimeOffDays = 90

var timeOffKinds = map[string]bool{"vacation": true, "sick": true, "personal": true, "other": true}

// TimeOffInput — yangi ta'til.
type TimeOffInput struct {
	StartAt time.Time `json:"start_at"`
	EndAt   time.Time `json:"end_at"`
	Kind    string    `json:"kind"`
	Reason  string    `json:"reason"`
}

// CreateTimeOff — oraliqdagi bo'sh slotlar `time_off` bilan yopiladi.
func (s *Service) CreateTimeOff(ctx context.Context, doctorID uuid.UUID, in TimeOffInput, resolution string) (store.TimeOff, int, error) {
	if in.Kind == "" {
		in.Kind = "other"
	}
	now := s.Now()
	switch {
	case !timeOffKinds[in.Kind]:
		return store.TimeOff{}, 0, gchttp.BadRequest("VALIDATION_ERROR", "kind: vacation | sick | personal | other")
	case !in.EndAt.After(in.StartAt) || !in.EndAt.After(now):
		return store.TimeOff{}, 0, gchttp.BadRequest("VALIDATION_ERROR", "end_at start_at dan keyin va kelajakda bo'lishi kerak")
	case in.EndAt.Sub(in.StartAt) > MaxTimeOffDays*24*time.Hour:
		return store.TimeOff{}, 0, gchttp.BadRequest("VALIDATION_ERROR", "ta'til ≤ 90 kun")
	case len(in.Reason) > 255:
		return store.TimeOff{}, 0, gchttp.BadRequest("VALIDATION_ERROR", "reason ≤ 255")
	}
	t := store.TimeOff{ID: uuid.New(), StartAt: in.StartAt.UTC(), EndAt: in.EndAt.UTC(), Kind: in.Kind, Reason: in.Reason}
	kept := 0
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		if err := lockDoctor(ctx, tx, doctorID); err != nil {
			return err
		}
		overlapping, err := store.TimeOffs(ctx, tx, doctorID, t.StartAt, t.EndAt)
		if err != nil {
			return err
		}
		if len(overlapping) > 0 {
			return gchttp.Conflict("TIME_OFF_OVERLAP", "Bu oraliqda ta'til allaqachon bor")
		}
		live, err := s.liveSlots(ctx, tx, `doctor_id = $1 AND start_at < $3 AND end_at > $2`, doctorID, t.StartAt, t.EndAt)
		if err != nil {
			return err
		}
		if len(live) > 0 && resolution != ResolutionKeepBookings {
			return liveConflict(live)
		}
		kept = len(live)
		if _, err := tx.Exec(ctx, `INSERT INTO time_offs (id, doctor_id, start_at, end_at, kind, reason) VALUES ($1, $2, $3, $4, $5, $6)`,
			t.ID, doctorID, t.StartAt, t.EndAt, t.Kind, t.Reason); err != nil {
			return err
		}
		_, err = s.blockAvailable(ctx, tx, ch, domain.BlockTimeOff, t.Reason, &t.ID,
			`doctor_id = $1 AND start_at < $3 AND end_at > $2`, doctorID, t.StartAt, t.EndAt)
		return err
	})
	return t, kept, err
}

// ListTimeOffs — tugamaganlari.
func (s *Service) ListTimeOffs(ctx context.Context, doctorID uuid.UUID) ([]store.TimeOff, error) {
	return store.TimeOffs(ctx, s.Pool, doctorID, s.Now(), s.Now().AddDate(10, 0, 0))
}

// DeleteTimeOff — unga bog'langan kelajak slotlar ochiladi, keyin generatsiya.
func (s *Service) DeleteTimeOff(ctx context.Context, doctorID, id uuid.UUID) (int, error) {
	reopened := 0
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		n, err := store.Exec(ctx, tx, `SELECT 1 FROM time_offs WHERE id = $1 AND doctor_id = $2 FOR UPDATE`, id, doctorID)
		if err != nil {
			return err
		}
		if n == 0 {
			return ErrNotFound
		}
		rows, err := tx.Query(ctx, `SELECT id FROM slots WHERE time_off_id = $1`, id)
		if err != nil {
			return err
		}
		ids, err := pgx.CollectRows(rows, pgx.RowTo[uuid.UUID])
		if err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `DELETE FROM time_offs WHERE id = $1`, id); err != nil {
			return err
		}
		if reopened, err = s.unblock(ctx, tx, ch, domain.BlockTimeOff, `id = ANY($1)`, ids); err != nil {
			return err
		}
		_, err = s.generateDoctor(ctx, tx, ch, doctorID)
		return err
	})
	return reopened, err
}

// --- dam olish kuni ------------------------------------------------------------------------------

// CreateDayOff — butun kun; faol bronlar — 409 (keep_bookings bilan davom).
func (s *Service) CreateDayOff(ctx context.Context, doctorID uuid.UUID, day domain.Date, reason, resolution string) (uuid.UUID, int, error) {
	if day.Before(domain.Today(s.Now())) {
		return uuid.Nil, 0, gchttp.BadRequest("VALIDATION_ERROR", "sana bugun yoki kelajakda bo'lishi kerak")
	}
	id := uuid.New()
	kept := 0
	from, to := domain.DayBounds(day)
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		live, err := s.liveSlots(ctx, tx, `doctor_id = $1 AND start_at >= $2 AND start_at < $3`, doctorID, from, to)
		if err != nil {
			return err
		}
		if len(live) > 0 && resolution != ResolutionKeepBookings {
			return liveConflict(live)
		}
		kept = len(live)
		if _, err := tx.Exec(ctx, `INSERT INTO schedule_exceptions (id, doctor_id, date, reason) VALUES ($1, $2, $3, $4)`,
			id, doctorID, day, reason); err != nil {
			if isUnique(err) {
				return gchttp.Conflict("DAY_OFF_EXISTS", "Bu kun allaqachon dam olish kuni")
			}
			return err
		}
		_, err = s.blockAvailable(ctx, tx, ch, domain.BlockDayOff, reason, nil,
			`doctor_id = $1 AND start_at >= $2 AND start_at < $3`, doctorID, from, to)
		return err
	})
	return id, kept, err
}

// ListDayOffs — kelajakdagilar.
func (s *Service) ListDayOffs(ctx context.Context, doctorID uuid.UUID) ([]store.Exception, error) {
	return store.ListExceptions(ctx, s.Pool, doctorID, domain.Today(s.Now()))
}

// DeleteDayOff — kun ochiladi va generatsiya.
func (s *Service) DeleteDayOff(ctx context.Context, doctorID, id uuid.UUID) (int, error) {
	reopened := 0
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		var day domain.Date
		err := tx.QueryRow(ctx, `DELETE FROM schedule_exceptions WHERE id = $1 AND doctor_id = $2 RETURNING date`, id, doctorID).Scan(&day)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrNotFound
		}
		if err != nil {
			return err
		}
		from, to := domain.DayBounds(day)
		if reopened, err = s.unblock(ctx, tx, ch, domain.BlockDayOff, `doctor_id = $1 AND start_at >= $2 AND start_at < $3`,
			doctorID, from, to); err != nil {
			return err
		}
		_, err = s.generateDoctor(ctx, tx, ch, doctorID)
		return err
	})
	return reopened, err
}

// --- bitta slot ----------------------------------------------------------------------------------

// BlockSlot — shifokor bo'sh slotini qo'lda yopadi.
func (s *Service) BlockSlot(ctx context.Context, doctorID, slotID uuid.UUID, note string) error {
	return s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		n, err := s.blockAvailable(ctx, tx, ch, domain.BlockManual, note, nil, `id = $1 AND doctor_id = $2 AND start_at > $3`,
			slotID, doctorID, s.Now())
		if err != nil {
			return err
		}
		if n == 0 {
			return gchttp.BadRequest("SLOT_NOT_FREE", "Faqat kelajakdagi bo'sh slotni yopish mumkin")
		}
		return nil
	})
}

// UnblockSlot — faqat qo'lda yopilgan kelajak slot.
func (s *Service) UnblockSlot(ctx context.Context, doctorID, slotID uuid.UUID) error {
	return s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		n, err := s.unblock(ctx, tx, ch, domain.BlockManual, `id = $1 AND doctor_id = $2`, slotID, doctorID)
		if err != nil {
			return err
		}
		if n == 0 {
			return gchttp.BadRequest("SLOT_NOT_BLOCKED", "Faqat qo'lda yopilgan kelajak slotni ochish mumkin")
		}
		return nil
	})
}

// CalendarItem — shifokor taqvimi qatori.
type CalendarItem struct {
	SlotID      uuid.UUID  `json:"slot_id"`
	StartAt     time.Time  `json:"start_at"`
	EndAt       time.Time  `json:"end_at"`
	Status      string     `json:"status"`
	BlockReason string     `json:"block_reason"`
	BlockNote   string     `json:"block_note"`
	Place       string     `json:"place"`
	ClinicID    *uuid.UUID `json:"clinic_id"`
	RoomID      *uuid.UUID `json:"room_id"`
	BookingID   *string    `json:"booking_id"`
}

func (s *Service) calendarItems(slots []domain.Slot) []CalendarItem {
	now := s.Now()
	out := make([]CalendarItem, 0, len(slots))
	for _, sl := range slots {
		it := CalendarItem{SlotID: sl.ID, StartAt: sl.StartAt, EndAt: sl.EndAt, Status: sl.EffectiveStatus(now),
			BlockReason: sl.BlockReason, BlockNote: sl.BlockNote, Place: sl.Place(), ClinicID: sl.ClinicID, RoomID: sl.RoomID}
		if it.Status == domain.StatusHeld || it.Status == domain.StatusBooked {
			it.BookingID = sl.HoldRef // booking'dagi tafsilot — frontend shu ID bilan oladi
		}
		out = append(out, it)
	}
	return out
}

// Calendar — [from, to] mahalliy kunlar (≤ 31).
func (s *Service) Calendar(ctx context.Context, doctorID uuid.UUID, from, to domain.Date) ([]CalendarItem, error) {
	if to.Before(from) || to.Sub(from) >= MaxRangeDays*24*time.Hour {
		return nil, gchttp.BadRequest("INVALID_RANGE", "to ≥ from va oraliq < 31 kun")
	}
	start, _ := domain.DayBounds(from)
	_, end := domain.DayBounds(to)
	slots, err := store.DoctorSlots(ctx, s.Pool, doctorID, start, end)
	if err != nil {
		return nil, err
	}
	return s.calendarItems(slots), nil
}

// --- klinika -------------------------------------------------------------------------------------

// ClinicDay — klinikaning bir kuni, shifokorlar bo'yicha.
func (s *Service) ClinicDay(ctx context.Context, clinicID uuid.UUID, day domain.Date) (map[string][]CalendarItem, error) {
	from, to := domain.DayBounds(day)
	slots, err := store.SlotsWhere(ctx, s.Pool, `clinic_id = $1 AND start_at >= $2 AND start_at < $3 ORDER BY doctor_id, start_at`,
		clinicID, from, to)
	if err != nil {
		return nil, err
	}
	out := map[string][]CalendarItem{}
	for _, sl := range slots {
		key := sl.DoctorID.String()
		out[key] = append(out[key], s.calendarItems([]domain.Slot{sl})...)
	}
	return out, nil
}

// ClosureResult — yopilish natijasi (D7: bronlar bekor qilinmaydi — klinika qayta yozadi).
type ClosureResult struct {
	ID                   uuid.UUID      `json:"id"`
	Date                 string         `json:"date"`
	Blocked              int            `json:"blocked"`
	BookingsToReschedule []ConflictSlot `json:"bookings_to_reschedule"`
}

// CreateClosure — klinika yopiq kuni (ertadan boshlab).
func (s *Service) CreateClosure(ctx context.Context, clinicID, userID uuid.UUID, day domain.Date, reason string) (ClosureResult, error) {
	if !day.After(domain.Today(s.Now())) {
		return ClosureResult{}, gchttp.BadRequest("VALIDATION_ERROR", "sana ertadan boshlab bo'lishi kerak")
	}
	res := ClosureResult{ID: uuid.New(), Date: day.Format(time.DateOnly)}
	from, to := domain.DayBounds(day)
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		if _, err := tx.Exec(ctx, `INSERT INTO clinic_closures (id, clinic_id, date, reason, created_by) VALUES ($1, $2, $3, $4, $5)`,
			res.ID, clinicID, day, reason, userID); err != nil {
			if isUnique(err) {
				return gchttp.Conflict("CLOSURE_EXISTS", "Bu kun allaqachon yopiq")
			}
			return err
		}
		live, err := s.liveSlots(ctx, tx, `clinic_id = $1 AND start_at >= $2 AND start_at < $3`, clinicID, from, to)
		if err != nil {
			return err
		}
		res.BookingsToReschedule = conflictsOf(live)
		res.Blocked, err = s.blockAvailable(ctx, tx, ch, domain.BlockClinicClosed, reason, nil,
			`clinic_id = $1 AND start_at >= $2 AND start_at < $3`, clinicID, from, to)
		return err
	})
	return res, err
}

// ListClosures — kelajakdagilar.
func (s *Service) ListClosures(ctx context.Context, clinicID uuid.UUID) ([]store.Closure, error) {
	return store.ListClosures(ctx, s.Pool, clinicID, domain.Today(s.Now()))
}

// DeleteClosure — kun ochiladi, klinikadagi shifokorlar uchun generatsiya.
func (s *Service) DeleteClosure(ctx context.Context, clinicID, id uuid.UUID) error {
	return s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		var day domain.Date
		err := tx.QueryRow(ctx, `DELETE FROM clinic_closures WHERE id = $1 AND clinic_id = $2 RETURNING date`, id, clinicID).Scan(&day)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrNotFound
		}
		if err != nil {
			return err
		}
		from, to := domain.DayBounds(day)
		if _, err := s.unblock(ctx, tx, ch, domain.BlockClinicClosed, `clinic_id = $1 AND start_at >= $2 AND start_at < $3`,
			clinicID, from, to); err != nil {
			return err
		}
		return s.generateClinicDoctors(ctx, tx, ch, clinicID)
	})
}

func (s *Service) generateClinicDoctors(ctx context.Context, tx pgx.Tx, ch *changes, clinicID uuid.UUID) error {
	rules, err := store.RulesAtClinic(ctx, tx, clinicID)
	if err != nil {
		return err
	}
	seen := map[uuid.UUID]bool{}
	for _, r := range rules {
		if seen[r.DoctorID] {
			continue
		}
		seen[r.DoctorID] = true
		if _, err := s.generateDoctor(ctx, tx, ch, r.DoctorID); err != nil {
			return err
		}
	}
	return nil
}

// ClinicRules — klinikadagi qoidalar (xona biriktirish uchun).
func (s *Service) ClinicRules(ctx context.Context, clinicID uuid.UUID) ([]domain.Rule, error) {
	return store.RulesAtClinic(ctx, s.Pool, clinicID)
}

// RoomResult — xona biriktirish natijasi (D8).
type RoomResult struct {
	Moved     int         `json:"moved"`
	Blocked   int         `json:"blocked"`
	Conflicts []time.Time `json:"conflicts"`
}

// AssignRoom — qoida va uning kelajak slotlariga xona. Xona boshqa shifokorda band: bo'sh slot — yopiladi
// (`room_busy`), band slot — eski xonasida qoladi va `conflicts` ga tushadi (registratura hal qiladi).
func (s *Service) AssignRoom(ctx context.Context, clinicID, ruleID uuid.UUID, roomID *uuid.UUID) (RoomResult, error) {
	if roomID != nil {
		room, err := s.Catalog.Room(ctx, *roomID)
		if errors.Is(err, clients.ErrNotFound) || (err == nil && (room.ClinicID != clinicID || !room.IsActive)) {
			return RoomResult{}, gchttp.BadRequest("INVALID_ROOM", "Xona topilmadi yoki faol emas")
		}
		if err != nil {
			return RoomResult{}, gchttp.Unavailable("CATALOG_UNAVAILABLE", "Katalog vaqtincha mavjud emas")
		}
	}
	res := RoomResult{Conflicts: []time.Time{}}
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		r, err := store.GetRule(ctx, tx, ruleID)
		if isNotFound(err) || (err == nil && (r.ClinicID == nil || *r.ClinicID != clinicID)) {
			return ErrRuleNotFound
		}
		if err != nil {
			return err
		}
		r.RoomID = roomID
		if err := store.SaveRule(ctx, tx, r); err != nil {
			return err
		}
		// Oldingi biriktirishda `room_busy` bilan yopilganlar qayta ko'rib chiqiladi
		if _, err := s.unblock(ctx, tx, ch, domain.BlockRoomBusy, `doctor_id = $1 AND clinic_id = $2`, r.DoctorID, clinicID); err != nil {
			return err
		}
		slots, err := store.SlotsWhere(ctx, tx, `doctor_id = $1 AND clinic_id = $2 AND start_at > $3 ORDER BY start_at FOR UPDATE`,
			r.DoctorID, clinicID, s.Now())
		if err != nil {
			return err
		}
		now := s.Now()
		for _, sl := range slots {
			if !r.Covers(sl.ClinicID, sl.StartAt, sl.EndAt, false) {
				continue
			}
			if roomID == nil {
				if _, err := tx.Exec(ctx, `UPDATE slots SET room_id = NULL, updated_at = now() WHERE id = $1`, sl.ID); err != nil {
					return err
				}
				res.Moved++
				continue
			}
			var busy bool
			if err := tx.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM slots WHERE room_id = $1 AND id <> $2 AND doctor_id <> $3
				AND start_at < $5 AND end_at > $4)`, *roomID, sl.ID, sl.DoctorID, sl.StartAt, sl.EndAt).Scan(&busy); err != nil {
				return err
			}
			switch {
			case !busy:
				if _, err := tx.Exec(ctx, `UPDATE slots SET room_id = $2, updated_at = now() WHERE id = $1`, sl.ID, *roomID); err != nil {
					return err
				}
				res.Moved++
			case sl.Status == domain.StatusFree || sl.HoldExpired(now):
				if _, err := s.blockAvailable(ctx, tx, ch, domain.BlockRoomBusy, "Xona band", nil, `id = $1`, sl.ID); err != nil {
					return err
				}
				if _, err := tx.Exec(ctx, `UPDATE slots SET room_id = NULL WHERE id = $1`, sl.ID); err != nil {
					return err
				}
				res.Blocked++
			default:
				res.Conflicts = append(res.Conflicts, sl.StartAt)
			}
			ch.touch(sl.DoctorID, sl.StartAt)
		}
		return nil
	})
	return res, err
}

// --- narx qoidalari (C12) -----------------------------------------------------------------------

// ListPriceRules — shifokor qoidalari.
func (s *Service) ListPriceRules(ctx context.Context, doctorID uuid.UUID) ([]domain.PriceRule, error) {
	return store.PriceRules(ctx, s.Pool, doctorID)
}

// SavePriceRule — yaratish/o'zgartirish; ommaviy ro'yxatdagi foiz o'zgaradi — kesh tozalanadi.
func (s *Service) SavePriceRule(ctx context.Context, p domain.PriceRule, create bool) (domain.PriceRule, error) {
	if err := p.Validate(); err != nil {
		return p, badRequest(err)
	}
	err := s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		if !create {
			old, err := store.GetPriceRule(ctx, tx, p.ID)
			if isNotFound(err) || (err == nil && old.DoctorID != p.DoctorID) {
				return ErrNotFound
			}
			if err != nil {
				return err
			}
		}
		if err := store.SavePriceRule(ctx, tx, p); err != nil {
			return err
		}
		s.touchHorizon(ctx, ch, p.DoctorID)
		return nil
	})
	if err != nil {
		return p, err
	}
	return store.GetPriceRule(ctx, s.Pool, p.ID)
}

// DeletePriceRule — o'chirish.
func (s *Service) DeletePriceRule(ctx context.Context, doctorID, id uuid.UUID) error {
	return s.inTx(ctx, func(tx pgx.Tx, ch *changes) error {
		n, err := store.Exec(ctx, tx, `DELETE FROM price_rules WHERE id = $1 AND doctor_id = $2`, id, doctorID)
		if err != nil {
			return err
		}
		if n == 0 {
			return ErrNotFound
		}
		s.touchHorizon(ctx, ch, doctorID)
		return nil
	})
}

func (s *Service) touchHorizon(ctx context.Context, ch *changes, doctorID uuid.UUID) {
	today := domain.Today(s.Now())
	for i := 0; i <= s.Settings.Schedule(ctx).GenerationDaysAhead; i++ {
		ch.touch(doctorID, domain.At(today.AddDate(0, 0, i), 12*60))
	}
}

func isUnique(err error) bool {
	var pg *pgconn.PgError
	return errors.As(err, &pg) && pg.Code == "23505"
}

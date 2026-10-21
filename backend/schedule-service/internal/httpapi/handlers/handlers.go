// Package handlers — HTTP handlerlar. Faqat so'rov/javob; biznes logika internal/service da.
package handlers

import (
	"context"
	"errors"
	"net/http"
	"slices"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/auth"
	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"
	"github.com/Muhiddin1408/nurse_new/libs/go-common/httpclient"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/app"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/domain"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/service"
	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/store"
)

// Handlers — bog'liqliklar bilan.
type Handlers struct {
	app *app.App
	svc *service.Service
}

// New — konstruktor.
func New(a *app.App) *Handlers { return &Handlers{app: a, svc: a.Svc} }

type ctxKey int

const doctorKey ctxKey = 1

var (
	errNotDoctor   = gchttp.Forbidden("ROLE_REQUIRED", "Bu amal faqat shifokor uchun")
	errClinicScope = gchttp.NotFound("CLINIC_NOT_FOUND", "Klinika topilmadi")
)

// RequireDoctor — `doctor` roli va `doctor_id` claim'i; proyeksiyada tasdiqlangan (approved) shifokor.
func (h *Handlers) RequireDoctor(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		u, _ := auth.UserFrom(r.Context())
		if u.DoctorID == nil || !u.HasRole("doctor") {
			gchttp.WriteError(w, r, errNotDoctor)
			return
		}
		if err := h.svc.RequireActiveDoctor(r.Context(), *u.DoctorID); err != nil {
			gchttp.WriteError(w, r, err)
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), doctorKey, *u.DoctorID)))
	})
}

// RequireClinicAdmin — catalog `GET /internal/v1/access` (60 s kesh). Begona klinika — 404 (mavjudligi oshkor qilinmaydi).
func (h *Handlers) RequireClinicAdmin(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		u, _ := auth.UserFrom(r.Context())
		clinicID, err := uuid.Parse(chi.URLParam(r, "clinic_id"))
		if err != nil {
			gchttp.WriteError(w, r, errClinicScope)
			return
		}
		ok, err := h.svc.Catalog.ClinicAccess(r.Context(), u.ID, clinicID)
		if err != nil {
			if httpclient.IsUnavailable(err) {
				gchttp.WriteError(w, r, gchttp.Unavailable("CATALOG_UNAVAILABLE", "Katalog vaqtincha mavjud emas"))
				return
			}
			gchttp.WriteError(w, r, err)
			return
		}
		if !ok {
			gchttp.WriteError(w, r, errClinicScope)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func doctorID(r *http.Request) uuid.UUID { return r.Context().Value(doctorKey).(uuid.UUID) }

func pathUUID(r *http.Request, name string) (uuid.UUID, error) {
	id, err := uuid.Parse(chi.URLParam(r, name))
	if err != nil {
		return uuid.Nil, service.ErrNotFound
	}
	return id, nil
}

func queryDate(r *http.Request, name string, def *domain.Date) (domain.Date, error) {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		if def != nil {
			return *def, nil
		}
		return domain.Date{}, gchttp.BadRequest("VALIDATION_ERROR", name+" majburiy (YYYY-MM-DD)")
	}
	d, err := domain.ParseDate(raw)
	if err != nil {
		return d, gchttp.BadRequest("VALIDATION_ERROR", name+": YYYY-MM-DD")
	}
	return d, nil
}

func queryTime(r *http.Request, name string) (time.Time, bool, error) {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return time.Time{}, false, nil
	}
	t, err := time.Parse(time.RFC3339, raw)
	if err != nil {
		return t, true, gchttp.BadRequest("VALIDATION_ERROR", name+": RFC 3339 vaqt (masalan 2026-10-21T09:00:00+05:00)")
	}
	return t.UTC(), true, nil
}

func resolution(r *http.Request) string { return r.URL.Query().Get("resolution") }

// --- ommaviy --------------------------------------------------------------------------------------

// PublicSlots — `?date=` (kesh 30 s) yoki `?date_from=&date_to=` (≤ 31 kun, ≤ 200).
func (h *Handlers) PublicSlots(w http.ResponseWriter, r *http.Request) error {
	doc, err := pathUUID(r, "doctor_id")
	if err != nil {
		return err
	}
	from, hasFrom, err := queryTime(r, "date_from")
	if err != nil {
		return err
	}
	to, hasTo, err := queryTime(r, "date_to")
	if err != nil {
		return err
	}
	if hasFrom || hasTo {
		if !hasFrom || !hasTo {
			return gchttp.BadRequest("VALIDATION_ERROR", "date_from va date_to birga beriladi")
		}
		slots, err := h.svc.RangeSlots(r.Context(), doc, from, to)
		if err != nil {
			return err
		}
		gchttp.WriteJSON(w, http.StatusOK, slots)
		return nil
	}
	today := domain.Today(time.Now())
	day, err := queryDate(r, "date", &today)
	if err != nil {
		return err
	}
	slots, err := h.svc.DaySlots(r.Context(), doc, day)
	if err != nil {
		return err
	}
	w.Header().Set("Cache-Control", "public, max-age=30")
	gchttp.WriteJSON(w, http.StatusOK, slots)
	return nil
}

// --- qoidalar --------------------------------------------------------------------------------------

type ruleOut struct {
	ID          uuid.UUID    `json:"id"`
	ClinicID    *uuid.UUID   `json:"clinic_id"`
	RoomID      *uuid.UUID   `json:"room_id"`
	Place       string       `json:"place"`
	Weekday     int          `json:"weekday"`
	StartTime   domain.Clock `json:"start_time"`
	EndTime     domain.Clock `json:"end_time"`
	SlotMinutes int          `json:"slot_minutes"`
	ValidFrom   string       `json:"valid_from"`
	ValidTo     *string      `json:"valid_to"`
}

func toRuleOut(r domain.Rule) ruleOut {
	out := ruleOut{ID: r.ID, ClinicID: r.ClinicID, RoomID: r.RoomID, Place: domain.PlaceClinic, Weekday: r.Weekday,
		StartTime: r.Start, EndTime: r.End, SlotMinutes: r.SlotMinutes, ValidFrom: r.ValidFrom.Format(time.DateOnly)}
	if r.ClinicID == nil {
		out.Place = domain.PlaceHome
	}
	if r.ValidTo != nil {
		s := r.ValidTo.Format(time.DateOnly)
		out.ValidTo = &s
	}
	return out
}

func rulesOut(rules []domain.Rule) []ruleOut {
	out := make([]ruleOut, 0, len(rules))
	for _, r := range rules {
		out = append(out, toRuleOut(r))
	}
	return out
}

type ruleIn struct {
	ClinicID    *uuid.UUID    `json:"clinic_id"`
	Weekday     *int          `json:"weekday"`
	StartTime   *domain.Clock `json:"start_time"`
	EndTime     *domain.Clock `json:"end_time"`
	SlotMinutes *int          `json:"slot_minutes"`
	ValidFrom   *string       `json:"valid_from"`
	ValidTo     *string       `json:"valid_to"`
}

// ListRules — GET.
func (h *Handlers) ListRules(w http.ResponseWriter, r *http.Request) error {
	rules, err := h.svc.ListRules(r.Context(), doctorID(r))
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, rulesOut(rules))
	return nil
}

func parseOptDate(s *string) (*domain.Date, error) {
	if s == nil || *s == "" {
		return nil, nil
	}
	d, err := domain.ParseDate(*s)
	if err != nil {
		return nil, gchttp.BadRequest("VALIDATION_ERROR", "sana YYYY-MM-DD bo'lishi kerak")
	}
	return &d, nil
}

// CreateRule — POST.
func (h *Handlers) CreateRule(w http.ResponseWriter, r *http.Request) error {
	var in ruleIn
	if err := gchttp.DecodeJSON(w, r, &in); err != nil {
		return err
	}
	if in.Weekday == nil || in.StartTime == nil || in.EndTime == nil || in.SlotMinutes == nil || in.ValidFrom == nil {
		return gchttp.BadRequest("VALIDATION_ERROR", "weekday, start_time, end_time, slot_minutes, valid_from majburiy")
	}
	from, err := parseOptDate(in.ValidFrom)
	if err != nil || from == nil {
		return gchttp.BadRequest("VALIDATION_ERROR", "valid_from YYYY-MM-DD")
	}
	to, err := parseOptDate(in.ValidTo)
	if err != nil {
		return err
	}
	rule, res, err := h.svc.CreateRule(r.Context(), domain.Rule{DoctorID: doctorID(r), ClinicID: in.ClinicID, Weekday: *in.Weekday,
		Start: *in.StartTime, End: *in.EndTime, SlotMinutes: *in.SlotMinutes, ValidFrom: *from, ValidTo: to})
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusCreated, map[string]any{"rule": toRuleOut(rule), "result": res})
	return nil
}

// UpdateRule — PATCH (`?resolution=keep_bookings`).
func (h *Handlers) UpdateRule(w http.ResponseWriter, r *http.Request) error {
	id, err := pathUUID(r, "rule_id")
	if err != nil {
		return service.ErrRuleNotFound
	}
	raw := map[string]any{}
	var in ruleIn
	if err := decodeTwice(w, r, &in, &raw); err != nil {
		return err
	}
	p := service.RulePatch{Weekday: in.Weekday, Start: in.StartTime, End: in.EndTime, SlotMinutes: in.SlotMinutes}
	if _, ok := raw["clinic_id"]; ok {
		p.ClinicSet, p.ClinicID = true, in.ClinicID
	}
	if p.ValidFrom, err = parseOptDate(in.ValidFrom); err != nil {
		return err
	}
	if _, ok := raw["valid_to"]; ok {
		p.ValidToSet = true
		if p.ValidTo, err = parseOptDate(in.ValidTo); err != nil {
			return err
		}
	}
	rule, res, err := h.svc.UpdateRule(r.Context(), doctorID(r), id, p, resolution(r))
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, map[string]any{"rule": toRuleOut(rule), "result": res})
	return nil
}

// DeleteRule — DELETE (`?resolution=keep_bookings`).
func (h *Handlers) DeleteRule(w http.ResponseWriter, r *http.Request) error {
	id, err := pathUUID(r, "rule_id")
	if err != nil {
		return service.ErrRuleNotFound
	}
	res, err := h.svc.DeleteRule(r.Context(), doctorID(r), id, resolution(r))
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, map[string]any{"result": res})
	return nil
}

// Regenerate — POST.
func (h *Handlers) Regenerate(w http.ResponseWriter, r *http.Request) error {
	res, err := h.svc.Regenerate(r.Context(), doctorID(r))
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, res)
	return nil
}

// --- dam olish kunlari va ta'til ---------------------------------------------------------------------

type dayOffOut struct {
	ID     uuid.UUID `json:"id"`
	Date   string    `json:"date"`
	Reason string    `json:"reason"`
}

// ListDayOffs — GET.
func (h *Handlers) ListDayOffs(w http.ResponseWriter, r *http.Request) error {
	list, err := h.svc.ListDayOffs(r.Context(), doctorID(r))
	if err != nil {
		return err
	}
	out := make([]dayOffOut, 0, len(list))
	for _, e := range list {
		out = append(out, dayOffOut{ID: e.ID, Date: e.Date.Format(time.DateOnly), Reason: e.Reason})
	}
	gchttp.WriteJSON(w, http.StatusOK, out)
	return nil
}

// CreateDayOff — POST {date, reason}.
func (h *Handlers) CreateDayOff(w http.ResponseWriter, r *http.Request) error {
	var in struct {
		Date   string `json:"date"`
		Reason string `json:"reason"`
	}
	if err := gchttp.DecodeJSON(w, r, &in); err != nil {
		return err
	}
	day, err := domain.ParseDate(in.Date)
	if err != nil || len(in.Reason) > 255 {
		return gchttp.BadRequest("VALIDATION_ERROR", "date YYYY-MM-DD, reason ≤ 255")
	}
	id, kept, err := h.svc.CreateDayOff(r.Context(), doctorID(r), day, in.Reason, resolution(r))
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusCreated, map[string]any{"id": id, "date": in.Date, "kept_bookings": kept})
	return nil
}

// DeleteDayOff — DELETE.
func (h *Handlers) DeleteDayOff(w http.ResponseWriter, r *http.Request) error {
	id, err := pathUUID(r, "id")
	if err != nil {
		return err
	}
	n, err := h.svc.DeleteDayOff(r.Context(), doctorID(r), id)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, map[string]int{"reopened_slots": n})
	return nil
}

type timeOffOut struct {
	ID      uuid.UUID `json:"id"`
	StartAt time.Time `json:"start_at"`
	EndAt   time.Time `json:"end_at"`
	Kind    string    `json:"kind"`
	Reason  string    `json:"reason"`
}

func toTimeOffOut(t store.TimeOff) timeOffOut {
	return timeOffOut{ID: t.ID, StartAt: t.StartAt, EndAt: t.EndAt, Kind: t.Kind, Reason: t.Reason}
}

// ListTimeOffs — GET.
func (h *Handlers) ListTimeOffs(w http.ResponseWriter, r *http.Request) error {
	list, err := h.svc.ListTimeOffs(r.Context(), doctorID(r))
	if err != nil {
		return err
	}
	out := make([]timeOffOut, 0, len(list))
	for _, t := range list {
		out = append(out, toTimeOffOut(t))
	}
	gchttp.WriteJSON(w, http.StatusOK, out)
	return nil
}

// CreateTimeOff — POST (`?resolution=keep_bookings`).
func (h *Handlers) CreateTimeOff(w http.ResponseWriter, r *http.Request) error {
	var in service.TimeOffInput
	if err := gchttp.DecodeJSON(w, r, &in); err != nil {
		return err
	}
	t, kept, err := h.svc.CreateTimeOff(r.Context(), doctorID(r), in, resolution(r))
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusCreated, map[string]any{"time_off": toTimeOffOut(t), "kept_bookings": kept})
	return nil
}

// DeleteTimeOff — DELETE.
func (h *Handlers) DeleteTimeOff(w http.ResponseWriter, r *http.Request) error {
	id, err := pathUUID(r, "id")
	if err != nil {
		return err
	}
	n, err := h.svc.DeleteTimeOff(r.Context(), doctorID(r), id)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, map[string]int{"reopened_slots": n})
	return nil
}

// BlockSlot — POST {note}.
func (h *Handlers) BlockSlot(w http.ResponseWriter, r *http.Request) error {
	id, err := pathUUID(r, "slot_id")
	if err != nil {
		return err
	}
	var in struct {
		Note string `json:"note"`
	}
	if r.ContentLength != 0 {
		if err := gchttp.DecodeJSON(w, r, &in); err != nil {
			return err
		}
	}
	if len(in.Note) > 255 {
		return gchttp.BadRequest("VALIDATION_ERROR", "note ≤ 255")
	}
	if err := h.svc.BlockSlot(r.Context(), doctorID(r), id, in.Note); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
	return nil
}

// UnblockSlot — POST.
func (h *Handlers) UnblockSlot(w http.ResponseWriter, r *http.Request) error {
	id, err := pathUUID(r, "slot_id")
	if err != nil {
		return err
	}
	if err := h.svc.UnblockSlot(r.Context(), doctorID(r), id); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
	return nil
}

// Calendar — `?from=&to=` (YYYY-MM-DD, < 31 kun).
func (h *Handlers) Calendar(w http.ResponseWriter, r *http.Request) error {
	from, err := queryDate(r, "from", nil)
	if err != nil {
		return err
	}
	to, err := queryDate(r, "to", nil)
	if err != nil {
		return err
	}
	items, err := h.svc.Calendar(r.Context(), doctorID(r), from, to)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, items)
	return nil
}

// --- narx qoidalari ------------------------------------------------------------------------------------

type priceIn struct {
	Name      *string       `json:"name"`
	Weekdays  *string       `json:"weekdays"`
	StartTime *domain.Clock `json:"start_time"`
	EndTime   *domain.Clock `json:"end_time"`
	Percent   *int          `json:"percent"`
	Priority  *int          `json:"priority"`
	IsActive  *bool         `json:"is_active"`
}

func (in priceIn) apply(p *domain.PriceRule) {
	if in.Name != nil {
		p.Name = *in.Name
	}
	if in.Weekdays != nil {
		p.Weekdays = *in.Weekdays
	}
	if in.StartTime != nil {
		p.Start = *in.StartTime
	}
	if in.EndTime != nil {
		p.End = *in.EndTime
	}
	if in.Percent != nil {
		p.Percent = *in.Percent
	}
	if in.Priority != nil {
		p.Priority = *in.Priority
	}
	if in.IsActive != nil {
		p.IsActive = *in.IsActive
	}
}

// ListPriceRules — GET.
func (h *Handlers) ListPriceRules(w http.ResponseWriter, r *http.Request) error {
	list, err := h.svc.ListPriceRules(r.Context(), doctorID(r))
	if err != nil {
		return err
	}
	if list == nil {
		list = []domain.PriceRule{}
	}
	gchttp.WriteJSON(w, http.StatusOK, list)
	return nil
}

// CreatePriceRule — POST.
func (h *Handlers) CreatePriceRule(w http.ResponseWriter, r *http.Request) error {
	var in priceIn
	if err := gchttp.DecodeJSON(w, r, &in); err != nil {
		return err
	}
	if in.StartTime == nil || in.EndTime == nil || in.Percent == nil {
		return gchttp.BadRequest("VALIDATION_ERROR", "start_time, end_time, percent majburiy")
	}
	p := domain.PriceRule{ID: uuid.New(), DoctorID: doctorID(r), Priority: 100, IsActive: true}
	in.apply(&p)
	if len(p.Name) > 100 {
		return gchttp.BadRequest("VALIDATION_ERROR", "name ≤ 100")
	}
	out, err := h.svc.SavePriceRule(r.Context(), p, true)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusCreated, out)
	return nil
}

// UpdatePriceRule — PATCH.
func (h *Handlers) UpdatePriceRule(w http.ResponseWriter, r *http.Request) error {
	id, err := pathUUID(r, "id")
	if err != nil {
		return err
	}
	var in priceIn
	if err := gchttp.DecodeJSON(w, r, &in); err != nil {
		return err
	}
	list, err := h.svc.ListPriceRules(r.Context(), doctorID(r))
	if err != nil {
		return err
	}
	idx := slices.IndexFunc(list, func(p domain.PriceRule) bool { return p.ID == id })
	if idx < 0 {
		return service.ErrNotFound
	}
	p := list[idx]
	in.apply(&p)
	out, err := h.svc.SavePriceRule(r.Context(), p, false)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, out)
	return nil
}

// DeletePriceRule — DELETE.
func (h *Handlers) DeletePriceRule(w http.ResponseWriter, r *http.Request) error {
	id, err := pathUUID(r, "id")
	if err != nil {
		return err
	}
	if err := h.svc.DeletePriceRule(r.Context(), doctorID(r), id); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
	return nil
}

// --- klinika ---------------------------------------------------------------------------------------------

func clinicID(r *http.Request) uuid.UUID {
	id, _ := uuid.Parse(chi.URLParam(r, "clinic_id"))
	return id
}

// ClinicDay — `?date=`.
func (h *Handlers) ClinicDay(w http.ResponseWriter, r *http.Request) error {
	today := domain.Today(time.Now())
	day, err := queryDate(r, "date", &today)
	if err != nil {
		return err
	}
	out, err := h.svc.ClinicDay(r.Context(), clinicID(r), day)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, map[string]any{"date": day.Format(time.DateOnly), "doctors": out})
	return nil
}

type closureOut struct {
	ID     uuid.UUID `json:"id"`
	Date   string    `json:"date"`
	Reason string    `json:"reason"`
}

// ListClosures — GET.
func (h *Handlers) ListClosures(w http.ResponseWriter, r *http.Request) error {
	list, err := h.svc.ListClosures(r.Context(), clinicID(r))
	if err != nil {
		return err
	}
	out := make([]closureOut, 0, len(list))
	for _, c := range list {
		out = append(out, closureOut{ID: c.ID, Date: c.Date.Format(time.DateOnly), Reason: c.Reason})
	}
	gchttp.WriteJSON(w, http.StatusOK, out)
	return nil
}

// CreateClosure — POST {date, reason}.
func (h *Handlers) CreateClosure(w http.ResponseWriter, r *http.Request) error {
	var in struct {
		Date   string `json:"date"`
		Reason string `json:"reason"`
	}
	if err := gchttp.DecodeJSON(w, r, &in); err != nil {
		return err
	}
	day, err := domain.ParseDate(in.Date)
	if err != nil || len(in.Reason) > 255 {
		return gchttp.BadRequest("VALIDATION_ERROR", "date YYYY-MM-DD, reason ≤ 255")
	}
	u, _ := auth.UserFrom(r.Context())
	res, err := h.svc.CreateClosure(r.Context(), clinicID(r), u.ID, day, in.Reason)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusCreated, res)
	return nil
}

// DeleteClosure — DELETE.
func (h *Handlers) DeleteClosure(w http.ResponseWriter, r *http.Request) error {
	id, err := pathUUID(r, "id")
	if err != nil {
		return err
	}
	if err := h.svc.DeleteClosure(r.Context(), clinicID(r), id); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
	return nil
}

// ClinicRules — GET.
func (h *Handlers) ClinicRules(w http.ResponseWriter, r *http.Request) error {
	rules, err := h.svc.ClinicRules(r.Context(), clinicID(r))
	if err != nil {
		return err
	}
	out := make([]map[string]any, 0, len(rules))
	for _, rule := range rules {
		out = append(out, map[string]any{"doctor_id": rule.DoctorID, "rule": toRuleOut(rule)})
	}
	gchttp.WriteJSON(w, http.StatusOK, out)
	return nil
}

// AssignRoom — POST {room_id|null}.
func (h *Handlers) AssignRoom(w http.ResponseWriter, r *http.Request) error {
	ruleID, err := pathUUID(r, "rule_id")
	if err != nil {
		return service.ErrRuleNotFound
	}
	var in struct {
		RoomID *uuid.UUID `json:"room_id"`
	}
	if err := gchttp.DecodeJSON(w, r, &in); err != nil {
		return err
	}
	res, err := h.svc.AssignRoom(r.Context(), clinicID(r), ruleID, in.RoomID)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, res)
	return nil
}

// --- internal ----------------------------------------------------------------------------------------------

// GetSlot — GET.
func (h *Handlers) GetSlot(w http.ResponseWriter, r *http.Request) error {
	id, err := pathUUID(r, "slot_id")
	if err != nil {
		return service.ErrSlotNotFound
	}
	v, err := h.svc.GetSlot(r.Context(), id)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, v)
	return nil
}

// Hold — POST.
func (h *Handlers) Hold(w http.ResponseWriter, r *http.Request) error {
	id, err := pathUUID(r, "slot_id")
	if err != nil {
		return service.ErrSlotTaken
	}
	var in service.HoldRequest
	if err := gchttp.DecodeJSON(w, r, &in); err != nil {
		return err
	}
	if in.TravelBufferMinutes != nil && (*in.TravelBufferMinutes < 0 || *in.TravelBufferMinutes > 90) {
		return gchttp.BadRequest("VALIDATION_ERROR", "travel_buffer_minutes 0..90")
	}
	v, err := h.svc.Hold(r.Context(), id, in)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, v)
	return nil
}

type refIn struct {
	HoldRef string `json:"hold_ref"`
	Minutes int    `json:"minutes"`
}

func decodeRef(w http.ResponseWriter, r *http.Request) (uuid.UUID, refIn, error) {
	var in refIn
	id, err := pathUUID(r, "slot_id")
	if err != nil {
		return id, in, service.ErrSlotNotFound
	}
	if err := gchttp.DecodeJSON(w, r, &in); err != nil {
		return id, in, err
	}
	if in.HoldRef == "" {
		return id, in, gchttp.BadRequest("VALIDATION_ERROR", "hold_ref majburiy")
	}
	return id, in, nil
}

// Confirm — POST.
func (h *Handlers) Confirm(w http.ResponseWriter, r *http.Request) error {
	id, in, err := decodeRef(w, r)
	if err != nil {
		return err
	}
	v, err := h.svc.Confirm(r.Context(), id, in.HoldRef)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, v)
	return nil
}

// Extend — POST.
func (h *Handlers) Extend(w http.ResponseWriter, r *http.Request) error {
	id, in, err := decodeRef(w, r)
	if err != nil {
		return err
	}
	v, err := h.svc.Extend(r.Context(), id, in.HoldRef, in.Minutes)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, v)
	return nil
}

// Release — POST (204, idempotent).
func (h *Handlers) Release(w http.ResponseWriter, r *http.Request) error {
	id, in, err := decodeRef(w, r)
	if errors.Is(err, service.ErrSlotNotFound) {
		w.WriteHeader(http.StatusNoContent)
		return nil
	}
	if err != nil {
		return err
	}
	if err := h.svc.Release(r.Context(), id, in.HoldRef); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
	return nil
}

// FreeSlots — GET `?from=&to=&place=&clinic_id=&min_minutes=&limit=`.
func (h *Handlers) FreeSlots(w http.ResponseWriter, r *http.Request) error {
	doc, err := pathUUID(r, "doctor_id")
	if err != nil {
		return err
	}
	from, okFrom, err := queryTime(r, "from")
	if err != nil {
		return err
	}
	to, okTo, err := queryTime(r, "to")
	if err != nil {
		return err
	}
	if !okFrom || !okTo || !to.After(from) {
		return gchttp.BadRequest("VALIDATION_ERROR", "from va to majburiy, to > from")
	}
	q := service.FreeSlotsQuery{DoctorID: doc, From: from, To: to, Place: r.URL.Query().Get("place")}
	if c := r.URL.Query().Get("clinic_id"); c != "" {
		id, err := uuid.Parse(c)
		if err != nil {
			return gchttp.BadRequest("VALIDATION_ERROR", "clinic_id uuid")
		}
		q.ClinicID = &id
	}
	q.MinMinutes, _ = strconv.Atoi(r.URL.Query().Get("min_minutes"))
	q.Limit, _ = strconv.Atoi(r.URL.Query().Get("limit"))
	items, err := h.svc.FreeSlots(r.Context(), q)
	if err != nil {
		return err
	}
	gchttp.WriteJSON(w, http.StatusOK, map[string]any{"items": items})
	return nil
}

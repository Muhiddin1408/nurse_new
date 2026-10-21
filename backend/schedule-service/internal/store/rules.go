package store

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/Muhiddin1408/nurse_new/backend/schedule-service/internal/domain"
)

const ruleCols = `id, doctor_id, clinic_id, room_id, weekday,
	(EXTRACT(EPOCH FROM start_time)::int / 60), (EXTRACT(EPOCH FROM end_time)::int / 60),
	slot_minutes, valid_from, valid_to`

func scanRule(row pgx.Row) (domain.Rule, error) {
	var r domain.Rule
	var wd, sm int16
	var start, end int
	err := row.Scan(&r.ID, &r.DoctorID, &r.ClinicID, &r.RoomID, &wd, &start, &end, &sm, &r.ValidFrom, &r.ValidTo)
	if errors.Is(err, pgx.ErrNoRows) {
		return r, ErrNotFound
	}
	r.Weekday, r.Start, r.End, r.SlotMinutes = int(wd), domain.Clock(start), domain.Clock(end), int(sm)
	return r, err
}

// Rules — shifokor qoidalari (validTo == nil yoki >= since).
func Rules(ctx context.Context, db DB, doctorID uuid.UUID) ([]domain.Rule, error) {
	return rulesWhere(ctx, db, `doctor_id = $1 ORDER BY weekday, start_time`, doctorID)
}

// RulesAtClinic — klinikadagi barcha qoidalar.
func RulesAtClinic(ctx context.Context, db DB, clinicID uuid.UUID) ([]domain.Rule, error) {
	return rulesWhere(ctx, db, `clinic_id = $1 ORDER BY doctor_id, weekday, start_time`, clinicID)
}

func rulesWhere(ctx context.Context, db DB, where string, args ...any) ([]domain.Rule, error) {
	rows, err := db.Query(ctx, `SELECT `+ruleCols+` FROM working_rules WHERE `+where, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []domain.Rule
	for rows.Next() {
		r, err := scanRule(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// GetRule — ID bo'yicha (qulf bilan).
func GetRule(ctx context.Context, db DB, id uuid.UUID) (domain.Rule, error) {
	return scanRule(db.QueryRow(ctx, `SELECT `+ruleCols+` FROM working_rules WHERE id = $1 FOR UPDATE`, id))
}

// SaveRule — insert yoki update.
func SaveRule(ctx context.Context, db DB, r domain.Rule) error {
	_, err := db.Exec(ctx, `INSERT INTO working_rules
		(id, doctor_id, clinic_id, room_id, weekday, start_time, end_time, slot_minutes, valid_from, valid_to)
		VALUES ($1, $2, $3, $4, $5, $6::text::time, $7::text::time, $8, $9, $10)
		ON CONFLICT (id) DO UPDATE SET clinic_id = EXCLUDED.clinic_id, room_id = EXCLUDED.room_id,
			weekday = EXCLUDED.weekday, start_time = EXCLUDED.start_time, end_time = EXCLUDED.end_time,
			slot_minutes = EXCLUDED.slot_minutes, valid_from = EXCLUDED.valid_from, valid_to = EXCLUDED.valid_to,
			updated_at = now()`,
		r.ID, r.DoctorID, r.ClinicID, r.RoomID, r.Weekday, r.Start.String(), r.End.String(), r.SlotMinutes,
		r.ValidFrom, r.ValidTo)
	return err
}

// DeleteRule — o'chirish.
func DeleteRule(ctx context.Context, db DB, id uuid.UUID) error {
	_, err := db.Exec(ctx, `DELETE FROM working_rules WHERE id = $1`, id)
	return err
}

// DoctorsWithRules — kelajakda amal qiladigan qoidasi bor shifokorlar (generatsiya uchun).
func DoctorsWithRules(ctx context.Context, db DB, today domain.Date) ([]uuid.UUID, error) {
	rows, err := db.Query(ctx, `SELECT DISTINCT doctor_id FROM working_rules WHERE valid_to IS NULL OR valid_to >= $1`, today)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, pgx.RowTo[uuid.UUID])
}

// --- dam olish kunlari, yopilishlar, ta'tillar ------------------------------------------------

// Exception — shifokorning butun kunlik dam olishi.
type Exception struct {
	ID     uuid.UUID   `json:"id"`
	Date   domain.Date `json:"-"`
	Reason string      `json:"reason"`
}

// ExceptionDates — [from, to] dagi dam olish kunlari.
func ExceptionDates(ctx context.Context, db DB, doctorID uuid.UUID, from, to domain.Date) (map[domain.Date]bool, error) {
	rows, err := db.Query(ctx, `SELECT date FROM schedule_exceptions WHERE doctor_id = $1 AND date BETWEEN $2 AND $3`,
		doctorID, from, to)
	if err != nil {
		return nil, err
	}
	dates, err := pgx.CollectRows(rows, pgx.RowTo[time.Time])
	out := make(map[domain.Date]bool, len(dates))
	for _, d := range dates {
		out[d] = true
	}
	return out, err
}

// ListExceptions — kelajakdagilar.
func ListExceptions(ctx context.Context, db DB, doctorID uuid.UUID, from domain.Date) ([]Exception, error) {
	rows, err := db.Query(ctx, `SELECT id, date, reason FROM schedule_exceptions WHERE doctor_id = $1 AND date >= $2 ORDER BY date`,
		doctorID, from)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (Exception, error) {
		var e Exception
		err := r.Scan(&e.ID, &e.Date, &e.Reason)
		return e, err
	})
}

// Closure — klinika yopiq kuni (D7).
type Closure struct {
	ID       uuid.UUID
	ClinicID uuid.UUID
	Date     domain.Date
	Reason   string
}

// ClosureSet — klinikalarning [from, to] dagi yopiq kunlari.
func ClosureSet(ctx context.Context, db DB, clinicIDs []uuid.UUID, from, to domain.Date) (map[domain.ClosureKey]bool, error) {
	out := map[domain.ClosureKey]bool{}
	if len(clinicIDs) == 0 {
		return out, nil
	}
	rows, err := db.Query(ctx, `SELECT clinic_id, date FROM clinic_closures WHERE clinic_id = ANY($1) AND date BETWEEN $2 AND $3`,
		clinicIDs, from, to)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var k domain.ClosureKey
		if err := rows.Scan(&k.ClinicID, &k.Date); err != nil {
			return nil, err
		}
		out[k] = true
	}
	return out, rows.Err()
}

// ListClosures — klinikaning kelajakdagi yopiq kunlari.
func ListClosures(ctx context.Context, db DB, clinicID uuid.UUID, from domain.Date) ([]Closure, error) {
	rows, err := db.Query(ctx, `SELECT id, clinic_id, date, reason FROM clinic_closures WHERE clinic_id = $1 AND date >= $2 ORDER BY date`,
		clinicID, from)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (Closure, error) {
		var c Closure
		err := r.Scan(&c.ID, &c.ClinicID, &c.Date, &c.Reason)
		return c, err
	})
}

// TimeOff — ta'til (D3).
type TimeOff struct {
	ID      uuid.UUID
	StartAt time.Time
	EndAt   time.Time
	Kind    string
	Reason  string
}

// TimeOffs — [from, to) bilan kesishuvchi ta'tillar.
func TimeOffs(ctx context.Context, db DB, doctorID uuid.UUID, from, to time.Time) ([]TimeOff, error) {
	rows, err := db.Query(ctx, `SELECT id, start_at, end_at, kind, reason FROM time_offs
		WHERE doctor_id = $1 AND start_at < $3 AND end_at > $2 ORDER BY start_at`, doctorID, from, to)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (TimeOff, error) {
		var t TimeOff
		err := r.Scan(&t.ID, &t.StartAt, &t.EndAt, &t.Kind, &t.Reason)
		t.StartAt, t.EndAt = t.StartAt.UTC(), t.EndAt.UTC()
		return t, err
	})
}

// --- narx qoidalari ---------------------------------------------------------------------------

const priceCols = `id, doctor_id, name, weekdays, (EXTRACT(EPOCH FROM start_time)::int / 60),
	(EXTRACT(EPOCH FROM end_time)::int / 60), percent, priority, is_active, created_at`

func scanPrice(row pgx.Row) (domain.PriceRule, error) {
	var p domain.PriceRule
	var start, end int
	var pct, prio int16
	err := row.Scan(&p.ID, &p.DoctorID, &p.Name, &p.Weekdays, &start, &end, &pct, &prio, &p.IsActive, &p.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return p, ErrNotFound
	}
	p.Start, p.End, p.Percent, p.Priority = domain.Clock(start), domain.Clock(end), int(pct), int(prio)
	return p, err
}

// PriceRules — shifokorning barcha narx qoidalari (faol emaslari ham — Matches o'zi filtrlaydi).
func PriceRules(ctx context.Context, db DB, doctorID uuid.UUID) ([]domain.PriceRule, error) {
	rows, err := db.Query(ctx, `SELECT `+priceCols+` FROM price_rules WHERE doctor_id = $1 ORDER BY priority, created_at`, doctorID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []domain.PriceRule
	for rows.Next() {
		p, err := scanPrice(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

// GetPriceRule — ID bo'yicha.
func GetPriceRule(ctx context.Context, db DB, id uuid.UUID) (domain.PriceRule, error) {
	return scanPrice(db.QueryRow(ctx, `SELECT `+priceCols+` FROM price_rules WHERE id = $1`, id))
}

// SavePriceRule — insert yoki update.
func SavePriceRule(ctx context.Context, db DB, p domain.PriceRule) error {
	_, err := db.Exec(ctx, `INSERT INTO price_rules (id, doctor_id, name, weekdays, start_time, end_time, percent, priority, is_active)
		VALUES ($1, $2, $3, $4, $5::text::time, $6::text::time, $7, $8, $9)
		ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, weekdays = EXCLUDED.weekdays, start_time = EXCLUDED.start_time,
			end_time = EXCLUDED.end_time, percent = EXCLUDED.percent, priority = EXCLUDED.priority,
			is_active = EXCLUDED.is_active, updated_at = now()`,
		p.ID, p.DoctorID, p.Name, p.Weekdays, p.Start.String(), p.End.String(), p.Percent, p.Priority, p.IsActive)
	return err
}

// --- proyeksiyalar ------------------------------------------------------------------------------

// Doctor — catalog'dan.
type Doctor struct {
	DoctorID          uuid.UUID
	UserID            uuid.UUID
	Status            string
	AcceptsHomeVisits bool
}

// GetDoctor — proyeksiya.
func GetDoctor(ctx context.Context, db DB, doctorID uuid.UUID) (Doctor, error) {
	var d Doctor
	err := db.QueryRow(ctx, `SELECT doctor_id, user_id, status, accepts_home_visits FROM doctors WHERE doctor_id = $1`,
		doctorID).Scan(&d.DoctorID, &d.UserID, &d.Status, &d.AcceptsHomeVisits)
	if errors.Is(err, pgx.ErrNoRows) {
		return d, ErrNotFound
	}
	return d, err
}

// UpsertDoctor — faqat yangiroq event qo'llanadi. accepts=nil — o'zgartirilmaydi. Qaytaradi: qo'llandimi.
func UpsertDoctor(ctx context.Context, db DB, doctorID, userID uuid.UUID, status string, accepts *bool, at time.Time) (bool, error) {
	n, err := Exec(ctx, db, `INSERT INTO doctors (doctor_id, user_id, status, accepts_home_visits, changed_at)
		VALUES ($1, $2, $3, COALESCE($4, false), $5)
		ON CONFLICT (doctor_id) DO UPDATE SET status = EXCLUDED.status, user_id = EXCLUDED.user_id,
			accepts_home_visits = COALESCE($4, doctors.accepts_home_visits), changed_at = EXCLUDED.changed_at
		WHERE doctors.changed_at < EXCLUDED.changed_at`, doctorID, userID, status, accepts, at)
	return n > 0, err
}

// UpsertClinic — klinika holati.
func UpsertClinic(ctx context.Context, db DB, clinicID uuid.UUID, status string, at time.Time) (bool, error) {
	n, err := Exec(ctx, db, `INSERT INTO clinics (clinic_id, status, changed_at) VALUES ($1, $2, $3)
		ON CONFLICT (clinic_id) DO UPDATE SET status = EXCLUDED.status, changed_at = EXCLUDED.changed_at
		WHERE clinics.changed_at < EXCLUDED.changed_at`, clinicID, status, at)
	return n > 0, err
}

// UpsertAffiliation — hamkorlik holati.
func UpsertAffiliation(ctx context.Context, db DB, id, doctorID, clinicID uuid.UUID, status string, at time.Time) (bool, error) {
	n, err := Exec(ctx, db, `INSERT INTO affiliations (affiliation_id, doctor_id, clinic_id, status, changed_at)
		VALUES ($1, $2, $3, $4, $5)
		ON CONFLICT (affiliation_id) DO UPDATE SET status = EXCLUDED.status, changed_at = EXCLUDED.changed_at
		WHERE affiliations.changed_at < EXCLUDED.changed_at`, id, doctorID, clinicID, status, at)
	return n > 0, err
}

// ActiveClinics — shifokorning faol hamkorlikdagi faol klinikalari.
func ActiveClinics(ctx context.Context, db DB, doctorID uuid.UUID) (map[uuid.UUID]bool, error) {
	rows, err := db.Query(ctx, `SELECT a.clinic_id FROM affiliations a JOIN clinics c ON c.clinic_id = a.clinic_id
		WHERE a.doctor_id = $1 AND a.status = 'active' AND c.status = 'active'`, doctorID)
	if err != nil {
		return nil, err
	}
	ids, err := pgx.CollectRows(rows, pgx.RowTo[uuid.UUID])
	out := make(map[uuid.UUID]bool, len(ids))
	for _, id := range ids {
		out[id] = true
	}
	return out, err
}

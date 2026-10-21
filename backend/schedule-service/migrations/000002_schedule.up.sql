-- schedule-service sxemasi: proyeksiyalar (catalog eventlari), jadval qoidalari, narx qoidalari, slotlar.
-- Vaqt — TIMESTAMPTZ (UTC); "kun" tushunchasi va qoida soatlari — Asia/Tashkent (domain.LocalTZ).

-- --- Proyeksiyalar (egasi catalog-service; changed_at — tartibsiz eventlardan himoya) ---------
CREATE TABLE doctors (
    doctor_id           UUID        PRIMARY KEY,
    user_id             UUID        NOT NULL,
    status              VARCHAR(16) NOT NULL CHECK (status IN ('approved', 'suspended')),
    accepts_home_visits BOOLEAN     NOT NULL DEFAULT false,
    changed_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE clinics (
    clinic_id  UUID        PRIMARY KEY,
    status     VARCHAR(16) NOT NULL CHECK (status IN ('active', 'suspended')),
    changed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE affiliations (
    affiliation_id UUID        PRIMARY KEY,
    doctor_id      UUID        NOT NULL,
    clinic_id      UUID        NOT NULL,
    status         VARCHAR(16) NOT NULL CHECK (status IN ('active', 'paused', 'ended')),
    changed_at     TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_affiliations_doctor_clinic ON affiliations (doctor_id, clinic_id);

-- --- Jadval -----------------------------------------------------------------------------------
CREATE TABLE working_rules (
    id           UUID        PRIMARY KEY,
    doctor_id    UUID        NOT NULL,
    clinic_id    UUID,                          -- NULL — uy chaqiruvi
    room_id      UUID,                          -- D8: faqat klinika admini
    weekday      SMALLINT    NOT NULL CHECK (weekday BETWEEN 0 AND 6),   -- 0 = dushanba
    start_time   TIME        NOT NULL,
    end_time     TIME        NOT NULL,
    slot_minutes SMALLINT    NOT NULL,
    valid_from   DATE        NOT NULL,
    valid_to     DATE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_working_rules_time CHECK (end_time > start_time),
    CONSTRAINT ck_working_rules_valid CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX ix_working_rules_doctor_weekday ON working_rules (doctor_id, weekday);
CREATE INDEX ix_working_rules_clinic ON working_rules (clinic_id) WHERE clinic_id IS NOT NULL;

CREATE TABLE schedule_exceptions (
    id         UUID         PRIMARY KEY,
    doctor_id  UUID         NOT NULL,
    date       DATE         NOT NULL,
    reason     VARCHAR(255) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_schedule_exceptions_doctor_date UNIQUE (doctor_id, date)
);

CREATE TABLE clinic_closures (
    id         UUID         PRIMARY KEY,
    clinic_id  UUID         NOT NULL,
    date       DATE         NOT NULL,
    reason     VARCHAR(255) NOT NULL DEFAULT '',
    created_by UUID,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_clinic_closures_clinic_date UNIQUE (clinic_id, date)
);

CREATE TABLE time_offs (
    id         UUID         PRIMARY KEY,
    doctor_id  UUID         NOT NULL,
    start_at   TIMESTAMPTZ  NOT NULL,
    end_at     TIMESTAMPTZ  NOT NULL,
    kind       VARCHAR(20)  NOT NULL DEFAULT 'other' CHECK (kind IN ('vacation', 'sick', 'personal', 'other')),
    reason     VARCHAR(255) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_time_offs_range CHECK (end_at > start_at)
);
CREATE INDEX ix_time_offs_doctor_range ON time_offs (doctor_id, start_at, end_at);

-- C12: vaqtga qarab narx foizi (monolitda catalog'da edi — ADR-0011)
CREATE TABLE price_rules (
    id         UUID         PRIMARY KEY,
    doctor_id  UUID         NOT NULL,
    name       VARCHAR(100) NOT NULL DEFAULT '',
    weekdays   VARCHAR(7)   NOT NULL DEFAULT '',  -- "0".."6" raqamlari; bo'sh — har kuni
    start_time TIME         NOT NULL,
    end_time   TIME         NOT NULL,             -- [start, end); start > end — yarim tundan o'tadi
    percent    SMALLINT     NOT NULL CHECK (percent BETWEEN -90 AND 100),
    priority   SMALLINT     NOT NULL DEFAULT 100,
    is_active  BOOLEAN      NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_price_rules_weekdays CHECK (weekdays ~ '^[0-6]*$'),
    CONSTRAINT ck_price_rules_time CHECK (start_time <> end_time)
);
CREATE INDEX ix_price_rules_doctor ON price_rules (doctor_id, is_active, priority);

-- --- Slotlar ----------------------------------------------------------------------------------
CREATE TABLE slots (
    id                    UUID         PRIMARY KEY,
    doctor_id             UUID         NOT NULL,
    clinic_id             UUID,                        -- NULL — uy chaqiruvi
    room_id               UUID,
    start_at              TIMESTAMPTZ  NOT NULL,
    end_at                TIMESTAMPTZ  NOT NULL,
    status                VARCHAR(10)  NOT NULL DEFAULT 'free' CHECK (status IN ('free', 'held', 'booked', 'blocked')),
    hold_ref              VARCHAR(64),                 -- booking_id
    hold_expires_at       TIMESTAMPTZ,
    block_reason          VARCHAR(24)  NOT NULL DEFAULT '',
    block_note            VARCHAR(255) NOT NULL DEFAULT '',
    time_off_id           UUID REFERENCES time_offs (id) ON DELETE SET NULL,
    -- ADR-0011 #4: travel_buffer bloki qaysi uy chaqiruvi sloti tufayli — faqat o'shani bo'shatish ochadi
    blocked_by_slot_id    UUID,
    travel_buffer_minutes SMALLINT,
    -- Bron tarixi bor (hech bo'lmasa bir marta band qilingan) — retention o'chirmaydi
    ever_held             BOOLEAN      NOT NULL DEFAULT false,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_slots_doctor_start UNIQUE (doctor_id, start_at),
    CONSTRAINT ck_slots_range CHECK (end_at > start_at),
    CONSTRAINT ck_slots_hold CHECK (status NOT IN ('held', 'booked') OR hold_ref IS NOT NULL)
);
CREATE UNIQUE INDEX uq_slots_room_start ON slots (room_id, start_at) WHERE room_id IS NOT NULL;
CREATE INDEX ix_slots_doctor_status_start ON slots (doctor_id, status, start_at);
CREATE INDEX ix_slots_clinic_status_start ON slots (clinic_id, status, start_at);
CREATE INDEX ix_slots_held_expiry ON slots (hold_expires_at) WHERE status = 'held';
CREATE INDEX ix_slots_blocked_by ON slots (blocked_by_slot_id) WHERE blocked_by_slot_id IS NOT NULL;
CREATE INDEX ix_slots_hold_ref ON slots (hold_ref) WHERE hold_ref IS NOT NULL;

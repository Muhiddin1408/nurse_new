# ===========================================================================
#
# E14: saqlash muddati. `SCHEMA` `.format()` bilan to'ldiriladi
# (`analytics/aggregates.py: ensure_schema`).
FACT_TTL_DAYS = 730  # 2 yil — xom hodisalar; agregat undan uzoq yashaydi

SCHEMA = """
-- ─────────────────────────────────────────────────────────────────────────
-- FAKT JADVALI: o'lchanadigan hodisalar. Har qatorda BITTA bron.
-- Faktlar (o'lchamlar) + o'lchov jadvallariga kalitlar (dimension keys).
--
-- MuhIM: bu jadval PostgreSQL'dagi Booking'ning nusxasi EMAS. U ataylab
-- DENORMALIZATSIYA qilingan — analitikada join qimmat, shuning uchun
-- tez-tez kerak bo'ladigan maydonlar shu yerga tekislab yoziladi.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fact_booking (
    event_id        String,            -- dedup kaliti (E4)
    booking_id      UUID,
    created_date    Date,              -- dim_time bilan bog'lanish (partition kaliti ham)
    doctor_id       UUID,
    clinic_id       Nullable(UUID),
    specialization  LowCardinality(String),  -- LowCardinality = kam xil qiymat, ES tejaydi
    place           LowCardinality(String),  -- 'clinic' | 'home'
    city            LowCardinality(String),

    -- O'LCHAMLAR (measures) — bular ustidan SUM, AVG, COUNT qilinadi
    total_price     UInt64,
    status          LowCardinality(String),  -- 'confirmed' | 'cancelled' | 'expired'...
    is_cancelled    UInt8,             -- 0/1 — bekor qilish darajasini tez hisoblash uchun
    is_home_visit   UInt8,

    patient_age_group LowCardinality(String), -- '0-17' | '18-40' | '41-65' | '65+'
    patient_gender    LowCardinality(String),
    client_hash       String            -- A10: HMAC(telefon), raqamning o'zi EMAS
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(created_date)   -- oylar bo'yicha bo'linadi — eski oylarni arxivlash oson
ORDER BY (created_date, specialization, city, event_id);
-- ⬆ ORDER BY = ClickHouse'ning asosiy indeksi VA ReplacingMergeTree'ning
-- dedup kaliti. `event_id` oxirida: at-least-once yetkazishda takrorlangan
-- hodisa (Kafka rebalance) bir xil kalit bilan keladi va birlashtiriladi.
-- Birlashish fonda bo'ladi — shuning uchun so'rovlarda `FINAL` (queries.py).

-- O'LCHOV JADVALLARI (dimensions) — kamdan-kam o'zgaradigan kontekst.
-- Bular kichik, shuning uchun to'liq join qilinadi.
CREATE TABLE IF NOT EXISTS dim_doctor (
    doctor_id       UUID,
    full_name       String,
    specialization  LowCardinality(String),
    experience_years UInt8,
    rating          Float32
) ENGINE = ReplacingMergeTree() ORDER BY doctor_id;
-- ReplacingMergeTree: bir xil kalitli yangi qator eskisini almashtiradi —
-- shifokor ma'lumoti yangilanganda ishlatiladi.

-- A10: eski jadvallarga ustun qo'shish (idempotent)
ALTER TABLE fact_booking ADD COLUMN IF NOT EXISTS client_hash String;

-- ─────────────────────────────────────────────────────────────────────────
-- E14: XOM JADVALNI ABADIY SAQLAMANG.
-- `created_date` bo'yicha TTL — muddati o'tgan oylik partitionlar butunlay
-- o'chadi (partition kaliti ham `created_date`, ya'ni o'chirish arzon).
-- Tarixiy tendensiya AGREGATDA qoladi, u ancha kichik va uzoqroq yashaydi.
-- ─────────────────────────────────────────────────────────────────────────
ALTER TABLE fact_booking MODIFY TTL created_date + INTERVAL {fact_ttl_days} DAY DELETE;

-- ─────────────────────────────────────────────────────────────────────────
-- E14: KUNLIK AGREGAT. Dashboard xom hodisalarni emas, SHUNI o'qiydi.
--
-- NEGA MATERIALIZED VIEW EMAS:
--     MV har INSERT'da ishlaydi. `fact_booking` ReplacingMergeTree, ya'ni
--     takrorlangan hodisa (Kafka rebalance, at-least-once) jadvalga YOZILADI
--     va faqat fonda birlashtiriladi. MV esa o'sha takrorni ko'rib, summani
--     ikki marta qo'shib yuborardi — va buni hech kim sezmasdi, chunki
--     daromad grafigi "ishonchli" ko'rinib turaveradi.
--
--     Shuning uchun agregat `FINAL` bilan QAYTA HISOBLANADI (kuniga bir
--     marta, oxirgi N kun). Sekinroq, lekin takrorlarga ham, kech kelgan
--     hodisalarga ham chidamli.
--
-- ReplacingMergeTree + `computed_at`: qayta hisoblash eski qatorni
-- almashtiradi, o'chirish kerak emas.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_booking_agg (
    created_date    Date,
    specialization  LowCardinality(String),
    city            LowCardinality(String),
    place           LowCardinality(String),
    status          LowCardinality(String),

    bookings        UInt64,
    revenue         UInt64,
    cancelled       UInt64,
    home_visits     UInt64,
    unique_clients  UInt64,

    computed_at     DateTime
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(created_date)
ORDER BY (created_date, specialization, city, place, status);
"""

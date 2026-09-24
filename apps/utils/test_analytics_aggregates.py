"""E14 — ClickHouse TTL va kunlik agregat (`daily_booking_agg`)."""

from analytics import aggregates
from analytics.queries import AGG_QUERIES, QUERIES
from analytics.schema import FACT_TTL_DAYS, SCHEMA


class FakeClient:
    """ClickHouse o'rniga — testda haqiqiy klaster yo'q."""

    def __init__(self, count=42):
        self.commands = []
        self.count = count

    def command(self, sql):
        self.commands.append(sql)
        return self.count if sql.lstrip().upper().startswith("SELECT") else None


def test_xom_jadvalda_ttl_bor():
    """E14: `fact_booking` abadiy o'smasligi kerak."""
    sql = SCHEMA.format(fact_ttl_days=FACT_TTL_DAYS)
    assert f"MODIFY TTL created_date + INTERVAL {FACT_TTL_DAYS} DAY DELETE" in sql


def test_agregat_jadvali_oylar_boyicha_bolinadi():
    agg = SCHEMA.split("CREATE TABLE IF NOT EXISTS daily_booking_agg")[1]
    assert "ReplacingMergeTree(computed_at)" in agg
    assert "PARTITION BY toYYYYMM(created_date)" in agg


def test_qayta_hisoblash_final_bilan_oqiydi():
    """Takrorlangan hodisa (Kafka at-least-once) daromadni ikki marta
    qo'shmasligi uchun — agregat AYNAN `FINAL` dan yig'iladi."""
    client = FakeClient()
    aggregates.rebuild(days=3, client=client)
    insert = client.commands[0]
    assert "FROM fact_booking FINAL" in insert
    assert "INSERT INTO daily_booking_agg" in insert
    assert "today() - 3" in insert


def test_qayta_hisoblash_yozilgan_qatorni_qaytaradi():
    assert aggregates.rebuild(days=1, client=FakeClient(count=7)) == 7


def test_agregat_sorovlari_agregatdan_oqiydi():
    """Agregat so'rovi xom jadvalga qaytib ketmasin — aks holda uni
    qurishning ma'nosi yo'q."""
    for name, q in AGG_QUERIES.items():
        assert "daily_booking_agg FINAL" in q, name
        assert "fact_booking" not in q, name


def test_agregat_va_xom_sorovlar_ismlari_kesishmaydi():
    """Ikkala lug'atda bir xil kalit bo'lsa, dashboard qaysi biridan
    o'qiyotganini aytib bo'lmasdi."""
    assert not (set(QUERIES) & set(AGG_QUERIES))

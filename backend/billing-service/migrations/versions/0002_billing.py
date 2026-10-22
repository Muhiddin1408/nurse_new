"""billing: daromad daftari (earning_entries), payout davrlari, to'lov rekvizitlari (ADR-0012)

Revision ID: 0002_billing
Revises: 0001_eventbus
Create Date: 2026-10-21 13:30:37.938038
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_billing"
down_revision: str | None = "0001_eventbus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payout_accounts",
        sa.Column("doctor_id", sa.UUID(), nullable=False),
        sa.Column("holder_name", sa.String(length=255), nullable=False),
        sa.Column("bank_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("account_number", sa.String(length=34), nullable=False),
        sa.Column("mfo", sa.String(length=10), server_default="", nullable=False),
        sa.Column("inn", sa.String(length=14), server_default="", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("doctor_id", name=op.f("pk_payout_accounts")),
    )
    op.create_table(
        "payouts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="pending", nullable=False),
        sa.Column("bookings_count", sa.Integer(), nullable=False),
        sa.Column("gross_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("platform_fee_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("provider_fee_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("refunds_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("net_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_by", sa.UUID(), nullable=True),
        sa.Column("bank_reference", sa.String(length=255), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payouts")),
        sa.UniqueConstraint("doctor_id", "period_start", "period_end", name="uq_payouts_doctor_period"),
    )
    op.create_index("ix_payouts_doctor", "payouts", ["doctor_id", "period_end"], unique=False)
    op.create_index("ix_payouts_status_period", "payouts", ["status", "period_end"], unique=False)
    op.create_table(
        "earning_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source_ref", sa.String(length=64), nullable=False),
        sa.Column("booking_id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=True),
        sa.Column("clinic_id", sa.UUID(), nullable=True),
        sa.Column("booking_number", sa.String(length=20), server_default="", nullable=False),
        sa.Column("booking_status", sa.String(length=20), server_default="", nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("services", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("amount_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("gross_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("platform_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("provider_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payout_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["payout_id"], ["payouts.id"], name=op.f("fk_earning_entries_payout_id_payouts")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_earning_entries")),
        sa.UniqueConstraint("kind", "source_ref", name="uq_earning_entries_kind_source"),
    )
    op.create_index("ix_earning_entries_booking", "earning_entries", ["booking_id"], unique=False)
    op.create_index(
        "ix_earning_entries_clinic_start",
        "earning_entries",
        ["clinic_id", "start_at"],
        unique=False,
        postgresql_where=sa.text("clinic_id IS NOT NULL"),
    )
    op.create_index("ix_earning_entries_doctor_start", "earning_entries", ["doctor_id", "start_at"], unique=False)
    op.create_index(
        "ix_earning_entries_payout",
        "earning_entries",
        ["payout_id"],
        unique=False,
        postgresql_where=sa.text("payout_id IS NOT NULL"),
    )
    op.create_index(
        "ix_earning_entries_unpaid",
        "earning_entries",
        ["doctor_id", "eligible_at"],
        unique=False,
        postgresql_where=sa.text("payout_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_earning_entries_unpaid", table_name="earning_entries", postgresql_where=sa.text("payout_id IS NULL")
    )
    op.drop_index(
        "ix_earning_entries_payout", table_name="earning_entries", postgresql_where=sa.text("payout_id IS NOT NULL")
    )
    op.drop_index("ix_earning_entries_doctor_start", table_name="earning_entries")
    op.drop_index(
        "ix_earning_entries_clinic_start",
        table_name="earning_entries",
        postgresql_where=sa.text("clinic_id IS NOT NULL"),
    )
    op.drop_index("ix_earning_entries_booking", table_name="earning_entries")
    op.drop_table("earning_entries")
    op.drop_index("ix_payouts_status_period", table_name="payouts")
    op.drop_index("ix_payouts_doctor", table_name="payouts")
    op.drop_table("payouts")
    op.drop_table("payout_accounts")

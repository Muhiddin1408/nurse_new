"""payment: to'lovlar, refundlar, ikki yozuvli daftar, reconciliation farqlari, provayder jurnali (ADR-0012)

Revision ID: 0002_payment
Revises: 0001_eventbus
Create Date: 2026-10-21 13:38:48.273958
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_payment"
down_revision: str | None = "0001_eventbus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discrepancies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=10), server_default="", nullable=False),
        sa.Column("external_id", sa.String(length=64), server_default="", nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=True),
        sa.Column("booking_id", sa.UUID(), nullable=True),
        sa.Column("local_tiyin", sa.BigInteger(), nullable=True),
        sa.Column("provider_tiyin", sa.BigInteger(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="open", nullable=False),
        sa.Column("resolution_note", sa.Text(), server_default="", nullable=False),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column("fingerprint", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discrepancies")),
    )
    op.create_index("ix_discrepancies_status_type", "discrepancies", ["status", "type"], unique=False)
    op.create_index("uq_discrepancies_fingerprint_day", "discrepancies", ["fingerprint", "day"], unique=True)
    op.create_table(
        "ledger_transactions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("ref_type", sa.String(length=20), nullable=False),
        sa.Column("ref_id", sa.UUID(), nullable=False),
        sa.Column("booking_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.String(length=255), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_transactions")),
    )
    op.create_index(
        "ix_ledger_booking",
        "ledger_transactions",
        ["booking_id"],
        unique=False,
        postgresql_where=sa.text("booking_id IS NOT NULL"),
    )
    op.create_index("uq_ledger_kind_ref", "ledger_transactions", ["kind", "ref_id"], unique=True)
    op.create_table(
        "payments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("booking_id", sa.UUID(), nullable=False),
        sa.Column("booking_number", sa.String(length=20), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=False),
        sa.Column("clinic_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=10), nullable=False),
        sa.Column("amount_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(length=64), server_default="", nullable=False),
        sa.Column("provider_create_time", sa.BigInteger(), nullable=True),
        sa.Column("perform_time", sa.BigInteger(), nullable=True),
        sa.Column("cancel_time", sa.BigInteger(), nullable=True),
        sa.Column("cancel_reason", sa.SmallInteger(), nullable=True),
        sa.Column("provider_state", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("money", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("receipt_items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("needs_refund", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fiscal_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_tiyin > 0", name=op.f("ck_payments_amount_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
    )
    op.create_index("ix_payments_booking_status", "payments", ["booking_id", "status"], unique=False)
    op.create_index(
        "ix_payments_fiscal_pending",
        "payments",
        ["updated_at"],
        unique=False,
        postgresql_where=sa.text("provider = 'click' AND status = 'succeeded' AND fiscal_submitted_at IS NULL"),
    )
    op.create_index(
        "ix_payments_open_created",
        "payments",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('created', 'processing')"),
    )
    op.create_index("ix_payments_provider_create_time", "payments", ["provider", "provider_create_time"], unique=False)
    op.create_index(
        "uq_payments_money_taken_per_booking",
        "payments",
        ["booking_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('succeeded', 'partially_refunded', 'refunded', 'reversed')"),
    )
    op.create_index(
        "uq_payments_provider_external",
        "payments",
        ["provider", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id <> ''"),
    )
    op.create_table(
        "provider_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=10), nullable=False),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_logs")),
    )
    op.create_index(op.f("ix_provider_logs_created_at"), "provider_logs", ["created_at"], unique=False)
    op.create_index(
        "ix_provider_logs_provider_op_created", "provider_logs", ["provider", "operation", "created_at"], unique=False
    )
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transaction_id", sa.UUID(), nullable=False),
        sa.Column("account", sa.String(length=30), nullable=False),
        sa.Column("debit_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("credit_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "debit_tiyin >= 0 AND credit_tiyin >= 0 AND (debit_tiyin = 0 OR credit_tiyin = 0)",
            name=op.f("ck_ledger_entries_one_side_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["ledger_transactions.id"],
            name=op.f("fk_ledger_entries_transaction_id_ledger_transactions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
    )
    op.create_index("ix_ledger_entries_account_doctor", "ledger_entries", ["account", "doctor_id"], unique=False)
    op.create_index("ix_ledger_entries_transaction", "ledger_entries", ["transaction_id"], unique=False)
    op.create_table(
        "refunds",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=False),
        sa.Column("booking_id", sa.UUID(), nullable=False),
        sa.Column("amount_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("doctor_share_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("initiated_by", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("external_refund_id", sa.String(length=64), server_default="", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), server_default="", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_tiyin > 0", name=op.f("ck_refunds_amount_positive")),
        sa.CheckConstraint("doctor_share_tiyin >= 0", name=op.f("ck_refunds_doctor_share_non_negative")),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], name=op.f("fk_refunds_payment_id_payments")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refunds")),
    )
    op.create_index("ix_refunds_booking", "refunds", ["booking_id"], unique=False)
    op.create_index(
        "ix_refunds_due", "refunds", ["next_attempt_at"], unique=False, postgresql_where=sa.text("status = 'pending'")
    )
    op.create_index("uq_refunds_source", "refunds", ["source", "source_ref"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_refunds_source", table_name="refunds")
    op.drop_index("ix_refunds_due", table_name="refunds", postgresql_where=sa.text("status = 'pending'"))
    op.drop_index("ix_refunds_booking", table_name="refunds")
    op.drop_table("refunds")
    op.drop_index("ix_ledger_entries_transaction", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_account_doctor", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_provider_logs_provider_op_created", table_name="provider_logs")
    op.drop_index(op.f("ix_provider_logs_created_at"), table_name="provider_logs")
    op.drop_table("provider_logs")
    op.drop_index("uq_payments_provider_external", table_name="payments", postgresql_where=sa.text("external_id <> ''"))
    op.drop_index(
        "uq_payments_money_taken_per_booking",
        table_name="payments",
        postgresql_where=sa.text("status IN ('succeeded', 'partially_refunded', 'refunded', 'reversed')"),
    )
    op.drop_index("ix_payments_provider_create_time", table_name="payments")
    op.drop_index(
        "ix_payments_open_created",
        table_name="payments",
        postgresql_where=sa.text("status IN ('created', 'processing')"),
    )
    op.drop_index(
        "ix_payments_fiscal_pending",
        table_name="payments",
        postgresql_where=sa.text("provider = 'click' AND status = 'succeeded' AND fiscal_submitted_at IS NULL"),
    )
    op.drop_index("ix_payments_booking_status", table_name="payments")
    op.drop_table("payments")
    op.drop_index("uq_ledger_kind_ref", table_name="ledger_transactions")
    op.drop_index(
        "ix_ledger_booking", table_name="ledger_transactions", postgresql_where=sa.text("booking_id IS NOT NULL")
    )
    op.drop_table("ledger_transactions")
    op.drop_index("uq_discrepancies_fingerprint_day", table_name="discrepancies")
    op.drop_index("ix_discrepancies_status_type", table_name="discrepancies")
    op.drop_table("discrepancies")

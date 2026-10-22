"""dispute: nizolar (holat mashinasi, SLA, yechim) va dalillar (file-service fayl ID'lari)

Revision ID: 0002_dispute
Revises: 0001_eventbus
Create Date: 2026-10-21 14:38:03.428406
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_dispute"
down_revision: str | None = "0001_eventbus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "disputes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("booking_id", sa.UUID(), nullable=False),
        sa.Column("booking_number", sa.String(length=20), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=False),
        sa.Column("paid_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sla_breached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(length=20), nullable=True),
        sa.Column("refund_tiyin", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("doctor_liable", sa.Boolean(), nullable=True),
        sa.Column("resolution_note", sa.Text(), server_default="", nullable=False),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refund_id", sa.UUID(), nullable=True),
        sa.Column("refunded_tiyin", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("refund_tiyin >= 0 AND refunded_tiyin >= 0", name=op.f("ck_disputes_amounts_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_disputes")),
    )
    op.create_index("ix_disputes_client_created", "disputes", ["client_id", "created_at"], unique=False)
    op.create_index(
        "ix_disputes_sla_pending",
        "disputes",
        ["due_at"],
        unique=False,
        postgresql_where=sa.text("status = 'open' AND sla_breached_at IS NULL"),
    )
    op.create_index("ix_disputes_status_due", "disputes", ["status", "due_at"], unique=False)
    op.create_index("uq_disputes_booking", "disputes", ["booking_id"], unique=True)
    op.create_table(
        "dispute_evidence",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dispute_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("added_by", sa.UUID(), nullable=False),
        sa.Column("content_type", sa.String(length=64), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["dispute_id"], ["disputes.id"], name=op.f("fk_dispute_evidence_dispute_id_disputes")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dispute_evidence")),
    )
    op.create_index("uq_dispute_evidence_file", "dispute_evidence", ["dispute_id", "file_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_dispute_evidence_file", table_name="dispute_evidence")
    op.drop_table("dispute_evidence")
    op.drop_index("uq_disputes_booking", table_name="disputes")
    op.drop_index("ix_disputes_status_due", table_name="disputes")
    op.drop_index(
        "ix_disputes_sla_pending",
        table_name="disputes",
        postgresql_where=sa.text("status = 'open' AND sla_breached_at IS NULL"),
    )
    op.drop_index("ix_disputes_client_created", table_name="disputes")
    op.drop_table("disputes")

"""review: sharhlar (moderatsiya, shifokor javobi) va reviewable_bookings proyeksiyasi

Revision ID: 0002_review
Revises: 0001_eventbus
Create Date: 2026-10-21 14:36:07.896729
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_review"
down_revision: str | None = "0001_eventbus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reviewable_bookings",
        sa.Column("booking_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.String(length=20), server_default="", nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=False),
        sa.Column("clinic_id", sa.UUID(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("booking_id", name=op.f("pk_reviewable_bookings")),
    )
    op.create_table(
        "reviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("booking_id", sa.UUID(), nullable=False),
        sa.Column("booking_number", sa.String(length=20), server_default="", nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=False),
        sa.Column("clinic_id", sa.UUID(), nullable=True),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.Text(), server_default="", nullable=False),
        sa.Column("is_anonymous", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("author_name", sa.String(length=64), server_default="", nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("moderation_flags", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("moderated_by", sa.UUID(), nullable=True),
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("doctor_reply", sa.Text(), server_default="", nullable=False),
        sa.Column("doctor_replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name=op.f("ck_reviews_rating_1_5")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        sa.UniqueConstraint("booking_id", name=op.f("uq_reviews_booking_id")),
    )
    op.create_index("ix_reviews_client", "reviews", ["client_id", "created_at"], unique=False)
    op.create_index(
        "ix_reviews_clinic_published",
        "reviews",
        ["clinic_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index(
        "ix_reviews_doctor_published",
        "reviews",
        ["doctor_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index("ix_reviews_status_created", "reviews", ["status", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_reviews_status_created", table_name="reviews")
    op.drop_index("ix_reviews_doctor_published", table_name="reviews", postgresql_where=sa.text("status = 'published'"))
    op.drop_index("ix_reviews_clinic_published", table_name="reviews", postgresql_where=sa.text("status = 'published'"))
    op.drop_index("ix_reviews_client", table_name="reviews")
    op.drop_table("reviews")
    op.drop_table("reviewable_bookings")

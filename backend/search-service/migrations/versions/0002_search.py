"""search: proyeksiya (shifokorlar, klinikalar, hamkorliklar, xizmatlar, mutaxassisliklar), reindex navbati

Revision ID: 0002_search
Revises: 0001_eventbus
Create Date: 2026-10-21 14:50:31.467428
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_search"
down_revision: str | None = "0001_eventbus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "affiliations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=False),
        sa.Column("clinic_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("'1970-01-01T00:00:00Z'"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_affiliations")),
    )
    op.create_index("ix_affiliations_clinic", "affiliations", ["clinic_id"], unique=False)
    op.create_index("ix_affiliations_doctor", "affiliations", ["doctor_id"], unique=False)
    op.create_table(
        "clinics",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("city", sa.String(length=100), server_default="", nullable=False),
        sa.Column("street", sa.String(length=255), server_default="", nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="unknown", nullable=False),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("reviews_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "info_at", sa.DateTime(timezone=True), server_default=sa.text("'1970-01-01T00:00:00Z'"), nullable=False
        ),
        sa.Column(
            "status_at", sa.DateTime(timezone=True), server_default=sa.text("'1970-01-01T00:00:00Z'"), nullable=False
        ),
        sa.Column(
            "rating_at", sa.DateTime(timezone=True), server_default=sa.text("'1970-01-01T00:00:00Z'"), nullable=False
        ),
        sa.Column("doc_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clinics")),
    )
    op.create_table(
        "doctors",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("full_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="unknown", nullable=False),
        sa.Column("specialization_ids", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("experience_years", sa.Integer(), server_default="0", nullable=False),
        sa.Column("languages", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("accepts_home_visits", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("home_visit_radius_km", sa.Integer(), server_default="0", nullable=False),
        sa.Column("home_lat", sa.Float(), nullable=True),
        sa.Column("home_lng", sa.Float(), nullable=True),
        sa.Column("photo_file_id", sa.UUID(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("reviews_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "profile_at", sa.DateTime(timezone=True), server_default=sa.text("'1970-01-01T00:00:00Z'"), nullable=False
        ),
        sa.Column(
            "status_at", sa.DateTime(timezone=True), server_default=sa.text("'1970-01-01T00:00:00Z'"), nullable=False
        ),
        sa.Column(
            "rating_at", sa.DateTime(timezone=True), server_default=sa.text("'1970-01-01T00:00:00Z'"), nullable=False
        ),
        sa.Column(
            "availability_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("'1970-01-01T00:00:00Z'"),
            nullable=False,
        ),
        sa.Column("doc_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_doctors")),
    )
    op.create_table(
        "reindex_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="all", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("requested_ip", sa.String(length=64), nullable=True),
        sa.Column("doctors_index", sa.String(length=128), server_default="", nullable=False),
        sa.Column("clinics_index", sa.String(length=128), server_default="", nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("doctors_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("clinics_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.String(length=2000), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reindex_runs")),
    )
    op.create_index(
        "uq_reindex_runs_active",
        "reindex_runs",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.create_table(
        "services",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=True),
        sa.Column("clinic_id", sa.UUID(), nullable=True),
        sa.Column("specialization_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("name_ru", sa.String(length=255), server_default="", nullable=False),
        sa.Column("place", sa.String(length=8), server_default="clinic", nullable=False),
        sa.Column("price_tiyin", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("'1970-01-01T00:00:00Z'"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_services")),
    )
    op.create_index(
        "ix_services_clinic", "services", ["clinic_id"], unique=False, postgresql_where=sa.text("clinic_id IS NOT NULL")
    )
    op.create_index(
        "ix_services_doctor", "services", ["doctor_id"], unique=False, postgresql_where=sa.text("doctor_id IS NOT NULL")
    )
    op.create_table(
        "specializations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("name_ru", sa.String(length=100), server_default="", nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_specializations")),
    )


def downgrade() -> None:
    op.drop_table("specializations")
    op.drop_index("ix_services_doctor", table_name="services", postgresql_where=sa.text("doctor_id IS NOT NULL"))
    op.drop_index("ix_services_clinic", table_name="services", postgresql_where=sa.text("clinic_id IS NOT NULL"))
    op.drop_table("services")
    op.drop_index(
        "uq_reindex_runs_active",
        table_name="reindex_runs",
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.drop_table("reindex_runs")
    op.drop_table("doctors")
    op.drop_table("clinics")
    op.drop_index("ix_affiliations_doctor", table_name="affiliations")
    op.drop_index("ix_affiliations_clinic", table_name="affiliations")
    op.drop_table("affiliations")

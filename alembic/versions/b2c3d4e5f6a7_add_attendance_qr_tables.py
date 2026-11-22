"""add attendance_sessions and attendance_records tables for QR attendance module

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-11-22 19:35:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "attendance_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jadwal_id", sa.Integer(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("pertemuan_ke", sa.Integer(), server_default="1", nullable=False),
        sa.Column("active_jti", sa.String(length=64), nullable=False),
        sa.Column("session_started_at", sa.DateTime(), nullable=False),
        sa.Column("qr_generated_at", sa.DateTime(), nullable=False),
        sa.Column("qr_expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "closed",
                name="attendance_session_status",
                native_enum=False,
            ),
            server_default="open",
            nullable=False,
        ),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column(
            "radius_meters", sa.Float(), server_default="100.0", nullable=False
        ),
        sa.Column(
            "require_location", sa.Boolean(), server_default="0", nullable=False
        ),
        sa.Column(
            "created_by",
            sa.String(length=100),
            server_default="dosen",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "pertemuan_ke >= 1", name="ck_attendance_session_pertemuan_pos"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'closed')", name="ck_attendance_session_status"
        ),
        sa.CheckConstraint(
            "radius_meters > 0", name="ck_attendance_session_radius_pos"
        ),
        sa.ForeignKeyConstraint(["jadwal_id"], ["jadwal.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_jti"),
        sa.UniqueConstraint(
            "jadwal_id", "session_date", name="uq_attendance_session_jadwal_date"
        ),
    )
    op.create_index(
        op.f("ix_attendance_sessions_jadwal_id"),
        "attendance_sessions",
        ["jadwal_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_attendance_sessions_session_date"),
        "attendance_sessions",
        ["session_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_attendance_sessions_status"),
        "attendance_sessions",
        ["status"],
        unique=False,
    )

    op.create_table(
        "attendance_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("jadwal_id", sa.Integer(), nullable=False),
        sa.Column("nim", sa.String(length=30), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "hadir",
                "terlambat",
                "alpha",
                "izin",
                name="attendance_status",
                native_enum=False,
            ),
            server_default="hadir",
            nullable=False,
        ),
        sa.Column(
            "scanned_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("token_jti", sa.String(length=64), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("distance_meters", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('hadir', 'terlambat', 'alpha', 'izin')",
            name="ck_attendance_record_status",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["attendance_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["jadwal_id"], ["jadwal.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["nim"], ["calon_mahasiswa.nim"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "nim", name="uq_attendance_record_session_nim"
        ),
    )
    op.create_index(
        op.f("ix_attendance_records_jadwal_id"),
        "attendance_records",
        ["jadwal_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_attendance_records_nim"),
        "attendance_records",
        ["nim"],
        unique=False,
    )
    op.create_index(
        op.f("ix_attendance_records_session_id"),
        "attendance_records",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_attendance_records_status"),
        "attendance_records",
        ["status"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_attendance_records_status"), table_name="attendance_records")
    op.drop_index(
        op.f("ix_attendance_records_session_id"), table_name="attendance_records"
    )
    op.drop_index(op.f("ix_attendance_records_nim"), table_name="attendance_records")
    op.drop_index(
        op.f("ix_attendance_records_jadwal_id"), table_name="attendance_records"
    )
    op.drop_table("attendance_records")

    op.drop_index(
        op.f("ix_attendance_sessions_status"), table_name="attendance_sessions"
    )
    op.drop_index(
        op.f("ix_attendance_sessions_session_date"), table_name="attendance_sessions"
    )
    op.drop_index(
        op.f("ix_attendance_sessions_jadwal_id"), table_name="attendance_sessions"
    )
    op.drop_table("attendance_sessions")

"""add outbox_events table and jadwal.sequence column

Revision ID: e8b4c9d1023a
Revises: d7a1e2f3045b
Create Date: 2026-11-20 17:20:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "e8b4c9d1023a"
down_revision = "d7a1e2f3045b"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("jadwal") as batch_op:
        batch_op.add_column(
            sa.Column(
                "sequence",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "sent",
                "failed",
                name="outbox_status",
                native_enum=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')",
            name="ck_outbox_events_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_outbox_events_attempts_nonneg",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(
        op.f("ix_outbox_events_status"),
        "outbox_events",
        ["status"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_outbox_events_status"), table_name="outbox_events")
    op.drop_table("outbox_events")

    with op.batch_alter_table("jadwal") as batch_op:
        batch_op.drop_column("sequence")

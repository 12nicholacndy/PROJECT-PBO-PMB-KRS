"""add payment_method, billing, and payment tables for SPP finance module

Revision ID: a1b2c3d4e5f6
Revises: f9c5d2e1847b
Create Date: 2026-11-22 18:56:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "f9c5d2e1847b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_method",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "billing",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nim", sa.String(length=30), nullable=False),
        sa.Column("semester", sa.String(length=5), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("late_fee", sa.Integer(), server_default="0", nullable=False),
        sa.Column("paid_amount", sa.Integer(), server_default="0", nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "unpaid",
                "partial",
                "paid",
                "overdue",
                name="billing_status",
                native_enum=False,
            ),
            server_default="unpaid",
            nullable=False,
        ),
        sa.Column("last_reminded_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
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
        sa.CheckConstraint("length(semester) = 5", name="ck_billing_semester_5"),
        sa.CheckConstraint("amount > 0", name="ck_billing_amount_positif"),
        sa.CheckConstraint("late_fee >= 0", name="ck_billing_late_fee_nonneg"),
        sa.CheckConstraint("paid_amount >= 0", name="ck_billing_paid_amount_nonneg"),
        sa.CheckConstraint(
            "status IN ('unpaid', 'partial', 'paid', 'overdue')",
            name="ck_billing_status",
        ),
        sa.ForeignKeyConstraint(["nim"], ["calon_mahasiswa.nim"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nim", "semester", name="uq_billing_nim_semester"),
    )
    op.create_index(op.f("ix_billing_due_date"), "billing", ["due_date"], unique=False)
    op.create_index(op.f("ix_billing_nim"), "billing", ["nim"], unique=False)
    op.create_index(op.f("ix_billing_semester"), "billing", ["semester"], unique=False)
    op.create_index(op.f("ix_billing_status"), "billing", ["status"], unique=False)

    op.create_table(
        "payment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("billing_id", sa.Integer(), nullable=False),
        sa.Column("payment_method_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "success",
                "failed",
                name="payment_status",
                native_enum=False,
            ),
            server_default="success",
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            server_default="manual",
            nullable=False,
        ),
        sa.Column(
            "paid_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "receipt_sent",
            sa.Boolean(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("receipt_email", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_payment_amount_positif"),
        sa.CheckConstraint(
            "status IN ('pending', 'success', 'failed')",
            name="ck_payment_status",
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'webhook')",
            name="ck_payment_source",
        ),
        sa.ForeignKeyConstraint(["billing_id"], ["billing.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_method_id"], ["payment_method.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_id"),
    )
    op.create_index(
        op.f("ix_payment_billing_id"), "payment", ["billing_id"], unique=False
    )
    op.create_index(
        op.f("ix_payment_payment_method_id"),
        "payment",
        ["payment_method_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_payment_payment_method_id"), table_name="payment")
    op.drop_index(op.f("ix_payment_billing_id"), table_name="payment")
    op.drop_table("payment")

    op.drop_index(op.f("ix_billing_status"), table_name="billing")
    op.drop_index(op.f("ix_billing_semester"), table_name="billing")
    op.drop_index(op.f("ix_billing_nim"), table_name="billing")
    op.drop_index(op.f("ix_billing_due_date"), table_name="billing")
    op.drop_table("billing")

    op.drop_table("payment_method")

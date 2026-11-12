"""Create three PMB tables

Revision ID: 0001_create_pmb
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_create_pmb"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "program_studi",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kode", sa.String(length=3), nullable=False),
        sa.Column("nama", sa.String(length=100), nullable=False),
        sa.Column("fakultas", sa.String(length=100), nullable=False),
        sa.UniqueConstraint("kode", name="uq_program_studi_kode"),
        sa.CheckConstraint("length(kode) = 3", name="ck_program_studi_kode_3"),
    )

    op.create_table(
        "jalur_masuk",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nama", sa.String(length=30), nullable=False),
        sa.UniqueConstraint("nama", name="uq_jalur_masuk_nama"),
    )

    op.create_table(
        "calon_mahasiswa",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nama_lengkap", sa.String(length=150), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("no_hp", sa.String(length=15), nullable=False),
        sa.Column("tanggal_lahir", sa.Date(), nullable=False),
        sa.Column("alamat", sa.String(length=255), nullable=False),
        sa.Column("program_studi_id", sa.Integer(), nullable=False),
        sa.Column("jalur_masuk_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "approved", "rejected",
                name="status_pmb",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("nim", sa.String(length=30), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["program_studi_id"], ["program_studi.id"],
            name="fk_calon_mahasiswa_program_studi",
        ),
        sa.ForeignKeyConstraint(
            ["jalur_masuk_id"], ["jalur_masuk.id"],
            name="fk_calon_mahasiswa_jalur_masuk",
        ),
        sa.UniqueConstraint("email", name="uq_calon_mahasiswa_email"),
        sa.UniqueConstraint("nim", name="uq_calon_mahasiswa_nim"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_calon_mahasiswa_status",
        ),
    )
    op.create_index(
        "ix_calon_mahasiswa_email",
        "calon_mahasiswa",
        ["email"],
        unique=False,
    )


def downgrade():
    # Hapus tabel anak terlebih dahulu karena bergantung pada dua tabel referensi.
    op.drop_index("ix_calon_mahasiswa_email", table_name="calon_mahasiswa")
    op.drop_table("calon_mahasiswa")
    op.drop_table("jalur_masuk")
    op.drop_table("program_studi")

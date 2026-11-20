"""add scheduling tables (ruangan, dosen, kelas, jadwal) and krs_detail.kelas_id

Revision ID: d7a1e2f3045b
Revises: c6d2d3fb9b6d
Create Date: 2026-11-20 16:25:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "d7a1e2f3045b"
down_revision = "c6d2d3fb9b6d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ruangan",
        sa.Column("kode", sa.String(length=20), nullable=False),
        sa.Column("nama", sa.String(length=100), nullable=True),
        sa.Column("kapasitas", sa.Integer(), nullable=False),
        sa.CheckConstraint("kapasitas > 0", name="ck_ruangan_kapasitas_positif"),
        sa.PrimaryKeyConstraint("kode"),
    )

    op.create_table(
        "dosen",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nidn", sa.String(length=20), nullable=False),
        sa.Column("nama", sa.String(length=150), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nidn"),
    )

    op.create_table(
        "kelas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kode_mk", sa.String(length=20), nullable=False),
        sa.Column("nama_kelas", sa.String(length=10), nullable=False),
        sa.Column("kuota", sa.Integer(), nullable=False),
        sa.Column("semester", sa.String(length=5), nullable=False),
        sa.CheckConstraint("kuota > 0", name="ck_kelas_kuota_positif"),
        sa.CheckConstraint("length(semester) = 5", name="ck_kelas_semester_5"),
        sa.ForeignKeyConstraint(["kode_mk"], ["matakuliah.kode_mk"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "kode_mk", "nama_kelas", "semester", name="uq_kelas_mk_nama_semester"
        ),
    )

    op.create_table(
        "jadwal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kelas_id", sa.Integer(), nullable=False),
        sa.Column("ruangan_kode", sa.String(length=20), nullable=False),
        sa.Column("dosen_id", sa.Integer(), nullable=False),
        sa.Column("hari", sa.Integer(), nullable=False),
        sa.Column("jam_mulai", sa.Time(), nullable=False),
        sa.Column("jam_selesai", sa.Time(), nullable=False),
        sa.CheckConstraint("jam_mulai < jam_selesai", name="ck_jadwal_jam_valid"),
        sa.CheckConstraint("hari BETWEEN 1 AND 6", name="ck_jadwal_hari_1_6"),
        sa.ForeignKeyConstraint(["dosen_id"], ["dosen.id"]),
        sa.ForeignKeyConstraint(["kelas_id"], ["kelas.id"]),
        sa.ForeignKeyConstraint(["ruangan_kode"], ["ruangan.kode"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_jadwal_hari_ruangan", "jadwal", ["hari", "ruangan_kode"], unique=False
    )
    op.create_index(
        "ix_jadwal_hari_dosen", "jadwal", ["hari", "dosen_id"], unique=False
    )

    with op.batch_alter_table("krs_detail") as batch_op:
        batch_op.add_column(sa.Column("kelas_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_krs_detail_kelas_id"), ["kelas_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_krs_detail_kelas_id", "kelas", ["kelas_id"], ["id"]
        )


def downgrade():
    with op.batch_alter_table("krs_detail") as batch_op:
        batch_op.drop_constraint("fk_krs_detail_kelas_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_krs_detail_kelas_id"))
        batch_op.drop_column("kelas_id")

    op.drop_index("ix_jadwal_hari_dosen", table_name="jadwal")
    op.drop_index("ix_jadwal_hari_ruangan", table_name="jadwal")
    op.drop_table("jadwal")
    op.drop_table("kelas")
    op.drop_table("dosen")
    op.drop_table("ruangan")

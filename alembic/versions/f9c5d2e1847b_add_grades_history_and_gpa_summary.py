"""add grades, grade_history, student_academic_summary tables and audit trigger

Revision ID: f9c5d2e1847b
Revises: e8b4c9d1023a
Create Date: 2026-11-20 22:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "f9c5d2e1847b"
down_revision = "e8b4c9d1023a"
branch_labels = None
depends_on = None


TRIGGER_UP_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_grades_after_update
AFTER UPDATE OF nilai_huruf ON grades
WHEN OLD.nilai_huruf != NEW.nilai_huruf
BEGIN
    INSERT INTO grade_history (grade_id, old_value, new_value, changed_by, changed_at, reason)
    VALUES (
        NEW.id,
        OLD.nilai_huruf,
        NEW.nilai_huruf,
        NEW.updated_by,
        CURRENT_TIMESTAMP,
        COALESCE(NULLIF(TRIM(NEW.change_reason), ''), 'Perubahan nilai')
    );
END;
"""


def upgrade():
    op.create_table(
        "grades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nim", sa.String(length=30), nullable=False),
        sa.Column("kode_mk", sa.String(length=20), nullable=False),
        sa.Column("semester", sa.String(length=5), nullable=False),
        sa.Column("nilai_huruf", sa.String(length=2), nullable=False),
        sa.Column("nilai_angka", sa.Float(), nullable=False),
        sa.Column(
            "presensi_persen",
            sa.Float(),
            server_default="100.0",
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("updated_by", sa.String(length=100), nullable=False),
        sa.Column("change_reason", sa.String(length=500), nullable=True),
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
        sa.CheckConstraint("length(semester) = 5", name="ck_grades_semester_5"),
        sa.CheckConstraint(
            "nilai_huruf IN ('A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'D', 'E')",
            name="ck_grades_nilai_huruf",
        ),
        sa.CheckConstraint(
            "nilai_angka >= 0.0 AND nilai_angka <= 4.0",
            name="ck_grades_nilai_angka_range",
        ),
        sa.CheckConstraint(
            "presensi_persen >= 0.0 AND presensi_persen <= 100.0",
            name="ck_grades_presensi_range",
        ),
        sa.ForeignKeyConstraint(["kode_mk"], ["matakuliah.kode_mk"]),
        sa.ForeignKeyConstraint(["nim"], ["calon_mahasiswa.nim"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "nim", "kode_mk", "semester", name="uq_grades_nim_mk_semester"
        ),
    )
    op.create_index(op.f("ix_grades_kode_mk"), "grades", ["kode_mk"], unique=False)
    op.create_index(op.f("ix_grades_nim"), "grades", ["nim"], unique=False)

    op.create_table(
        "grade_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("grade_id", sa.Integer(), nullable=False),
        sa.Column("old_value", sa.String(length=10), nullable=False),
        sa.Column("new_value", sa.String(length=10), nullable=False),
        sa.Column("changed_by", sa.String(length=100), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(["grade_id"], ["grades.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_grade_history_grade_id"),
        "grade_history",
        ["grade_id"],
        unique=False,
    )

    op.create_table(
        "student_academic_summary",
        sa.Column("nim", sa.String(length=30), nullable=False),
        sa.Column(
            "total_sks_lulus",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "ipk",
            sa.Float(),
            server_default="0.0",
            nullable=False,
        ),
        sa.Column(
            "predikat",
            sa.String(length=50),
            server_default="Belum Ada Nilai",
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint("total_sks_lulus >= 0", name="ck_summary_sks_nonneg"),
        sa.CheckConstraint(
            "ipk >= 0.0 AND ipk <= 4.0", name="ck_summary_ipk_range"
        ),
        sa.ForeignKeyConstraint(["nim"], ["calon_mahasiswa.nim"]),
        sa.PrimaryKeyConstraint("nim"),
    )

    op.execute(TRIGGER_UP_SQL)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_grades_after_update")
    op.drop_table("student_academic_summary")
    op.drop_index(op.f("ix_grade_history_grade_id"), table_name="grade_history")
    op.drop_table("grade_history")
    op.drop_index(op.f("ix_grades_nim"), table_name="grades")
    op.drop_index(op.f("ix_grades_kode_mk"), table_name="grades")
    op.drop_table("grades")

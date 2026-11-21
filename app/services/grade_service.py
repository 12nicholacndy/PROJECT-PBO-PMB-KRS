from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CalonMahasiswa,
    Grade,
    GradeHistory,
    KRS,
    KRSDetail,
    MataKuliah,
    ProgramStudi,
    StatusPMB,
    StudentAcademicSummary,
)
from app.services.gpa_calculator import (
    MIN_ATTENDANCE_PERCENT,
    _ensure_student,
    _load_grade_records,
    compute_ipk_stats,
    convert_grade_to_point,
    is_passing_grade,
    normalize_grade_letter,
)
from app.services.grade_errors import (
    AttendanceRequirementError,
    GradeConflictError,
    GradeNotFoundError,
    GradeValidationError,
)


@dataclass(frozen=True)
class GradeView:
    id: int
    nim: str
    kode_mk: str
    nama_mk: str
    sks: int
    semester: str
    nilai_huruf: str
    nilai_angka: float
    mutu: float
    lulus: bool
    presensi_persen: float
    created_by: str
    updated_by: str
    change_reason: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class GradeHistoryView:
    id: int
    grade_id: int
    nim: str
    kode_mk: str
    nama_mk: str
    semester: str
    old_value: str
    new_value: str
    changed_by: str
    changed_at: datetime
    reason: str


@dataclass(frozen=True)
class AcademicSummaryView:
    nim: str
    total_sks_lulus: int
    ipk: float
    predikat: str
    updated_at: datetime | None


@dataclass(frozen=True)
class StudentOptionView:
    nim: str
    nama: str
    program_studi: str
    angkatan: str
    ipk: float
    total_sks_lulus: int
    predikat: str


class GradeService:
    """Service pengelolaan nilai, aturan bisnis presensi, sinkronisasi KRS, dan auto-IPK."""

    def _validate_attendance(self, presensi_persen: float) -> float:
        if presensi_persen < 0.0 or presensi_persen > 100.0:
            raise GradeValidationError(
                "Persentase presensi harus berada di rentang 0 sampai 100"
            )
        if presensi_persen < MIN_ATTENDANCE_PERCENT:
            raise AttendanceRequirementError(
                presensi_persen=presensi_persen,
                minimum_persen=MIN_ATTENDANCE_PERCENT,
            )
        return round(float(presensi_persen), 2)

    def _validate_semester(self, semester: str) -> str:
        sem = semester.strip()
        if len(sem) != 5 or not sem.isdigit():
            raise GradeValidationError(
                "Semester harus terdiri dari 5 digit angka (contoh: 20251)"
            )
        return sem

    def _sync_krs_detail_grade(
        self, db: Session, nim: str, semester: str, kode_mk: str, nilai_huruf: str
    ) -> None:
        """Menjaga konsistensi kolom krs_detail.nilai agar validator prasyarat KRS tetap sinkron."""
        detail = db.scalar(
            select(KRSDetail)
            .join(KRS, KRS.id == KRSDetail.krs_id)
            .where(
                KRS.nim == nim,
                KRS.semester == semester,
                KRSDetail.kode_mk == kode_mk,
            )
        )
        if detail is not None:
            detail.nilai = nilai_huruf

    def _refresh_academic_summary(
        self, db: Session, nim: str
    ) -> StudentAcademicSummary:
        """Menghitung ulang dan menyimpan IPK, Total SKS Lulus, & Predikat dalam transaksi aktif."""
        records = _load_grade_records(db, nim)
        stats = compute_ipk_stats(records)
        now = datetime.now(timezone.utc)

        summary = db.get(StudentAcademicSummary, nim)
        if summary is None:
            summary = StudentAcademicSummary(
                nim=nim,
                total_sks_lulus=stats.total_sks_lulus,
                ipk=stats.ipk,
                predikat=stats.predikat,
                updated_at=now,
            )
            db.add(summary)
        else:
            summary.total_sks_lulus = stats.total_sks_lulus
            summary.ipk = stats.ipk
            summary.predikat = stats.predikat
            summary.updated_at = now
        db.flush()
        return summary

    def _to_grade_view(self, grade: Grade, mk: MataKuliah) -> GradeView:
        return GradeView(
            id=grade.id,
            nim=grade.nim,
            kode_mk=grade.kode_mk,
            nama_mk=mk.nama,
            sks=mk.sks,
            semester=grade.semester,
            nilai_huruf=grade.nilai_huruf,
            nilai_angka=grade.nilai_angka,
            mutu=round(mk.sks * grade.nilai_angka, 2),
            lulus=is_passing_grade(grade.nilai_huruf),
            presensi_persen=grade.presensi_persen,
            created_by=grade.created_by,
            updated_by=grade.updated_by,
            change_reason=grade.change_reason,
            created_at=grade.created_at,
            updated_at=grade.updated_at,
        )

    def input_grade(
        self,
        db: Session,
        *,
        nim: str,
        kode_mk: str,
        semester: str,
        nilai_huruf: str,
        presensi_persen: float,
        created_by: str,
    ) -> tuple[GradeView, AcademicSummaryView]:
        mahasiswa, _ = _ensure_student(db, nim.strip())
        if mahasiswa.status != StatusPMB.APPROVED or not mahasiswa.nim:
            raise GradeValidationError(
                f"Mahasiswa '{nim}' belum berstatus approved"
            )

        mk = db.get(MataKuliah, kode_mk.strip())
        if mk is None:
            raise GradeNotFoundError(
                f"Mata kuliah '{kode_mk}' tidak ditemukan"
            )

        sem = self._validate_semester(semester)
        attendance = self._validate_attendance(presensi_persen)
        huruf = normalize_grade_letter(nilai_huruf)
        angka = convert_grade_to_point(huruf)
        aktor = created_by.strip()
        if not aktor:
            raise GradeValidationError("Identitas penginput nilai (created_by) wajib diisi")

        existing = db.scalar(
            select(Grade).where(
                Grade.nim == mahasiswa.nim,
                Grade.kode_mk == mk.kode_mk,
                Grade.semester == sem,
            )
        )
        if existing is not None:
            raise GradeConflictError(
                f"Nilai untuk NIM {mahasiswa.nim} pada MK {mk.kode_mk} semester {sem} "
                f"sudah ada (id={existing.id}). Gunakan endpoint edit nilai (PUT /api/grades/{existing.id})."
            )

        now = datetime.now(timezone.utc)
        grade = Grade(
            nim=mahasiswa.nim,
            kode_mk=mk.kode_mk,
            semester=sem,
            nilai_huruf=huruf,
            nilai_angka=angka,
            presensi_persen=attendance,
            created_by=aktor,
            updated_by=aktor,
            change_reason=None,
            created_at=now,
            updated_at=now,
        )
        db.add(grade)
        db.flush()

        self._sync_krs_detail_grade(db, mahasiswa.nim, sem, mk.kode_mk, huruf)
        summary = self._refresh_academic_summary(db, mahasiswa.nim)
        db.commit()
        db.refresh(grade)
        db.refresh(summary)

        return (
            self._to_grade_view(grade, mk),
            AcademicSummaryView(
                nim=summary.nim,
                total_sks_lulus=summary.total_sks_lulus,
                ipk=summary.ipk,
                predikat=summary.predikat,
                updated_at=summary.updated_at,
            ),
        )

    def update_grade(
        self,
        db: Session,
        *,
        grade_id: int,
        new_nilai_huruf: str,
        changed_by: str,
        reason: str,
        presensi_persen: float | None = None,
    ) -> tuple[GradeView, AcademicSummaryView, GradeHistoryView]:
        grade = db.get(Grade, grade_id)
        if grade is None:
            raise GradeNotFoundError(f"Data nilai dengan ID {grade_id} tidak ditemukan")

        mk = db.get(MataKuliah, grade.kode_mk)
        if mk is None:
            raise GradeNotFoundError(f"Mata kuliah '{grade.kode_mk}' tidak ditemukan")

        aktor = changed_by.strip()
        alasan = reason.strip()
        if not aktor:
            raise GradeValidationError("Identitas pengubah nilai (changed_by) wajib diisi")
        if not alasan:
            raise GradeValidationError("Alasan perubahan nilai (reason) wajib diisi")

        if presensi_persen is not None:
            grade.presensi_persen = self._validate_attendance(presensi_persen)

        huruf_baru = normalize_grade_letter(new_nilai_huruf)
        if huruf_baru == grade.nilai_huruf:
            raise GradeValidationError(
                f"Nilai baru '{huruf_baru}' sama dengan nilai saat ini"
            )

        grade.nilai_huruf = huruf_baru
        grade.nilai_angka = convert_grade_to_point(huruf_baru)
        grade.updated_by = aktor
        grade.change_reason = alasan
        grade.updated_at = datetime.now(timezone.utc)

        # Flush memicu database trigger `trg_grades_after_update` untuk insert ke `grade_history`.
        db.flush()

        self._sync_krs_detail_grade(
            db, grade.nim, grade.semester, grade.kode_mk, huruf_baru
        )
        summary = self._refresh_academic_summary(db, grade.nim)
        db.commit()
        db.refresh(grade)
        db.refresh(summary)

        latest_history = db.scalar(
            select(GradeHistory)
            .where(GradeHistory.grade_id == grade.id)
            .order_by(GradeHistory.id.desc())
            .limit(1)
        )
        if latest_history is None:  # pragma: no cover - jaring pengaman non-SQLite
            raise GradeValidationError(
                "Audit trail gagal tercatat untuk perubahan nilai"
            )

        return (
            self._to_grade_view(grade, mk),
            AcademicSummaryView(
                nim=summary.nim,
                total_sks_lulus=summary.total_sks_lulus,
                ipk=summary.ipk,
                predikat=summary.predikat,
                updated_at=summary.updated_at,
            ),
            GradeHistoryView(
                id=latest_history.id,
                grade_id=latest_history.grade_id,
                nim=grade.nim,
                kode_mk=grade.kode_mk,
                nama_mk=mk.nama,
                semester=grade.semester,
                old_value=latest_history.old_value,
                new_value=latest_history.new_value,
                changed_by=latest_history.changed_by,
                changed_at=latest_history.changed_at,
                reason=latest_history.reason,
            ),
        )

    def list_grades(
        self,
        db: Session,
        *,
        nim: str | None = None,
        semester: str | None = None,
    ) -> list[GradeView]:
        stmt = (
            select(Grade, MataKuliah)
            .join(MataKuliah, Grade.kode_mk == MataKuliah.kode_mk)
            .order_by(Grade.semester.asc(), Grade.kode_mk.asc(), Grade.id.asc())
        )
        if nim is not None:
            stmt = stmt.where(Grade.nim == nim.strip())
        if semester is not None:
            stmt = stmt.where(Grade.semester == semester.strip())

        rows = db.execute(stmt).all()
        return [self._to_grade_view(grade, mk) for grade, mk in rows]

    def get_grade_history(self, db: Session, nim: str) -> list[GradeHistoryView]:
        _ensure_student(db, nim.strip())
        rows = db.execute(
            select(GradeHistory, Grade, MataKuliah)
            .join(Grade, GradeHistory.grade_id == Grade.id)
            .join(MataKuliah, Grade.kode_mk == MataKuliah.kode_mk)
            .where(Grade.nim == nim.strip())
            .order_by(GradeHistory.id.desc())
        ).all()
        return [
            GradeHistoryView(
                id=hist.id,
                grade_id=hist.grade_id,
                nim=grade.nim,
                kode_mk=grade.kode_mk,
                nama_mk=mk.nama,
                semester=grade.semester,
                old_value=hist.old_value,
                new_value=hist.new_value,
                changed_by=hist.changed_by,
                changed_at=hist.changed_at,
                reason=hist.reason,
            )
            for hist, grade, mk in rows
        ]

    def get_academic_summary(self, db: Session, nim: str) -> AcademicSummaryView:
        _ensure_student(db, nim.strip())
        summary = db.get(StudentAcademicSummary, nim.strip())
        if summary is not None:
            return AcademicSummaryView(
                nim=summary.nim,
                total_sks_lulus=summary.total_sks_lulus,
                ipk=summary.ipk,
                predikat=summary.predikat,
                updated_at=summary.updated_at,
            )
        stats = compute_ipk_stats(_load_grade_records(db, nim.strip()))
        return AcademicSummaryView(
            nim=nim.strip(),
            total_sks_lulus=stats.total_sks_lulus,
            ipk=stats.ipk,
            predikat=stats.predikat,
            updated_at=None,
        )

    def list_approved_students(self, db: Session) -> list[StudentOptionView]:
        rows = db.execute(
            select(CalonMahasiswa, ProgramStudi, StudentAcademicSummary)
            .join(ProgramStudi, CalonMahasiswa.program_studi_id == ProgramStudi.id)
            .outerjoin(
                StudentAcademicSummary,
                StudentAcademicSummary.nim == CalonMahasiswa.nim,
            )
            .where(
                CalonMahasiswa.status == StatusPMB.APPROVED,
                CalonMahasiswa.nim.is_not(None),
            )
            .order_by(CalonMahasiswa.nim.asc())
        ).all()

        results: list[StudentOptionView] = []
        for mhs, prodi, summary in rows:
            nim = mhs.nim or ""
            angkatan = nim[:4] if len(nim) >= 4 and nim[:4].isdigit() else "-"
            results.append(
                StudentOptionView(
                    nim=nim,
                    nama=mhs.nama_lengkap,
                    program_studi=prodi.nama,
                    angkatan=angkatan,
                    ipk=summary.ipk if summary is not None else 0.0,
                    total_sks_lulus=(
                        summary.total_sks_lulus if summary is not None else 0
                    ),
                    predikat=(
                        summary.predikat if summary is not None else "Belum Ada Nilai"
                    ),
                )
            )
        return results

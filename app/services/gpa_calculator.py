from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CalonMahasiswa, Grade, MataKuliah, ProgramStudi
from app.services.grade_errors import GradeNotFoundError, GradeValidationError

# Skala konversi huruf -> angka (mendukung A/B/C/D/E serta plus/minus konsisten dengan KRS).
GRADE_SCALE: dict[str, float] = {
    "A": 4.0,
    "A-": 3.7,
    "B+": 3.3,
    "B": 3.0,
    "B-": 2.7,
    "C+": 2.3,
    "C": 2.0,
    "D": 1.0,
    "E": 0.0,
}

# Batas minimal lulus mata kuliah untuk transkrip & perhitungan IPK: nilai >= D (1.0).
# Catatan: prasyarat KRS (Minggu 5) tetap memakai >= C (2.0) di krs_validators.py.
MIN_PASSING_GRADE_POINT: float = 1.0
MIN_ATTENDANCE_PERCENT: float = 75.0


@dataclass(frozen=True)
class GradeRecord:
    kode_mk: str
    nama_mk: str
    sks: int
    semester: str
    nilai_huruf: str
    nilai_angka: float


@dataclass(frozen=True)
class IPKStats:
    ipk: float
    total_sks_lulus: int
    total_mutu_lulus: float
    predikat: str
    best_by_course: dict[str, GradeRecord]


@dataclass(frozen=True)
class TranscriptCourseRow:
    kode_mk: str
    nama_mk: str
    sks: int
    nilai_huruf: str
    nilai_angka: float
    mutu: float
    lulus: bool
    is_retake: bool
    counted_in_ipk: bool


@dataclass(frozen=True)
class TranscriptSemesterBlock:
    semester: str
    semester_label: str
    courses: list[TranscriptCourseRow]
    total_sks: int
    total_mutu: float
    ips: float


@dataclass(frozen=True)
class TranscriptBiodata:
    nim: str
    nama: str
    program_studi: str
    fakultas: str
    angkatan: str


@dataclass(frozen=True)
class TranscriptData:
    biodata: TranscriptBiodata
    semesters: list[TranscriptSemesterBlock]
    total_sks_tempuh: int
    total_sks_lulus: int
    total_mutu_lulus: float
    ipk: float
    predikat: str
    generated_at: datetime


def normalize_grade_letter(nilai_huruf: str) -> str:
    normalized = nilai_huruf.strip().upper()
    if normalized not in GRADE_SCALE:
        valid = ", ".join(GRADE_SCALE.keys())
        raise GradeValidationError(
            f"Nilai huruf '{nilai_huruf}' tidak valid. Pilihan yang sah: {valid}"
        )
    return normalized


def convert_grade_to_point(nilai_huruf: str) -> float:
    return GRADE_SCALE[normalize_grade_letter(nilai_huruf)]


def is_passing_grade(nilai_huruf: str) -> bool:
    return convert_grade_to_point(nilai_huruf) >= MIN_PASSING_GRADE_POINT


def determine_predikat(ipk: float, total_sks_lulus: int) -> str:
    """Menentukan predikat kelulusan berdasarkan IPK kumulatif."""
    if total_sks_lulus <= 0:
        return "Belum Ada Nilai"
    if ipk >= 3.50:
        return "Cum Laude"
    if ipk >= 3.00:
        return "Sangat Memuaskan"
    if ipk >= 2.50:
        return "Memuaskan"
    if ipk >= 2.00:
        return "Cukup"
    return "Kurang"


def format_semester_label(semester: str) -> str:
    """Mengubah kode semester 5 digit (mis. '20251') menjadi label ramah cetak."""
    if len(semester) == 5 and semester.isdigit():
        tahun = int(semester[:4])
        kode = semester[4]
        if kode == "1":
            return f"Semester Ganjil {tahun}/{tahun + 1}"
        if kode == "2":
            return f"Semester Genap {tahun}/{tahun + 1}"
        if kode == "3":
            return f"Semester Pendek {tahun}/{tahun + 1}"
    return f"Semester {semester}"


def compute_ips_from_records(records: list[GradeRecord]) -> float:
    """Menghitung IPS = Σ(SKS × Nilai Angka) / Σ(SKS) untuk satu semester."""
    total_sks = sum(r.sks for r in records)
    if total_sks == 0:
        return 0.0
    total_mutu = sum(r.sks * r.nilai_angka for r in records)
    return round(total_mutu / total_sks, 2)


def compute_ipk_stats(records: list[GradeRecord]) -> IPKStats:
    """
    Menghitung IPK kumulatif sesuai aturan tugas:
    - IPK = Σ(SKS × Nilai Angka) / Σ(SKS)
    - Hanya menghitung MK yang sudah lulus (nilai >= D / bobot >= 1.0)
    - Jika MK diulang lintas semester, ambil nilai tertinggi
    - Edge case: mahasiswa semester awal tanpa nilai atau belum lulus MK -> 0.0
    """
    best_by_course: dict[str, GradeRecord] = {}
    for rec in records:
        if rec.nilai_angka < MIN_PASSING_GRADE_POINT:
            continue
        existing = best_by_course.get(rec.kode_mk)
        if existing is None or (
            rec.nilai_angka > existing.nilai_angka
            or (
                rec.nilai_angka == existing.nilai_angka
                and rec.semester > existing.semester
            )
        ):
            best_by_course[rec.kode_mk] = rec

    total_sks_lulus = sum(r.sks for r in best_by_course.values())
    if total_sks_lulus == 0:
        return IPKStats(
            ipk=0.0,
            total_sks_lulus=0,
            total_mutu_lulus=0.0,
            predikat="Belum Ada Nilai",
            best_by_course={},
        )

    total_mutu_lulus = round(
        sum(r.sks * r.nilai_angka for r in best_by_course.values()), 2
    )
    ipk = round(total_mutu_lulus / total_sks_lulus, 2)
    return IPKStats(
        ipk=ipk,
        total_sks_lulus=total_sks_lulus,
        total_mutu_lulus=total_mutu_lulus,
        predikat=determine_predikat(ipk, total_sks_lulus),
        best_by_course=best_by_course,
    )


def _ensure_student(db: Session, nim: str) -> tuple[CalonMahasiswa, ProgramStudi]:
    row = db.execute(
        select(CalonMahasiswa, ProgramStudi)
        .join(ProgramStudi, CalonMahasiswa.program_studi_id == ProgramStudi.id)
        .where(CalonMahasiswa.nim == nim)
    ).first()
    if row is None:
        raise GradeNotFoundError(f"Mahasiswa dengan NIM '{nim}' tidak ditemukan")
    return row[0], row[1]


def _load_grade_records(
    db: Session, nim: str, semester: str | None = None
) -> list[GradeRecord]:
    stmt = (
        select(Grade, MataKuliah)
        .join(MataKuliah, Grade.kode_mk == MataKuliah.kode_mk)
        .where(Grade.nim == nim)
        .order_by(Grade.semester.asc(), Grade.kode_mk.asc())
    )
    if semester is not None:
        stmt = stmt.where(Grade.semester == semester)

    rows = db.execute(stmt).all()
    return [
        GradeRecord(
            kode_mk=grade.kode_mk,
            nama_mk=mk.nama,
            sks=mk.sks,
            semester=grade.semester,
            nilai_huruf=grade.nilai_huruf,
            nilai_angka=grade.nilai_angka,
        )
        for grade, mk in rows
    ]


def calculate_ips(db: Session, nim: str, semester: str) -> float:
    """Menghitung Indeks Prestasi Semester (IPS) untuk NIM dan semester tertentu."""
    _ensure_student(db, nim)
    records = _load_grade_records(db, nim, semester=semester)
    return compute_ips_from_records(records)


def calculate_ipk(db: Session, nim: str) -> float:
    """Menghitung Indeks Prestasi Kumulatif (IPK) sesuai aturan lulus & MK diulang."""
    _ensure_student(db, nim)
    records = _load_grade_records(db, nim)
    return compute_ipk_stats(records).ipk


def get_transcript(db: Session, nim: str) -> TranscriptData:
    """Menyusun catatan akademik lengkap (transkrip) untuk mahasiswa."""
    mahasiswa, prodi = _ensure_student(db, nim)
    records = _load_grade_records(db, nim)
    stats = compute_ipk_stats(records)

    # Hitung berapa kali tiap MK diambil agar baris ulangan dapat ditandai di transkrip.
    course_counts: dict[str, int] = defaultdict(int)
    for rec in records:
        course_counts[rec.kode_mk] += 1

    grouped: dict[str, list[GradeRecord]] = defaultdict(list)
    for rec in records:
        grouped[rec.semester].append(rec)

    semesters: list[TranscriptSemesterBlock] = []
    total_sks_tempuh = 0

    for sem in sorted(grouped.keys()):
        sem_records = grouped[sem]
        rows: list[TranscriptCourseRow] = []
        sem_sks = 0
        sem_mutu = 0.0
        for rec in sem_records:
            mutu = round(rec.sks * rec.nilai_angka, 2)
            sem_sks += rec.sks
            sem_mutu += mutu
            best = stats.best_by_course.get(rec.kode_mk)
            counted = (
                best is not None
                and best.semester == rec.semester
                and best.nilai_angka == rec.nilai_angka
            )
            rows.append(
                TranscriptCourseRow(
                    kode_mk=rec.kode_mk,
                    nama_mk=rec.nama_mk,
                    sks=rec.sks,
                    nilai_huruf=rec.nilai_huruf,
                    nilai_angka=rec.nilai_angka,
                    mutu=mutu,
                    lulus=rec.nilai_angka >= MIN_PASSING_GRADE_POINT,
                    is_retake=course_counts[rec.kode_mk] > 1,
                    counted_in_ipk=counted,
                )
            )
        total_sks_tempuh += sem_sks
        semesters.append(
            TranscriptSemesterBlock(
                semester=sem,
                semester_label=format_semester_label(sem),
                courses=rows,
                total_sks=sem_sks,
                total_mutu=round(sem_mutu, 2),
                ips=compute_ips_from_records(sem_records),
            )
        )

    angkatan = nim[:4] if len(nim) >= 4 and nim[:4].isdigit() else str(
        (mahasiswa.approved_at or mahasiswa.created_at).year
    )
    biodata = TranscriptBiodata(
        nim=nim,
        nama=mahasiswa.nama_lengkap,
        program_studi=prodi.nama,
        fakultas=prodi.fakultas,
        angkatan=angkatan,
    )

    return TranscriptData(
        biodata=biodata,
        semesters=semesters,
        total_sks_tempuh=total_sks_tempuh,
        total_sks_lulus=stats.total_sks_lulus,
        total_mutu_lulus=stats.total_mutu_lulus,
        ipk=stats.ipk,
        predikat=stats.predikat,
        generated_at=datetime.now(timezone.utc),
    )

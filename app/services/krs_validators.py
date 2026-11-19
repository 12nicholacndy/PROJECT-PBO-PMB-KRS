from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.models import KRS, KRSDetail, MataKuliah, prerequisite_table

MAX_SKS = 24

# Pemetaan huruf nilai ke bobot angka agar tidak terjebak perbandingan string
# ('A' < 'B' < 'C' < 'D' secara alfabet, sehingga nilai >= 'C' justru terbalik).
SKALA_NILAI: dict[str, float] = {
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
MIN_PASSING_WEIGHT = 2.0
PASSING_GRADES: tuple[str, ...] = tuple(
    huruf for huruf, bobot in SKALA_NILAI.items() if bobot >= MIN_PASSING_WEIGHT
)

HARI_NAMA: dict[int, str] = {
    1: "Senin",
    2: "Selasa",
    3: "Rabu",
    4: "Kamis",
    5: "Jumat",
    6: "Sabtu",
}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str = ""


@dataclass
class KRSContext:
    nim: str
    semester: str
    existing_courses: list[MataKuliah]
    target_course: MataKuliah
    db: Session


def build_prasyarat_belum_lulus_query(
    nim: str, semester_aktif: str, kode_mk: str
) -> Select[tuple[MataKuliah]]:
    """Query NOT EXISTS untuk mengambil prasyarat MK yang belum lulus (>= C)
    oleh mahasiswa di semester sebelumnya.

    Memanfaatkan indeks otomatis dari:
    - PK gabungan prerequisite(kode_mk, kode_prasyarat)
    - UniqueConstraint krs(nim, semester)
    - UniqueConstraint krs_detail(krs_id, kode_mk)
    """
    sudah_lulus_subquery = (
        select(KRSDetail.id)
        .join(KRS, KRS.id == KRSDetail.krs_id)
        .where(
            KRS.nim == nim,
            KRS.semester < semester_aktif,
            KRSDetail.kode_mk == prerequisite_table.c.kode_prasyarat,
            KRSDetail.nilai.in_(PASSING_GRADES),
        )
    )

    return (
        select(MataKuliah)
        .join(
            prerequisite_table,
            MataKuliah.kode_mk == prerequisite_table.c.kode_prasyarat,
        )
        .where(
            prerequisite_table.c.kode_mk == kode_mk,
            ~exists(sudah_lulus_subquery),
        )
        .order_by(MataKuliah.kode_mk)
    )


def prasyarat_belum_lulus(
    db: Session, nim: str, semester_aktif: str, kode_mk: str
) -> list[MataKuliah]:
    stmt = build_prasyarat_belum_lulus_query(nim, semester_aktif, kode_mk)
    return list(db.scalars(stmt).all())


def jadwal_bentrok(a: MataKuliah, b: MataKuliah) -> bool:
    """Rumus irisan interval: bentrok jika hari sama dan interval jam beririsan.

    Jadwal bersambung (mis. selesai 11:30 dan mulai 11:30) tidak dianggap bentrok
    karena memakai operator `<`, bukan `<=`.
    """
    return (
        a.hari == b.hari
        and a.jam_mulai < b.jam_selesai
        and b.jam_mulai < a.jam_selesai
    )


class Validator(ABC):
    """Abstract base class untuk pola Chain of Responsibility + Template Method."""

    def __init__(self) -> None:
        self._next: Validator | None = None

    def set_next(self, next_validator: "Validator") -> "Validator":
        self._next = next_validator
        return next_validator

    def validate(self, ctx: KRSContext) -> ValidationResult:
        result = self.check(ctx)
        if not result.ok or self._next is None:
            return result
        return self._next.validate(ctx)

    @abstractmethod
    def check(self, ctx: KRSContext) -> ValidationResult:
        """Memeriksa satu aturan bisnis tertentu pada konteks KRS."""


class DuplicateValidator(Validator):
    def check(self, ctx: KRSContext) -> ValidationResult:
        for mk in ctx.existing_courses:
            if mk.kode_mk == ctx.target_course.kode_mk:
                return ValidationResult(
                    ok=False,
                    message=(
                        f"Mata kuliah {ctx.target_course.nama} "
                        f"({ctx.target_course.kode_mk}) sudah ada di KRS"
                    ),
                )
        return ValidationResult(ok=True)


class SKSValidator(Validator):
    def __init__(self, max_sks: int = MAX_SKS) -> None:
        super().__init__()
        self.max_sks = max_sks

    def check(self, ctx: KRSContext) -> ValidationResult:
        existing_sks = sum(
            mk.sks
            for mk in ctx.existing_courses
            if mk.kode_mk != ctx.target_course.kode_mk
        )
        total_sks = existing_sks + ctx.target_course.sks
        if total_sks > self.max_sks:
            return ValidationResult(
                ok=False,
                message=(
                    f"Total SKS {total_sks} melebihi batas maksimal {self.max_sks}"
                ),
            )
        return ValidationResult(ok=True)


class PrerequisiteValidator(Validator):
    def check(self, ctx: KRSContext) -> ValidationResult:
        belum = prasyarat_belum_lulus(
            ctx.db, ctx.nim, ctx.semester, ctx.target_course.kode_mk
        )
        if belum:
            nama_prasyarat = ", ".join(mk.nama for mk in belum)
            return ValidationResult(
                ok=False,
                message=f"Prasyarat tidak terpenuhi: {nama_prasyarat}",
            )
        return ValidationResult(ok=True)


class ConflictValidator(Validator):
    def check(self, ctx: KRSContext) -> ValidationResult:
        for mk in ctx.existing_courses:
            if mk.kode_mk == ctx.target_course.kode_mk:
                continue
            if jadwal_bentrok(mk, ctx.target_course):
                hari = HARI_NAMA.get(mk.hari, f"Hari {mk.hari}")
                jam_lama = (
                    f"{mk.jam_mulai.strftime('%H:%M')}-"
                    f"{mk.jam_selesai.strftime('%H:%M')}"
                )
                jam_baru = (
                    f"{ctx.target_course.jam_mulai.strftime('%H:%M')}-"
                    f"{ctx.target_course.jam_selesai.strftime('%H:%M')}"
                )
                return ValidationResult(
                    ok=False,
                    message=(
                        f"Jadwal bentrok antara {ctx.target_course.nama} "
                        f"({hari} {jam_baru}) dengan {mk.nama} ({hari} {jam_lama})"
                    ),
                )
        return ValidationResult(ok=True)


def build_default_validators() -> list[Validator]:
    """Urutan default: Duplicate paling depan karena paling murah & pesan paling jelas."""
    return [
        DuplicateValidator(),
        SKSValidator(),
        PrerequisiteValidator(),
        ConflictValidator(),
    ]


def chain_validators(validators: list[Validator]) -> Validator | None:
    if not validators:
        return None
    for idx in range(len(validators) - 1):
        validators[idx].set_next(validators[idx + 1])
    return validators[0]

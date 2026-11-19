from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import CalonMahasiswa, KRS, KRSDetail, KRSStatus, MataKuliah
from app.services.krs_validators import (
    ConflictValidator,
    KRSContext,
    MAX_SKS,
    PrerequisiteValidator,
    ValidationResult,
    Validator,
    build_default_validators,
    chain_validators,
    jadwal_bentrok,
    HARI_NAMA,
)

EDITABLE_KRS_STATUSES = (KRSStatus.DRAFT, KRSStatus.REVISION)


class KRSError(Exception):
    """Base domain error untuk modul KRS."""


class KRSNotFoundError(KRSError):
    pass


class KRSStatusError(KRSError):
    pass


class KRSValidationError(KRSError):
    pass


class KRSManager:
    """Service tunggal untuk mengelola operasi KRS tanpa bergantung pada HTTP."""

    def __init__(
        self,
        db: Session,
        validators: list[Validator] | None = None,
    ) -> None:
        self.db = db
        self._validators = (
            validators if validators is not None else build_default_validators()
        )
        self._chain = chain_validators(self._validators)

    def _get_or_create_locked_krs(self, nim: str, semester: str) -> KRS:
        """Mengambil baris KRS dengan with_for_update(), atau membuat draft baru."""
        mahasiswa = self.db.scalar(
            select(CalonMahasiswa.id).where(CalonMahasiswa.nim == nim)
        )
        if mahasiswa is None:
            raise KRSNotFoundError(
                f"Mahasiswa dengan NIM {nim} tidak ditemukan atau belum disetujui"
            )

        krs = self.db.scalars(
            select(KRS)
            .where(KRS.nim == nim, KRS.semester == semester)
            .with_for_update()
        ).one_or_none()

        if krs is None:
            krs = KRS(nim=nim, semester=semester, status=KRSStatus.DRAFT)
            self.db.add(krs)
            self.db.flush()

        return krs

    def _ensure_editable(self, krs: KRS) -> None:
        if krs.status not in EDITABLE_KRS_STATUSES:
            raise KRSStatusError(
                f"KRS dengan status '{krs.status.value}' tidak dapat diubah"
            )

    def _get_existing_courses(self, krs_id: int) -> list[MataKuliah]:
        """Ambil semua mata kuliah di KRS dalam satu query JOIN (hindari N+1)."""
        return list(
            self.db.scalars(
                select(MataKuliah)
                .join(KRSDetail, KRSDetail.kode_mk == MataKuliah.kode_mk)
                .where(KRSDetail.krs_id == krs_id)
                .order_by(MataKuliah.kode_mk)
            ).all()
        )

    def _load_krs_with_details(self, krs_id: int) -> KRS:
        krs = self.db.scalars(
            select(KRS)
            .options(selectinload(KRS.details).selectinload(KRSDetail.matakuliah))
            .where(KRS.id == krs_id)
        ).one()
        return krs

    def add_course(self, nim: str, kode_mk: str, semester: str) -> KRS:
        """Menambah satu mata kuliah ke KRS dalam satu transaksi terkunci.

        Menggunakan rantai validator fail-fast (berhenti pada kegagalan pertama).
        """
        krs = self._get_or_create_locked_krs(nim, semester)
        self._ensure_editable(krs)

        target_course = self.db.get(MataKuliah, kode_mk)
        if target_course is None:
            raise KRSNotFoundError(f"Mata kuliah {kode_mk} tidak ditemukan")

        existing_courses = self._get_existing_courses(krs.id)

        if self._chain is not None:
            ctx = KRSContext(
                nim=nim,
                semester=semester,
                existing_courses=existing_courses,
                target_course=target_course,
                db=self.db,
            )
            result = self._chain.validate(ctx)
            if not result.ok:
                raise KRSValidationError(result.message)

        detail = KRSDetail(krs_id=krs.id, kode_mk=kode_mk)
        self.db.add(detail)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise KRSValidationError(
                f"Mata kuliah {target_course.nama} ({target_course.kode_mk}) sudah ada di KRS"
            ) from exc

        return self._load_krs_with_details(krs.id)

    def remove_course(self, nim: str, kode_mk: str, semester: str) -> KRS:
        """Menghapus satu mata kuliah dari KRS berstatus draft atau revision."""
        krs = self.db.scalars(
            select(KRS)
            .where(KRS.nim == nim, KRS.semester == semester)
            .with_for_update()
        ).one_or_none()
        if krs is None:
            raise KRSNotFoundError(
                f"KRS untuk NIM {nim} pada semester {semester} tidak ditemukan"
            )

        self._ensure_editable(krs)

        detail = self.db.scalars(
            select(KRSDetail).where(
                KRSDetail.krs_id == krs.id,
                KRSDetail.kode_mk == kode_mk,
            )
        ).one_or_none()
        if detail is None:
            raise KRSNotFoundError(
                f"Mata kuliah {kode_mk} tidak ditemukan di dalam KRS"
            )

        self.db.delete(detail)
        self.db.commit()
        return self._load_krs_with_details(krs.id)

    def get_krs(self, nim: str, semester: str) -> KRS:
        krs = self.db.scalars(
            select(KRS)
            .options(selectinload(KRS.details).selectinload(KRSDetail.matakuliah))
            .where(KRS.nim == nim, KRS.semester == semester)
        ).one_or_none()
        if krs is None:
            raise KRSNotFoundError(
                f"KRS untuk NIM {nim} pada semester {semester} tidak ditemukan"
            )
        return krs

    def validate_krs(self, nim: str, semester: str) -> dict:
        """Memvalidasi seluruh KRS secara utuh dan mengumpulkan semua kesalahan sekaligus."""
        krs = self.get_krs(nim, semester)
        courses = [detail.matakuliah for detail in krs.details]
        total_sks = sum(mk.sks for mk in courses)
        failures: list[ValidationResult] = []

        if total_sks > MAX_SKS:
            failures.append(
                ValidationResult(
                    ok=False,
                    message=f"Total SKS {total_sks} melebihi batas maksimal {MAX_SKS}",
                )
            )

        prereq_validator = PrerequisiteValidator()
        for mk in courses:
            ctx = KRSContext(
                nim=nim,
                semester=semester,
                existing_courses=courses,
                target_course=mk,
                db=self.db,
            )
            res = prereq_validator.check(ctx)
            if not res.ok:
                failures.append(
                    ValidationResult(
                        ok=False,
                        message=f"{mk.nama}: {res.message}",
                    )
                )

        # Cek bentrok antar pasangan MK (i < j agar pasangan yang sama tidak dilaporkan 2x).
        for i in range(len(courses)):
            for j in range(i + 1, len(courses)):
                a, b = courses[i], courses[j]
                if jadwal_bentrok(a, b):
                    hari = HARI_NAMA.get(a.hari, f"Hari {a.hari}")
                    jam_a = (
                        f"{a.jam_mulai.strftime('%H:%M')}-"
                        f"{a.jam_selesai.strftime('%H:%M')}"
                    )
                    jam_b = (
                        f"{b.jam_mulai.strftime('%H:%M')}-"
                        f"{b.jam_selesai.strftime('%H:%M')}"
                    )
                    failures.append(
                        ValidationResult(
                            ok=False,
                            message=(
                                f"Jadwal bentrok antara {a.nama} ({hari} {jam_a}) "
                                f"dengan {b.nama} ({hari} {jam_b})"
                            ),
                        )
                    )

        return {
            "nim": krs.nim,
            "semester": krs.semester,
            "status": krs.status.value,
            "total_sks": total_sks,
            "valid": len(failures) == 0,
            "errors": [item.message for item in failures],
        }

    def list_matakuliah(self) -> list[MataKuliah]:
        return list(
            self.db.scalars(
                select(MataKuliah).order_by(MataKuliah.kode_mk)
            ).all()
        )

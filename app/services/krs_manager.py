from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    CalonMahasiswa,
    KRS,
    KRSAktor,
    KRSDetail,
    KRSLog,
    KRSStatus,
    MataKuliah,
    Notifikasi,
)
from app.services.krs_errors import (
    KRSError,
    KRSForbiddenError,
    KRSLockedError,
    KRSNotFoundError,
    KRSTransitionError,
    KRSValidationError,
)
from app.services.krs_validators import (
    HARI_NAMA,
    KRSContext,
    MAX_SKS,
    PrerequisiteValidator,
    ValidationResult,
    Validator,
    build_default_validators,
    chain_validators,
    jadwal_bentrok,
)

# Alias kompatibilitas dengan kode Minggu 5.
KRSStatusError = KRSLockedError

EDITABLE_KRS_STATUSES = (KRSStatus.DRAFT, KRSStatus.REVISION)


class KRSManager:
    """Service tunggal untuk mengelola seluruh alur KRS tanpa bergantung pada HTTP."""

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

    def _get_locked_krs(self, nim: str, semester: str) -> KRS:
        krs = self.db.scalars(
            select(KRS)
            .where(KRS.nim == nim, KRS.semester == semester)
            .with_for_update()
        ).one_or_none()
        if krs is None:
            raise KRSNotFoundError(
                f"KRS untuk NIM {nim} pada semester {semester} tidak ditemukan"
            )
        return krs

    def _get_or_create_locked_krs(
        self, nim: str, semester: str, dosen_pa_id: int | None = None
    ) -> KRS:
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
            now = datetime.now(timezone.utc)
            krs = KRS(
                nim=nim,
                semester=semester,
                status=KRSStatus.DRAFT,
                dosen_pa_id=dosen_pa_id,
                version=1,
                updated_at=now,
            )
            self.db.add(krs)
            self.db.flush()
        elif dosen_pa_id is not None and krs.dosen_pa_id is None:
            krs.dosen_pa_id = dosen_pa_id

        return krs

    def _ensure_editable(self, krs: KRS) -> None:
        if krs.status not in EDITABLE_KRS_STATUSES:
            raise KRSLockedError(
                f"KRS dengan status '{krs.status.value}' terkunci dan tidak dapat diubah"
            )

    def _check_optimistic_version(
        self, krs: KRS, expected_version: int | None
    ) -> None:
        if expected_version is not None and krs.version != expected_version:
            raise KRSLockedError(
                f"KRS telah berubah sejak halaman dibuka (versi diharapkan "
                f"{expected_version}, versi saat ini {krs.version}); silakan muat ulang"
            )

    def _check_dosen_pa_ownership(self, krs: KRS, dosen_pa_id: int) -> None:
        if krs.dosen_pa_id is None or krs.dosen_pa_id != dosen_pa_id:
            raise KRSForbiddenError(
                f"Dosen PA ID {dosen_pa_id} tidak berwenang memproses KRS "
                f"mahasiswa {krs.nim}"
            )

    def _touch_krs(self, krs: KRS, now: datetime | None = None) -> datetime:
        """Naikkan versi dan perbarui updated_at setiap ada perubahan pada KRS."""
        timestamp = now or datetime.now(timezone.utc)
        krs.updated_at = timestamp
        krs.version = (krs.version or 1) + 1
        return timestamp

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
        return self.db.scalars(
            select(KRS)
            .options(selectinload(KRS.details).selectinload(KRSDetail.matakuliah))
            .where(KRS.id == krs_id)
        ).one()

    def _record_log(
        self,
        krs: KRS,
        aktor: KRSAktor,
        aktor_id: str,
        aksi: str,
        dari_status: KRSStatus,
        ke_status: KRSStatus,
        catatan: str | None = None,
    ) -> None:
        self.db.add(
            KRSLog(
                krs_id=krs.id,
                aktor=aktor.value,
                aktor_id=aktor_id,
                aksi=aksi,
                dari_status=dari_status.value,
                ke_status=ke_status.value,
                catatan=catatan,
            )
        )

    def _record_notification(self, nim: str, judul: str, pesan: str) -> None:
        self.db.add(
            Notifikasi(
                nim=nim,
                judul=judul,
                pesan=pesan,
                dibaca=False,
            )
        )

    def add_course(
        self,
        nim: str,
        kode_mk: str,
        semester: str,
        dosen_pa_id: int | None = None,
    ) -> KRS:
        """Menambah satu mata kuliah ke KRS dalam satu transaksi terkunci."""
        krs = self._get_or_create_locked_krs(nim, semester, dosen_pa_id=dosen_pa_id)
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

        self.db.add(KRSDetail(krs_id=krs.id, kode_mk=kode_mk))
        self._touch_krs(krs)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise KRSValidationError(
                f"Mata kuliah {target_course.nama} ({target_course.kode_mk}) "
                f"sudah ada di KRS"
            ) from exc

        return self._load_krs_with_details(krs.id)

    def remove_course(self, nim: str, kode_mk: str, semester: str) -> KRS:
        """Menghapus satu mata kuliah dari KRS berstatus draft atau revision."""
        krs = self._get_locked_krs(nim, semester)
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
        self._touch_krs(krs)
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

    def _validate_courses(
        self, krs: KRS, courses: list[MataKuliah]
    ) -> dict:
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
                nim=krs.nim,
                semester=krs.semester,
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

    def validate_krs(self, nim: str, semester: str) -> dict:
        """Memvalidasi seluruh KRS secara utuh dan mengumpulkan semua kesalahan sekaligus."""
        krs = self.get_krs(nim, semester)
        courses = [detail.matakuliah for detail in krs.details]
        return self._validate_courses(krs, courses)

    def submit_krs(
        self,
        nim: str,
        semester: str,
        requester_nim: str | None = None,
    ) -> KRS:
        """Mengajukan KRS (DRAFT/REVISION -> SUBMITTED) dalam satu transaksi."""
        if requester_nim is not None and requester_nim != nim:
            raise KRSForbiddenError(
                "Mahasiswa hanya boleh mengajukan KRS miliknya sendiri"
            )

        krs = self._get_locked_krs(nim, semester)
        try:
            # 1. Cek transisi terlebih dahulu sebelum menjalankan validasi mahal.
            krs.cek_transisi(KRSStatus.SUBMITTED, KRSAktor.MAHASISWA)

            # 2. Tolak KRS kosong.
            courses = self._get_existing_courses(krs.id)
            if not courses:
                raise KRSValidationError(
                    "KRS kosong tidak dapat diajukan; tambahkan minimal satu mata kuliah"
                )

            # 3. Validasi ulang seluruh aturan KRS sebelum diserahkan ke dosen PA.
            validation = self._validate_courses(krs, courses)
            if not validation["valid"]:
                raise KRSValidationError("; ".join(validation["errors"]))

            # 4. Jalankan transisi melalui pintu resmi model, catat waktu & log, commit.
            dari_status = krs.status
            now = datetime.now(timezone.utc)
            krs.transition(KRSStatus.SUBMITTED, KRSAktor.MAHASISWA)
            krs.submitted_at = now
            self._touch_krs(krs, now=now)
            self._record_log(
                krs=krs,
                aktor=KRSAktor.MAHASISWA,
                aktor_id=nim,
                aksi="submit",
                dari_status=dari_status,
                ke_status=KRSStatus.SUBMITTED,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return self._load_krs_with_details(krs.id)

    def approve_krs(
        self,
        nim: str,
        semester: str,
        dosen_pa_id: int,
        expected_version: int | None = None,
    ) -> KRS:
        """Persetujuan KRS oleh Dosen PA dalam satu transaksi atomik."""
        krs = self._get_locked_krs(nim, semester)
        try:
            self._check_dosen_pa_ownership(krs, dosen_pa_id)
            krs.cek_transisi(KRSStatus.APPROVED, KRSAktor.DOSEN_PA)
            self._check_optimistic_version(krs, expected_version)

            dari_status = krs.status
            now = datetime.now(timezone.utc)
            krs.transition(KRSStatus.APPROVED, KRSAktor.DOSEN_PA)
            krs.approved_at = now
            self._touch_krs(krs, now=now)

            self._record_log(
                krs=krs,
                aktor=KRSAktor.DOSEN_PA,
                aktor_id=str(dosen_pa_id),
                aksi="approve",
                dari_status=dari_status,
                ke_status=KRSStatus.APPROVED,
            )
            self._record_notification(
                nim=nim,
                judul=f"KRS Semester {semester} Disetujui",
                pesan=(
                    f"KRS Anda pada semester {semester} telah disetujui "
                    f"oleh Dosen PA (ID {dosen_pa_id})."
                ),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return self._load_krs_with_details(krs.id)

    def revise_krs(
        self,
        nim: str,
        semester: str,
        dosen_pa_id: int,
        catatan: str,
        expected_version: int | None = None,
    ) -> KRS:
        """Pengembalian KRS untuk revisi (SUBMITTED -> REVISION) dengan catatan wajib."""
        catatan_bersih = catatan.strip() if isinstance(catatan, str) else ""
        if not catatan_bersih:
            raise KRSValidationError("Catatan revisi wajib diisi oleh dosen PA")

        krs = self._get_locked_krs(nim, semester)
        try:
            self._check_dosen_pa_ownership(krs, dosen_pa_id)
            krs.cek_transisi(KRSStatus.REVISION, KRSAktor.DOSEN_PA)
            self._check_optimistic_version(krs, expected_version)

            dari_status = krs.status
            now = datetime.now(timezone.utc)
            krs.transition(KRSStatus.REVISION, KRSAktor.DOSEN_PA)
            krs.catatan_revisi = catatan_bersih
            self._touch_krs(krs, now=now)

            self._record_log(
                krs=krs,
                aktor=KRSAktor.DOSEN_PA,
                aktor_id=str(dosen_pa_id),
                aksi="revise",
                dari_status=dari_status,
                ke_status=KRSStatus.REVISION,
                catatan=catatan_bersih,
            )
            self._record_notification(
                nim=nim,
                judul=f"KRS Semester {semester} Perlu Revisi",
                pesan=(
                    f"Dosen PA (ID {dosen_pa_id}) meminta revisi KRS semester "
                    f"{semester}: {catatan_bersih}"
                ),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return self._load_krs_with_details(krs.id)

    def get_logs(self, nim: str, semester: str) -> list[KRSLog]:
        krs = self.get_krs(nim, semester)
        return list(
            self.db.scalars(
                select(KRSLog)
                .where(KRSLog.krs_id == krs.id)
                .order_by(KRSLog.id)
            ).all()
        )

    def get_notifications(self, nim: str) -> list[Notifikasi]:
        mahasiswa = self.db.scalar(
            select(CalonMahasiswa.id).where(CalonMahasiswa.nim == nim)
        )
        if mahasiswa is None:
            raise KRSNotFoundError(
                f"Mahasiswa dengan NIM {nim} tidak ditemukan atau belum disetujui"
            )
        return list(
            self.db.scalars(
                select(Notifikasi)
                .where(Notifikasi.nim == nim)
                .order_by(Notifikasi.id)
            ).all()
        )

    def list_matakuliah(self) -> list[MataKuliah]:
        return list(
            self.db.scalars(
                select(MataKuliah).order_by(MataKuliah.kode_mk)
            ).all()
        )

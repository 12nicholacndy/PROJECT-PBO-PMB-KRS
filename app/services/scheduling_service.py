from dataclasses import dataclass
from datetime import time
import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Dosen,
    Jadwal,
    KRS,
    KRSDetail,
    Kelas,
    OutboxEvent,
    Ruangan,
)
from app.services.conflict_detector import (
    Conflict,
    detect_schedule_conflict,
    pasangan,
)
from app.services.krs_invalidator import KRSInvalidator
from app.services.krs_validators import HARI_NAMA
from app.services.outbox import OutboxDispatcher
from app.services.schedule_errors import (
    RoomCapacityError,
    ScheduleConflictError,
    ScheduleNotFoundError,
)
from app.services.schedule_events import (
    EventType,
    ScheduleEvent,
    snapshot_jadwal,
)
from app.services.schedule_observers import (
    ScheduleSubject,
    get_schedule_subject,
)

logger = logging.getLogger("app.scheduling")


@dataclass(frozen=True)
class JadwalData:
    kelas_id: int
    ruangan_kode: str
    dosen_id: int
    hari: int
    jam_mulai: time
    jam_selesai: time


def _ringkas_slot(snapshot: dict[str, Any]) -> str:
    hari = HARI_NAMA.get(int(snapshot["hari"]), f"Hari {snapshot['hari']}")
    mulai = str(snapshot["jam_mulai"])[:5]
    selesai = str(snapshot["jam_selesai"])[:5]
    return f"{hari} {mulai}-{selesai} di {snapshot['ruangan_kode']}"


class SchedulingService:
    """
    Service penjadwalan kuliah (CSP hard constraints + Observer/Outbox + KRS Invalidation):
    - Deteksi bentrok 3 dimensi (waktu-ruangan-dosen) berbasis interval setengah terbuka.
    - Capacity check: max(kelas.kuota, jumlah_terdaftar) <= ruangan.kapasitas.
    - Pessimistic locking dengan urutan tetap (Ruangan -> Dosen) anti-deadlock.
    - Pada `update()` dan `delete()`:
      1. Invalidasi KRS terdampak (SUBMITTED/APPROVED -> REVISION via KRSAktor.SISTEM).
      2. Tulis baris `outbox_events` di dalam transaksi database yang SAMA.
      3. Commit sekali di akhir.
      4. Baru SETELAH commit, jalankan `OutboxDispatcher.run()` agar notifikasi
         tidak pernah terkirim untuk perubahan yang batal dan kegagalan pengiriman
         tidak membatalkan perubahan jadwal yang sudah sah.
    """

    def __init__(
        self,
        db: Session,
        *,
        subject: ScheduleSubject | None = None,
        invalidator: KRSInvalidator | None = None,
        dispatch_after_commit: bool = True,
    ) -> None:
        self.db = db
        self.subject = subject if subject is not None else get_schedule_subject()
        self.invalidator = (
            invalidator if invalidator is not None else KRSInvalidator()
        )
        self.dispatch_after_commit = dispatch_after_commit

    def _kunci_resource(
        self, ruangan_kode: str, dosen_id: int
    ) -> tuple[Ruangan, Dosen]:
        """
        Mengunci baris Ruangan terlebih dahulu, baru Dosen (urutan tetap anti-deadlock).
        Pada SQLite klausa FOR UPDATE diabaikan; pada MySQL/PostgreSQL menjadi row-level lock.
        """
        ruangan = self.db.scalar(
            select(Ruangan)
            .where(Ruangan.kode == ruangan_kode)
            .with_for_update()
        )
        if ruangan is None:
            raise ScheduleNotFoundError(
                f"Ruangan '{ruangan_kode}' tidak ditemukan"
            )

        dosen = self.db.scalar(
            select(Dosen).where(Dosen.id == dosen_id).with_for_update()
        )
        if dosen is None:
            raise ScheduleNotFoundError(
                f"Dosen dengan id={dosen_id} tidak ditemukan"
            )

        return ruangan, dosen

    def _cari_bentrok_sql(
        self,
        data: JadwalData,
        exclude_jadwal_id: int | None = None,
    ) -> list[Conflict]:
        """
        Mencari jadwal eksisting yang bertumpuk pada hari yang sama untuk ruangan atau dosen
        yang sama, memanfaatkan composite index (hari, ruangan_kode) dan (hari, dosen_id).
        """
        stmt = select(Jadwal).where(
            Jadwal.hari == data.hari,
            Jadwal.jam_mulai < data.jam_selesai,
            data.jam_mulai < Jadwal.jam_selesai,
            or_(
                Jadwal.ruangan_kode == data.ruangan_kode,
                Jadwal.dosen_id == data.dosen_id,
            ),
        )
        if exclude_jadwal_id is not None:
            stmt = stmt.where(Jadwal.id != exclude_jadwal_id)

        existing_rows = list(self.db.scalars(stmt.order_by(Jadwal.id)).all())
        conflicts: list[Conflict] = []
        for other in existing_rows:
            affected = (
                pasangan(exclude_jadwal_id, other.id)
                if exclude_jadwal_id is not None
                else (other.id,)
            )
            if other.ruangan_kode == data.ruangan_kode:
                conflicts.append(
                    Conflict(type="room_conflict", affected=affected)
                )
            if other.dosen_id == data.dosen_id:
                conflicts.append(
                    Conflict(type="lecturer_conflict", affected=affected)
                )
        return conflicts

    def cek_kapasitas(self, ruangan: Ruangan, kelas: Kelas) -> None:
        """
        Hard constraint kapasitas: max(kelas.kuota, peserta_terdaftar) <= ruangan.kapasitas.
        Memakai '<=' karena ruangan berkapasitas 40 sah diisi tepat 40 orang.
        """
        terdaftar = (
            self.db.scalar(
                select(func.count(KRSDetail.id)).where(
                    KRSDetail.kelas_id == kelas.id
                )
            )
            or 0
        )
        dibutuhkan = max(kelas.kuota, terdaftar)
        if dibutuhkan > ruangan.kapasitas:
            raise RoomCapacityError(
                f"Kapasitas ruangan {ruangan.kode} ({ruangan.kapasitas} kursi) "
                f"tidak mencukupi untuk kelas {kelas.kode_mk}-{kelas.nama_kelas} "
                f"yang membutuhkan {dibutuhkan} kursi "
                f"(kuota={kelas.kuota}, terdaftar={terdaftar})"
            )

    def _dispatch_outbox_safely(self) -> None:
        """Menjalankan OutboxDispatcher SETELAH commit; error tidak boleh membatalkan jadwal."""
        if not self.dispatch_after_commit:
            return
        try:
            dispatcher = OutboxDispatcher(self.db, self.subject)
            dispatcher.run()
        except Exception:
            logger.exception(
                "OutboxDispatcher gagal setelah commit; event tetap tersimpan di outbox_events"
            )
            self.db.rollback()

    def _simpan(
        self,
        jadwal: Jadwal,
        data: JadwalData,
        *,
        is_new: bool,
        before: dict[str, Any] | None = None,
    ) -> Jadwal:
        kelas = self.db.get(Kelas, data.kelas_id)
        if kelas is None:
            raise ScheduleNotFoundError(
                f"Kelas dengan id={data.kelas_id} tidak ditemukan"
            )

        # 1. Kunci ruangan dulu baru dosen (urutan tetap anti-deadlock).
        ruangan, _dosen = self._kunci_resource(data.ruangan_kode, data.dosen_id)

        # 2. Cari bentrok di SQL SEBELUM menyalin/menambahkan objek ke session
        #    agar autoflush tidak menyebabkan objek menemukan dirinya sendiri.
        exclude_id = None if is_new else jadwal.id
        conflicts = self._cari_bentrok_sql(data, exclude_jadwal_id=exclude_id)
        if conflicts:
            raise ScheduleConflictError(
                "Jadwal bentrok dengan jadwal yang sudah ada",
                conflicts=conflicts,
            )

        # 3. Periksa kapasitas ruangan terhadap kuota dan jumlah terdaftar.
        self.cek_kapasitas(ruangan, kelas)

        # 4. Semua hard constraint lolos -> salin nilai.
        jadwal.kelas_id = data.kelas_id
        jadwal.ruangan_kode = data.ruangan_kode
        jadwal.dosen_id = data.dosen_id
        jadwal.hari = data.hari
        jadwal.jam_mulai = data.jam_mulai
        jadwal.jam_selesai = data.jam_selesai

        if is_new:
            self.db.add(jadwal)
        else:
            # Naikkan nomor SEQUENCE untuk ekspor iCalendar (.ics) saat jadwal berubah.
            jadwal.sequence = (jadwal.sequence or 0) + 1
            after = snapshot_jadwal(jadwal)
            after["kode_mk"] = kelas.kode_mk
            after["nama_kelas"] = kelas.nama_kelas

            # 5. Invalidasi KRS terdampak (SUBMITTED/APPROVED -> REVISION) dalam 1 transaksi.
            affected_kelas_ids = {data.kelas_id}
            if before is not None and before.get("kelas_id") is not None:
                affected_kelas_ids.add(int(before["kelas_id"]))

            alasan = (
                f"Jadwal {kelas.kode_mk}-{kelas.nama_kelas} berubah dari "
                f"{_ringkas_slot(before)} ke {_ringkas_slot(after)}. "
                f"Mohon tinjau ulang KRS Anda."
                if before is not None
                else f"Jadwal {kelas.kode_mk}-{kelas.nama_kelas} berubah."
            )
            for kid in sorted(affected_kelas_ids):
                self.invalidator.invalidate_for_kelas(self.db, kid, alasan)

            # 6. Catat event ke Transactional Outbox dalam transaksi yang sama.
            event = ScheduleEvent.create(
                event_type=EventType.SCHEDULE_CHANGED,
                jadwal_id=jadwal.id,
                kelas_id=jadwal.kelas_id,
                before=before,
                after=after,
            )
            self.db.add(OutboxEvent.from_event(event))

        # 7. Satu kali commit atomik untuk jadwal + invalidasi KRS + outbox.
        self.db.commit()
        self.db.refresh(jadwal)

        # 8. Notify HANYA setelah commit berhasil.
        if not is_new:
            self._dispatch_outbox_safely()

        return jadwal

    def create(self, data: JadwalData) -> Jadwal:
        return self._simpan(Jadwal(), data, is_new=True)

    def update(self, jadwal_id: int, data: JadwalData) -> Jadwal:
        jadwal = self.db.get(Jadwal, jadwal_id)
        if jadwal is None:
            raise ScheduleNotFoundError(
                f"Jadwal dengan id={jadwal_id} tidak ditemukan"
            )
        before = snapshot_jadwal(jadwal)
        return self._simpan(jadwal, data, is_new=False, before=before)

    def delete(self, jadwal_id: int) -> None:
        jadwal = self.db.get(Jadwal, jadwal_id)
        if jadwal is None:
            raise ScheduleNotFoundError(
                f"Jadwal dengan id={jadwal_id} tidak ditemukan"
            )
        before = snapshot_jadwal(jadwal)
        kelas_id = jadwal.kelas_id
        kode_mk = before.get("kode_mk") or f"Kelas#{kelas_id}"
        nama_kelas = before.get("nama_kelas")
        label = f"{kode_mk}-{nama_kelas}" if nama_kelas else str(kode_mk)

        alasan = (
            f"Jadwal {label} pada {_ringkas_slot(before)} telah dihapus. "
            f"Mohon tinjau ulang KRS Anda."
        )
        self.invalidator.invalidate_for_kelas(self.db, kelas_id, alasan)

        event = ScheduleEvent.create(
            event_type=EventType.SCHEDULE_DELETED,
            jadwal_id=jadwal.id,
            kelas_id=kelas_id,
            before=before,
            after=None,
        )
        self.db.delete(jadwal)
        self.db.add(OutboxEvent.from_event(event))
        self.db.commit()

        # Notify HANYA setelah commit berhasil.
        self._dispatch_outbox_safely()

    def list_jadwal(
        self,
        *,
        nim: str | None = None,
        semester: str | None = None,
    ) -> list[Jadwal]:
        stmt = (
            select(Jadwal)
            .options(
                selectinload(Jadwal.kelas).selectinload(Kelas.matakuliah),
                selectinload(Jadwal.dosen),
                selectinload(Jadwal.ruangan),
            )
            .order_by(Jadwal.hari, Jadwal.jam_mulai, Jadwal.id)
        )
        if semester is not None:
            stmt = stmt.join(Kelas, Kelas.id == Jadwal.kelas_id).where(
                Kelas.semester == semester
            )
        if nim is not None:
            stmt = (
                stmt.join(KRSDetail, KRSDetail.kelas_id == Jadwal.kelas_id)
                .join(KRS, KRS.id == KRSDetail.krs_id)
                .where(KRS.nim == nim)
                .distinct()
            )
        return list(self.db.scalars(stmt).all())

    def detect_all_conflicts(self) -> list[Conflict]:
        """
        Mendeteksi seluruh bentrok yang sudah ada di database (mis. hasil impor data lama
        atau seed pengujian) menggunakan detektor Grouping + Sort-and-Sweep.
        """
        jadwals = self.list_jadwal()
        return detect_schedule_conflict(jadwals)

    def list_outbox_events(self, limit: int = 50) -> list[OutboxEvent]:
        return list(
            self.db.scalars(
                select(OutboxEvent).order_by(OutboxEvent.id.desc()).limit(limit)
            ).all()
        )

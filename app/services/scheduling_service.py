from dataclasses import dataclass
from datetime import time

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Dosen, Jadwal, KRSDetail, Kelas, Ruangan
from app.services.conflict_detector import (
    Conflict,
    detect_schedule_conflict,
    pasangan,
)
from app.services.schedule_errors import (
    RoomCapacityError,
    ScheduleConflictError,
    ScheduleNotFoundError,
)


@dataclass(frozen=True)
class JadwalData:
    kelas_id: int
    ruangan_kode: str
    dosen_id: int
    hari: int
    jam_mulai: time
    jam_selesai: time


class SchedulingService:
    """
    Service penjadwalan kuliah (CSP hard constraints):
    - Deteksi bentrok 3 dimensi (waktu-ruangan-dosen) berbasis interval setengah terbuka.
    - Capacity check: max(kelas.kuota, jumlah_terdaftar) <= ruangan.kapasitas.
    - Pessimistic locking dengan urutan tetap (Ruangan -> Dosen) untuk mencegah deadlock
      dan race condition saat dua admin menjadwalkan secara bersamaan.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

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

    def _simpan(
        self, jadwal: Jadwal, data: JadwalData, *, is_new: bool
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

        # 4. Semua hard constraint lolos -> salin nilai, tambahkan, lalu commit.
        jadwal.kelas_id = data.kelas_id
        jadwal.ruangan_kode = data.ruangan_kode
        jadwal.dosen_id = data.dosen_id
        jadwal.hari = data.hari
        jadwal.jam_mulai = data.jam_mulai
        jadwal.jam_selesai = data.jam_selesai

        if is_new:
            self.db.add(jadwal)

        self.db.commit()
        self.db.refresh(jadwal)
        return jadwal

    def create(self, data: JadwalData) -> Jadwal:
        return self._simpan(Jadwal(), data, is_new=True)

    def update(self, jadwal_id: int, data: JadwalData) -> Jadwal:
        jadwal = self.db.get(Jadwal, jadwal_id)
        if jadwal is None:
            raise ScheduleNotFoundError(
                f"Jadwal dengan id={jadwal_id} tidak ditemukan"
            )
        return self._simpan(jadwal, data, is_new=False)

    def delete(self, jadwal_id: int) -> None:
        jadwal = self.db.get(Jadwal, jadwal_id)
        if jadwal is None:
            raise ScheduleNotFoundError(
                f"Jadwal dengan id={jadwal_id} tidak ditemukan"
            )
        # Titik integrasi Observer Pattern (Minggu 8) untuk notifikasi pembatalan jadwal.
        self.db.delete(jadwal)
        self.db.commit()

    def list_jadwal(self) -> list[Jadwal]:
        return list(
            self.db.scalars(
                select(Jadwal).order_by(
                    Jadwal.hari, Jadwal.jam_mulai, Jadwal.id
                )
            ).all()
        )

    def detect_all_conflicts(self) -> list[Conflict]:
        """
        Mendeteksi seluruh bentrok yang sudah ada di database (mis. hasil impor data lama
        atau seed pengujian) menggunakan detektor Grouping + Sort-and-Sweep.
        """
        jadwals = self.list_jadwal()
        return detect_schedule_conflict(jadwals)

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    CalonMahasiswa,
    JalurMasuk,
    NIMSequence,
    ProgramStudi,
    StatusPMB,
)
from app.schemas import RegisterRequest

MAX_NIM_SEQUENCE = 9999
MAX_NIM_RETRIES = 3


class EmailSudahTerdaftar(Exception):
    pass


class ReferensiTidakValid(Exception):
    pass


class PendaftarTidakDitemukan(Exception):
    pass


class StatusTidakValid(Exception):
    pass


class NIMTidakValid(Exception):
    pass


def format_nim(tahun: int, kode_prodi: str, nomor_urut: int) -> str:
    """Format NIM 11 digit tanpa strip: YYYY + kode_prodi(3) + nomor_urut(4)."""
    if not (1000 <= tahun <= 9999):
        raise NIMTidakValid("Tahun NIM harus terdiri dari 4 digit")
    if not isinstance(kode_prodi, str) or len(kode_prodi) != 3 or not kode_prodi.isdigit():
        raise NIMTidakValid("Kode program studi harus terdiri dari 3 digit angka")
    if not (1 <= nomor_urut <= MAX_NIM_SEQUENCE):
        raise NIMTidakValid(
            f"Nomor urut NIM harus berada di antara 1 dan {MAX_NIM_SEQUENCE}"
        )
    return f"{tahun:04d}{kode_prodi}{nomor_urut:04d}"


def generate_nim(db: Session, tahun: int, kode_prodi: str) -> str:
    """Generate NIM berurutan per (tahun, kode_prodi) dengan row lock.

    Fungsi ini sengaja hanya melakukan flush() (bukan commit()) agar seluruh
    perubahan pada proses approve terjadi dalam satu transaksi atomik.
    """
    if not isinstance(kode_prodi, str) or len(kode_prodi) != 3 or not kode_prodi.isdigit():
        raise NIMTidakValid("Kode program studi harus terdiri dari 3 digit angka")

    seq = db.scalars(
        select(NIMSequence)
        .where(
            NIMSequence.tahun == tahun,
            NIMSequence.kode_prodi == kode_prodi,
        )
        .with_for_update()
    ).one_or_none()

    if seq is None:
        # Nilai 0 diisi eksplisit karena default kolom baru berlaku saat insert.
        seq = NIMSequence(tahun=tahun, kode_prodi=kode_prodi, last_number=0)
        db.add(seq)

    seq.last_number += 1
    if seq.last_number > MAX_NIM_SEQUENCE:
        raise NIMTidakValid("Nomor urut NIM melebihi batas maksimal 9999")

    db.flush()
    return format_nim(tahun, kode_prodi, seq.last_number)


def register_pendaftar(db: Session, data: RegisterRequest) -> CalonMahasiswa:
    # Pemeriksaan awal agar pesan konflik lebih jelas.
    email_ada = db.scalar(
        select(CalonMahasiswa.id).where(CalonMahasiswa.email == str(data.email))
    )
    if email_ada is not None:
        raise EmailSudahTerdaftar("Email sudah terdaftar")

    prodi_ada = db.get(ProgramStudi, data.program_studi_id)
    jalur_ada = db.get(JalurMasuk, data.jalur_masuk_id)
    if prodi_ada is None:
        raise ReferensiTidakValid("Program studi tidak ditemukan")
    if jalur_ada is None:
        raise ReferensiTidakValid("Jalur masuk tidak ditemukan")

    pendaftar = CalonMahasiswa(
        nama_lengkap=data.nama_lengkap,
        email=str(data.email),
        no_hp=data.no_hp,
        tanggal_lahir=data.tanggal_lahir,
        alamat=data.alamat,
        program_studi_id=data.program_studi_id,
        jalur_masuk_id=data.jalur_masuk_id,
        status=StatusPMB.PENDING,
    )

    db.add(pendaftar)
    try:
        db.commit()
        db.refresh(pendaftar)
    except IntegrityError as exc:
        # Unique constraint database tetap menjadi penjaga terakhir
        # jika dua request dengan email sama datang hampir bersamaan.
        db.rollback()
        raise EmailSudahTerdaftar("Email sudah terdaftar") from exc

    return pendaftar


def approve_pendaftar(
    db: Session, pendaftar_id: int, tahun: int | None = None
) -> CalonMahasiswa:
    """Menyetujui pendaftar, menghasilkan NIM secara idempoten dalam satu transaksi."""
    for attempt in range(MAX_NIM_RETRIES):
        pendaftar = db.get(CalonMahasiswa, pendaftar_id, with_for_update=True)
        if pendaftar is None:
            raise PendaftarTidakDitemukan("Pendaftar tidak ditemukan")

        # Idempoten: jika sudah approved, kembalikan data & NIM lama tanpa menaikkan counter.
        if pendaftar.status == StatusPMB.APPROVED:
            return pendaftar

        # Status akhir rejected tidak boleh diubah kembali menjadi approved.
        if pendaftar.status != StatusPMB.PENDING:
            raise StatusTidakValid(
                "Pendaftar dengan status rejected tidak dapat disetujui"
            )

        prodi = db.get(ProgramStudi, pendaftar.program_studi_id)
        if prodi is None:
            raise NIMTidakValid("Program studi pendaftar tidak ditemukan")

        now = datetime.now(timezone.utc)
        tahun_nim = tahun if tahun is not None else now.year

        try:
            nim = generate_nim(db, tahun_nim, prodi.kode)
            pendaftar.nim = nim
            pendaftar.status = StatusPMB.APPROVED
            pendaftar.approved_at = now
            db.commit()
            db.refresh(pendaftar)
            return pendaftar
        except IntegrityError as exc:
            # Jaring pengaman jika terjadi bentrok inisialisasi baris counter
            # atau unique constraint NIM saat dua request datang bersamaan.
            db.rollback()
            if attempt == MAX_NIM_RETRIES - 1:
                raise NIMTidakValid(
                    "Gagal menghasilkan NIM unik setelah beberapa percobaan"
                ) from exc

    raise NIMTidakValid("Gagal menghasilkan NIM unik")  # pragma: no cover


def reject_pendaftar(db: Session, pendaftar_id: int) -> CalonMahasiswa:
    """Menolak pendaftar berstatus pending. Pendaftar approved tidak boleh ditolak."""
    pendaftar = db.get(CalonMahasiswa, pendaftar_id, with_for_update=True)
    if pendaftar is None:
        raise PendaftarTidakDitemukan("Pendaftar tidak ditemukan")

    if pendaftar.status == StatusPMB.REJECTED:
        return pendaftar

    if pendaftar.status != StatusPMB.PENDING:
        raise StatusTidakValid(
            "Pendaftar yang sudah disetujui tidak dapat ditolak"
        )

    pendaftar.status = StatusPMB.REJECTED
    db.commit()
    db.refresh(pendaftar)
    return pendaftar


def get_status_pendaftar(db: Session, pendaftar_id: int) -> CalonMahasiswa:
    pendaftar = db.get(CalonMahasiswa, pendaftar_id)
    if pendaftar is None:
        raise PendaftarTidakDitemukan("Pendaftar tidak ditemukan")
    return pendaftar


def get_pmb_stats(db: Session) -> dict:
    """Menghitung statistik pendaftar langsung di database menggunakan GROUP BY."""
    total = db.scalar(select(func.count(CalonMahasiswa.id))) or 0

    status_rows = db.execute(
        select(CalonMahasiswa.status, func.count(CalonMahasiswa.id))
        .group_by(CalonMahasiswa.status)
    ).all()
    per_status = {item.value: 0 for item in StatusPMB}
    for status_val, count in status_rows:
        key = status_val.value if isinstance(status_val, StatusPMB) else str(status_val)
        per_status[key] = count

    # Outer join agar prodi dan jalur masuk dengan 0 pendaftar tetap tampil.
    prodi_rows = db.execute(
        select(ProgramStudi.nama, func.count(CalonMahasiswa.id))
        .outerjoin(
            CalonMahasiswa, CalonMahasiswa.program_studi_id == ProgramStudi.id
        )
        .group_by(ProgramStudi.id, ProgramStudi.nama)
    ).all()
    per_program_studi = {nama: count for nama, count in prodi_rows}

    jalur_rows = db.execute(
        select(JalurMasuk.nama, func.count(CalonMahasiswa.id))
        .outerjoin(
            CalonMahasiswa, CalonMahasiswa.jalur_masuk_id == JalurMasuk.id
        )
        .group_by(JalurMasuk.id, JalurMasuk.nama)
    ).all()
    per_jalur_masuk = {nama: count for nama, count in jalur_rows}

    return {
        "total": total,
        "per_status": per_status,
        "per_program_studi": per_program_studi,
        "per_jalur_masuk": per_jalur_masuk,
    }

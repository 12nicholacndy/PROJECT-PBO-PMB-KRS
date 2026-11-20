from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CalonMahasiswa, JalurMasuk, ProgramStudi, StatusPMB
from app.schemas import RegisterRequest


class EmailSudahTerdaftar(Exception):
    pass


class ReferensiTidakValid(Exception):
    pass


def register_pendaftar(db: Session, data: RegisterRequest) -> CalonMahasiswa:
    normalized_email = str(data.email).lower()

    # Pemeriksaan awal agar pesan konflik lebih jelas.
    email_ada = db.scalar(
        select(CalonMahasiswa.id).where(CalonMahasiswa.email == normalized_email)
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
        email=normalized_email,
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

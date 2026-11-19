from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class StatusPMB(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProgramStudi(Base):
    __tablename__ = "program_studi"
    __table_args__ = (
        CheckConstraint("length(kode) = 3", name="ck_program_studi_kode_3"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kode: Mapped[str] = mapped_column(String(3), unique=True, nullable=False)
    nama: Mapped[str] = mapped_column(String(100), nullable=False)
    fakultas: Mapped[str] = mapped_column(String(100), nullable=False)


class JalurMasuk(Base):
    __tablename__ = "jalur_masuk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nama: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)


class CalonMahasiswa(Base):
    __tablename__ = "calon_mahasiswa"
    __table_args__ = (
        UniqueConstraint("email", name="uq_calon_mahasiswa_email"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_calon_mahasiswa_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nama_lengkap: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    no_hp: Mapped[str] = mapped_column(String(15), nullable=False)
    tanggal_lahir: Mapped[date] = mapped_column(Date, nullable=False)
    alamat: Mapped[str] = mapped_column(String(255), nullable=False)

    program_studi_id: Mapped[int] = mapped_column(
        ForeignKey("program_studi.id"), nullable=False
    )
    jalur_masuk_id: Mapped[int] = mapped_column(
        ForeignKey("jalur_masuk.id"), nullable=False
    )

    status: Mapped[StatusPMB] = mapped_column(
        SAEnum(
            StatusPMB,
            name="status_pmb",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=StatusPMB.PENDING,
        server_default=StatusPMB.PENDING.value,
    )

    # Disiapkan untuk proses approval dan generator NIM pada minggu 4.
    nim: Mapped[str | None] = mapped_column(String(30), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class NIMSequence(Base):
    __tablename__ = "nim_sequence"

    tahun: Mapped[int] = mapped_column(Integer, primary_key=True)
    kode_prodi: Mapped[str] = mapped_column(String(3), primary_key=True)
    last_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
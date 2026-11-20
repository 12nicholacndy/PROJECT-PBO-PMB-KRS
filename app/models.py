from datetime import date, datetime, time
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.services.krs_errors import KRSTransitionError


class Base(DeclarativeBase):
    pass


class StatusPMB(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class KRSStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REVISION = "revision"


class KRSAktor(str, Enum):
    MAHASISWA = "mahasiswa"
    DOSEN_PA = "dosen_pa"


# Tabel transisi resmi state machine KRS: (state_asal, aktor) -> himpunan state tujuan sah.
KRS_TRANSITIONS: dict[tuple[KRSStatus, KRSAktor], set[KRSStatus]] = {
    (KRSStatus.DRAFT, KRSAktor.MAHASISWA): {KRSStatus.SUBMITTED},
    (KRSStatus.SUBMITTED, KRSAktor.DOSEN_PA): {
        KRSStatus.APPROVED,
        KRSStatus.REVISION,
    },
    (KRSStatus.REVISION, KRSAktor.MAHASISWA): {KRSStatus.SUBMITTED},
}


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


# Tabel asosiasi self-referencing many-to-many untuk prasyarat mata kuliah.
# Composite PK (kode_mk, kode_prasyarat) mencegah duplikasi & otomatis membentuk indeks.
prerequisite_table = Table(
    "prerequisite",
    Base.metadata,
    Column("kode_mk", String(20), ForeignKey("matakuliah.kode_mk"), primary_key=True),
    Column(
        "kode_prasyarat",
        String(20),
        ForeignKey("matakuliah.kode_mk"),
        primary_key=True,
    ),
)


class MataKuliah(Base):
    __tablename__ = "matakuliah"
    __table_args__ = (
        CheckConstraint("sks BETWEEN 1 AND 4", name="ck_matakuliah_sks_1_4"),
        CheckConstraint("hari BETWEEN 1 AND 6", name="ck_matakuliah_hari_1_6"),
        CheckConstraint("jam_mulai < jam_selesai", name="ck_matakuliah_jam_valid"),
    )

    kode_mk: Mapped[str] = mapped_column(String(20), primary_key=True)
    nama: Mapped[str] = mapped_column(String(150), nullable=False)
    sks: Mapped[int] = mapped_column(Integer, nullable=False)
    hari: Mapped[int] = mapped_column(Integer, nullable=False)
    jam_mulai: Mapped[time] = mapped_column(Time, nullable=False)
    jam_selesai: Mapped[time] = mapped_column(Time, nullable=False)

    # primaryjoin: MK ini -> kolom kode_mk di tabel prerequisite
    # secondaryjoin: kolom kode_prasyarat -> MK prasyarat yang harus lulus dulu
    prasyarat: Mapped[list["MataKuliah"]] = relationship(
        "MataKuliah",
        secondary=prerequisite_table,
        primaryjoin=kode_mk == prerequisite_table.c.kode_mk,
        secondaryjoin=kode_mk == prerequisite_table.c.kode_prasyarat,
    )


class KRS(Base):
    __tablename__ = "krs"
    __table_args__ = (
        UniqueConstraint("nim", "semester", name="uq_krs_nim_semester"),
        CheckConstraint("length(semester) = 5", name="ck_krs_semester_5"),
        CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'revision')",
            name="ck_krs_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nim: Mapped[str] = mapped_column(
        String(30), ForeignKey("calon_mahasiswa.nim"), nullable=False
    )
    semester: Mapped[str] = mapped_column(String(5), nullable=False)
    status: Mapped[KRSStatus] = mapped_column(
        SAEnum(
            KRSStatus,
            name="krs_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=KRSStatus.DRAFT,
        server_default=KRSStatus.DRAFT.value,
    )
    dosen_pa_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    catatan_revisi: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    details: Mapped[list["KRSDetail"]] = relationship(
        back_populates="krs",
        cascade="all, delete-orphan",
    )
    logs: Mapped[list["KRSLog"]] = relationship(
        back_populates="krs",
        cascade="all, delete-orphan",
    )

    def cek_transisi(self, target: KRSStatus, aktor: KRSAktor) -> None:
        """Memeriksa apakah transisi status diizinkan bagi aktor tanpa mengubah state."""
        allowed = KRS_TRANSITIONS.get((self.status, aktor), set())
        if target not in allowed:
            raise KRSTransitionError(
                f"Transisi tidak diizinkan: {self.status.value} -> {target.value} "
                f"oleh {aktor.value}"
            )

    def transition(self, target: KRSStatus, aktor: KRSAktor) -> None:
        """Satu-satunya pintu resmi untuk mengubah status KRS (enkapsulasi)."""
        self.cek_transisi(target, aktor)
        self.status = target


class KRSDetail(Base):
    __tablename__ = "krs_detail"
    __table_args__ = (
        UniqueConstraint("krs_id", "kode_mk", name="uq_krs_detail_krs_mk"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    krs_id: Mapped[int] = mapped_column(ForeignKey("krs.id"), nullable=False)
    kode_mk: Mapped[str] = mapped_column(
        String(20), ForeignKey("matakuliah.kode_mk"), nullable=False
    )
    kelas_id: Mapped[int | None] = mapped_column(
        ForeignKey("kelas.id"), nullable=True, index=True
    )
    nilai: Mapped[str | None] = mapped_column(String(2), nullable=True)

    krs: Mapped["KRS"] = relationship(back_populates="details")
    matakuliah: Mapped["MataKuliah"] = relationship()
    kelas: Mapped["Kelas | None"] = relationship()


class Ruangan(Base):
    """Ruang kuliah dengan kode sebagai natural primary key."""

    __tablename__ = "ruangan"
    __table_args__ = (
        CheckConstraint("kapasitas > 0", name="ck_ruangan_kapasitas_positif"),
    )

    kode: Mapped[str] = mapped_column(String(20), primary_key=True)
    nama: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kapasitas: Mapped[int] = mapped_column(Integer, nullable=False)

    jadwals: Mapped[list["Jadwal"]] = relationship(back_populates="ruangan")


class Dosen(Base):
    """Dosen pengampu mata kuliah dengan NIDN unik."""

    __tablename__ = "dosen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nidn: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    nama: Mapped[str] = mapped_column(String(150), nullable=False)

    jadwals: Mapped[list["Jadwal"]] = relationship(back_populates="dosen")


class Kelas(Base):
    """Kelas paralel dari sebuah mata kuliah pada semester tertentu."""

    __tablename__ = "kelas"
    __table_args__ = (
        UniqueConstraint(
            "kode_mk", "nama_kelas", "semester", name="uq_kelas_mk_nama_semester"
        ),
        CheckConstraint("kuota > 0", name="ck_kelas_kuota_positif"),
        CheckConstraint("length(semester) = 5", name="ck_kelas_semester_5"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kode_mk: Mapped[str] = mapped_column(
        String(20), ForeignKey("matakuliah.kode_mk"), nullable=False
    )
    nama_kelas: Mapped[str] = mapped_column(String(10), nullable=False)
    kuota: Mapped[int] = mapped_column(Integer, nullable=False)
    semester: Mapped[str] = mapped_column(String(5), nullable=False)

    matakuliah: Mapped["MataKuliah"] = relationship()
    jadwals: Mapped[list["Jadwal"]] = relationship(
        back_populates="kelas",
        cascade="all, delete-orphan",
    )


class Jadwal(Base):
    """Satu pertemuan mingguan yang mengikat kelas, hari/jam, ruangan, dan dosen."""

    __tablename__ = "jadwal"
    __table_args__ = (
        CheckConstraint("jam_mulai < jam_selesai", name="ck_jadwal_jam_valid"),
        CheckConstraint("hari BETWEEN 1 AND 6", name="ck_jadwal_hari_1_6"),
        Index("ix_jadwal_hari_ruangan", "hari", "ruangan_kode"),
        Index("ix_jadwal_hari_dosen", "hari", "dosen_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kelas_id: Mapped[int] = mapped_column(ForeignKey("kelas.id"), nullable=False)
    ruangan_kode: Mapped[str] = mapped_column(
        String(20), ForeignKey("ruangan.kode"), nullable=False
    )
    dosen_id: Mapped[int] = mapped_column(ForeignKey("dosen.id"), nullable=False)
    hari: Mapped[int] = mapped_column(Integer, nullable=False)
    jam_mulai: Mapped[time] = mapped_column(Time, nullable=False)
    jam_selesai: Mapped[time] = mapped_column(Time, nullable=False)

    kelas: Mapped["Kelas"] = relationship(back_populates="jadwals")
    ruangan: Mapped["Ruangan"] = relationship(back_populates="jadwals")
    dosen: Mapped["Dosen"] = relationship(back_populates="jadwals")


class KRSLog(Base):
    """Audit trail setiap perubahan status KRS (submit, approve, revise)."""

    __tablename__ = "krs_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    krs_id: Mapped[int] = mapped_column(
        ForeignKey("krs.id"), nullable=False, index=True
    )
    aktor: Mapped[str] = mapped_column(String(20), nullable=False)
    aktor_id: Mapped[str] = mapped_column(String(50), nullable=False)
    aksi: Mapped[str] = mapped_column(String(30), nullable=False)
    dari_status: Mapped[str] = mapped_column(String(20), nullable=False)
    ke_status: Mapped[str] = mapped_column(String(20), nullable=False)
    catatan: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    krs: Mapped["KRS"] = relationship(back_populates="logs")


class Notifikasi(Base):
    """Notifikasi yang dicatat secara atomik di database (siap untuk Observer Pattern)."""

    __tablename__ = "notifikasi"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nim: Mapped[str] = mapped_column(
        String(30), ForeignKey("calon_mahasiswa.nim"), nullable=False, index=True
    )
    judul: Mapped[str] = mapped_column(String(150), nullable=False)
    pesan: Mapped[str] = mapped_column(String(500), nullable=False)
    dibaca: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

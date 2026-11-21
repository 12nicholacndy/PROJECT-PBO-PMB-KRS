from datetime import date, datetime, time
from enum import Enum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DDL,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Table,
    Time,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.services.krs_errors import KRSTransitionError
from app.services.schedule_events import ScheduleEvent


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
    SISTEM = "sistem"


class OutboxStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


# Tabel transisi resmi state machine KRS: (state_asal, aktor) -> himpunan state tujuan sah.
# Minggu 8 menambahkan aktor SISTEM untuk invalidasi KRS terdampak perubahan jadwal.
KRS_TRANSITIONS: dict[tuple[KRSStatus, KRSAktor], set[KRSStatus]] = {
    (KRSStatus.DRAFT, KRSAktor.MAHASISWA): {KRSStatus.SUBMITTED},
    (KRSStatus.SUBMITTED, KRSAktor.DOSEN_PA): {
        KRSStatus.APPROVED,
        KRSStatus.REVISION,
    },
    (KRSStatus.REVISION, KRSAktor.MAHASISWA): {KRSStatus.SUBMITTED},
    (KRSStatus.SUBMITTED, KRSAktor.SISTEM): {KRSStatus.REVISION},
    (KRSStatus.APPROVED, KRSAktor.SISTEM): {KRSStatus.REVISION},
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
    sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    kelas: Mapped["Kelas"] = relationship(back_populates="jadwals")
    ruangan: Mapped["Ruangan"] = relationship(back_populates="jadwals")
    dosen: Mapped["Dosen"] = relationship(back_populates="jadwals")


class KRSLog(Base):
    """Audit trail setiap perubahan status KRS (submit, approve, revise, invalidate)."""

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
    """Kotak masuk notifikasi mahasiswa (dipertahankan dari Minggu 6; event jadwal dicatat di outbox_events)."""

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


class OutboxEvent(Base):
    """
    Transactional Outbox (Minggu 8): menyimpan event perubahan jadwal di dalam
    transaksi database yang sama agar tidak ada notifikasi untuk commit yang batal
    dan tidak ada perubahan jadwal yang kehilangan event.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed')",
            name="ck_outbox_events_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_outbox_events_attempts_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        SAEnum(
            OutboxStatus,
            name="outbox_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=OutboxStatus.PENDING,
        server_default=OutboxStatus.PENDING.value,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @classmethod
    def from_event(cls, event: ScheduleEvent) -> "OutboxEvent":
        return cls(
            event_id=event.event_id,
            event_type=event.event_type.value,
            payload=event.to_payload(),
            status=OutboxStatus.PENDING,
            attempts=0,
        )

    def to_event(self) -> ScheduleEvent:
        return ScheduleEvent.from_payload(self.event_id, self.payload)


class Grade(Base):
    """Nilai mahasiswa per mata kuliah per semester (Minggu 9–10)."""

    __tablename__ = "grades"
    __table_args__ = (
        UniqueConstraint(
            "nim", "kode_mk", "semester", name="uq_grades_nim_mk_semester"
        ),
        CheckConstraint("length(semester) = 5", name="ck_grades_semester_5"),
        CheckConstraint(
            "nilai_huruf IN ('A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'D', 'E')",
            name="ck_grades_nilai_huruf",
        ),
        CheckConstraint(
            "nilai_angka >= 0.0 AND nilai_angka <= 4.0",
            name="ck_grades_nilai_angka_range",
        ),
        CheckConstraint(
            "presensi_persen >= 0.0 AND presensi_persen <= 100.0",
            name="ck_grades_presensi_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nim: Mapped[str] = mapped_column(
        String(30), ForeignKey("calon_mahasiswa.nim"), nullable=False, index=True
    )
    kode_mk: Mapped[str] = mapped_column(
        String(20), ForeignKey("matakuliah.kode_mk"), nullable=False, index=True
    )
    semester: Mapped[str] = mapped_column(String(5), nullable=False)
    nilai_huruf: Mapped[str] = mapped_column(String(2), nullable=False)
    nilai_angka: Mapped[float] = mapped_column(Float, nullable=False)
    presensi_persen: Mapped[float] = mapped_column(
        Float, nullable=False, default=100.0, server_default="100.0"
    )
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    change_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    matakuliah: Mapped["MataKuliah"] = relationship()
    histories: Mapped[list["GradeHistory"]] = relationship(
        back_populates="grade",
        cascade="all, delete-orphan",
    )


class GradeHistory(Base):
    """Audit trail otomatis setiap perubahan nilai di tabel grades."""

    __tablename__ = "grade_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grade_id: Mapped[int] = mapped_column(
        ForeignKey("grades.id", ondelete="CASCADE"), nullable=False, index=True
    )
    old_value: Mapped[str] = mapped_column(String(10), nullable=False)
    new_value: Mapped[str] = mapped_column(String(10), nullable=False)
    changed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)

    grade: Mapped["Grade"] = relationship(back_populates="histories")


class StudentAcademicSummary(Base):
    """Ringkasan IPK, total SKS lulus, dan predikat yang diperbarui otomatis setiap input/edit nilai."""

    __tablename__ = "student_academic_summary"
    __table_args__ = (
        CheckConstraint("total_sks_lulus >= 0", name="ck_summary_sks_nonneg"),
        CheckConstraint("ipk >= 0.0 AND ipk <= 4.0", name="ck_summary_ipk_range"),
    )

    nim: Mapped[str] = mapped_column(
        String(30), ForeignKey("calon_mahasiswa.nim"), primary_key=True
    )
    total_sks_lulus: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ipk: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0.0"
    )
    predikat: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="Belum Ada Nilai",
        server_default="Belum Ada Nilai",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )


GRADE_AUDIT_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_grades_after_update
AFTER UPDATE OF nilai_huruf ON grades
WHEN OLD.nilai_huruf != NEW.nilai_huruf
BEGIN
    INSERT INTO grade_history (grade_id, old_value, new_value, changed_by, changed_at, reason)
    VALUES (
        NEW.id,
        OLD.nilai_huruf,
        NEW.nilai_huruf,
        NEW.updated_by,
        CURRENT_TIMESTAMP,
        COALESCE(NULLIF(TRIM(NEW.change_reason), ''), 'Perubahan nilai')
    );
END;
"""

event.listen(
    GradeHistory.__table__,
    "after_create",
    DDL(GRADE_AUDIT_TRIGGER_SQL).execute_if(dialect="sqlite"),
)

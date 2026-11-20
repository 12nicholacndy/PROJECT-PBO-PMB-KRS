import re
from datetime import date, datetime, time

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


PHONE_PATTERN = re.compile(r"^(?:\+62|62|0)8[1-9][0-9]{6,10}$")


class RegisterRequest(BaseModel):
    nama_lengkap: str = Field(min_length=3, max_length=150)
    email: EmailStr
    no_hp: str = Field(min_length=9, max_length=15)
    tanggal_lahir: date
    alamat: str = Field(min_length=3, max_length=255)
    program_studi_id: int = Field(gt=0)
    jalur_masuk_id: int = Field(gt=0)

    @field_validator("nama_lengkap", "alamat")
    @classmethod
    def tidak_boleh_kosong(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field tidak boleh kosong")
        return value

    @field_validator("no_hp")
    @classmethod
    def validasi_nomor_hp_indonesia(cls, value: str) -> str:
        if not PHONE_PATTERN.fullmatch(value):
            raise ValueError(
                "Nomor HP harus diawali 08, 62, atau +628 dan berisi angka yang valid"
            )
        return value


class RegisterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nama_lengkap: str
    email: EmailStr
    no_hp: str
    tanggal_lahir: date
    alamat: str
    program_studi_id: int
    jalur_masuk_id: int
    status: str
    nim: str | None


class ApproveResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nama_lengkap: str
    email: EmailStr
    program_studi_id: int
    jalur_masuk_id: int
    status: str
    nim: str | None
    approved_at: datetime | None


class StatusResponse(BaseModel):
    """Output cek status publik — hanya kolom yang relevan tanpa data pribadi sensitif."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nama_lengkap: str
    program_studi_id: int
    jalur_masuk_id: int
    status: str
    nim: str | None
    approved_at: datetime | None


class StatsResponse(BaseModel):
    total: int
    per_status: dict[str, int]
    per_program_studi: dict[str, int]
    per_jalur_masuk: dict[str, int]


class AddCourseRequest(BaseModel):
    nim: str = Field(min_length=1, max_length=30)
    semester: str = Field(pattern=r"^\d{5}$")
    kode_mk: str = Field(min_length=1, max_length=20)
    dosen_pa_id: int | None = Field(default=None, gt=0)


class MataKuliahResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kode_mk: str
    nama: str
    sks: int
    hari: int
    jam_mulai: time
    jam_selesai: time


class KRSDetailResponse(BaseModel):
    kode_mk: str
    nama: str
    sks: int
    hari: int
    jam_mulai: time
    jam_selesai: time
    nilai: str | None


class KRSResponse(BaseModel):
    id: int
    nim: str
    semester: str
    status: str
    dosen_pa_id: int | None
    version: int
    catatan_revisi: str | None
    submitted_at: datetime | None
    approved_at: datetime | None
    total_sks: int
    courses: list[KRSDetailResponse]


class KRSValidateResponse(BaseModel):
    nim: str
    semester: str
    status: str
    total_sks: int
    valid: bool
    errors: list[str]


class SubmitKRSRequest(BaseModel):
    requester_nim: str | None = Field(default=None, min_length=1, max_length=30)


class ApproveKRSRequest(BaseModel):
    dosen_pa_id: int = Field(gt=0)
    version: int | None = Field(default=None, gt=0)


class ReviseKRSRequest(BaseModel):
    dosen_pa_id: int = Field(gt=0)
    catatan: str = Field(min_length=1, max_length=500)
    version: int | None = Field(default=None, gt=0)

    @field_validator("catatan")
    @classmethod
    def catatan_tidak_boleh_kosong(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Catatan revisi wajib diisi")
        return value


class KRSLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    krs_id: int
    aktor: str
    aktor_id: str
    aksi: str
    dari_status: str
    ke_status: str
    catatan: str | None
    created_at: datetime


class NotifikasiResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nim: str
    judul: str
    pesan: str
    dibaca: bool
    created_at: datetime


class KRSRecommendRequest(BaseModel):
    wajib_codes: list[str] = Field(default_factory=list)
    pilihan_codes: list[str] = Field(default_factory=list)
    max_sks: int = Field(default=24, ge=1, le=24)


class SkippedCourseResponse(BaseModel):
    kode_mk: str
    nama: str
    alasan: str


class KRSRecommendResponse(BaseModel):
    nim: str
    semester: str
    max_sks: int
    total_sks: int
    wajib_terpilih: list[str]
    pilihan_terpilih: list[str]
    recommended_courses: list[MataKuliahResponse]
    skipped_courses: list[SkippedCourseResponse]
    validated: bool


class JadwalRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kelas_id: int = Field(gt=0)
    ruangan_kode: str = Field(min_length=1, max_length=20)
    dosen_id: int = Field(gt=0)
    hari: int = Field(ge=1, le=6)
    jam_mulai: time
    jam_selesai: time

    @model_validator(mode="after")
    def validasi_rentang_jam(self) -> "JadwalRequest":
        if self.jam_mulai >= self.jam_selesai:
            raise ValueError("jam_mulai harus lebih awal daripada jam_selesai")
        return self


class JadwalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kelas_id: int
    ruangan_kode: str
    dosen_id: int
    hari: int
    jam_mulai: time
    jam_selesai: time
    sequence: int = 0


class ConflictItemResponse(BaseModel):
    type: str
    affected: list[int]


class ConflictListResponse(BaseModel):
    total: int
    conflicts: list[ConflictItemResponse]


class DeleteJadwalResponse(BaseModel):
    deleted_id: int
    message: str


class AISuggestionItemResponse(BaseModel):
    hari: int
    jam_mulai: time
    jam_selesai: time
    ruangan_kode: str
    alasan: str


class AISuggestionListResponse(BaseModel):
    jadwal_id: int
    kelas_id: int
    dosen_id: int
    available_slots_count: int
    suggestions: list[AISuggestionItemResponse]


class OutboxEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: str
    event_type: str
    status: str
    attempts: int
    last_error: str | None = None
    created_at: datetime
    processed_at: datetime | None = None


class NotificationLogResponse(BaseModel):
    channel: str
    recipient: str
    key: str
    message: str


class OutboxAuditResponse(BaseModel):
    events: list[OutboxEventResponse]
    recent_channel_logs: list[NotificationLogResponse]


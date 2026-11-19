import re
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


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
    total_sks: int
    courses: list[KRSDetailResponse]


class KRSValidateResponse(BaseModel):
    nim: str
    semester: str
    status: str
    total_sks: int
    valid: bool
    errors: list[str]


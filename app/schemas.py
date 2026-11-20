import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


PHONE_PATTERN = re.compile(r"^(?:\+62|62|0)8[1-9][0-9]{6,10}$")


class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

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
        if len(value) < 3:
            raise ValueError("Field minimal 3 karakter non-spasi")
        return value

    @field_validator("no_hp")
    @classmethod
    def validasi_nomor_hp_indonesia(cls, value: str) -> str:
        if not PHONE_PATTERN.fullmatch(value):
            raise ValueError(
                "Nomor HP harus diawali 08, 628, atau +628 dan berisi angka yang valid"
            )
        return value


class RegisterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nama_lengkap: str
    email: EmailStr
    no_hp: str
    program_studi_id: int
    jalur_masuk_id: int
    status: str
    nim: str | None
    created_at: datetime

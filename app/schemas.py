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


# --- Skema Modul Nilai & Transkrip (Minggu 9–10) ---


class GradeInputRequest(BaseModel):
    nim: str = Field(..., min_length=1, max_length=30)
    kode_mk: str = Field(..., min_length=1, max_length=20)
    semester: str = Field(..., pattern=r"^\d{5}$")
    nilai_huruf: str = Field(..., min_length=1, max_length=2)
    presensi_persen: float = Field(default=100.0)
    created_by: str = Field(..., min_length=1, max_length=100)


class GradeUpdateRequest(BaseModel):
    nilai_huruf: str = Field(..., min_length=1, max_length=2)
    changed_by: str = Field(..., min_length=1, max_length=100)
    reason: str = Field(..., min_length=1, max_length=500)
    presensi_persen: float | None = Field(default=None)


class GradeItemResponse(BaseModel):
    id: int
    nim: str
    kode_mk: str
    nama_mk: str
    sks: int
    semester: str
    nilai_huruf: str
    nilai_angka: float
    mutu: float
    lulus: bool
    presensi_persen: float
    created_by: str
    updated_by: str
    change_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class AcademicSummaryResponse(BaseModel):
    nim: str
    total_sks_lulus: int
    ipk: float
    predikat: str
    updated_at: datetime | None = None


class GradeHistoryItemResponse(BaseModel):
    id: int
    grade_id: int
    nim: str
    kode_mk: str
    nama_mk: str
    semester: str
    old_value: str
    new_value: str
    changed_by: str
    changed_at: datetime
    reason: str


class GradeInputResponse(BaseModel):
    grade: GradeItemResponse
    summary: AcademicSummaryResponse


class GradeUpdateResponse(BaseModel):
    grade: GradeItemResponse
    summary: AcademicSummaryResponse
    history: GradeHistoryItemResponse


class IPSResponse(BaseModel):
    nim: str
    semester: str
    ips: float


class IPKResponse(BaseModel):
    nim: str
    ipk: float
    total_sks_lulus: int
    predikat: str
    updated_at: datetime | None = None


class TranscriptBiodataResponse(BaseModel):
    nim: str
    nama: str
    program_studi: str
    fakultas: str
    angkatan: str


class TranscriptCourseResponse(BaseModel):
    kode_mk: str
    nama_mk: str
    sks: int
    nilai_huruf: str
    nilai_angka: float
    mutu: float
    lulus: bool
    is_retake: bool
    counted_in_ipk: bool


class TranscriptSemesterResponse(BaseModel):
    semester: str
    semester_label: str
    courses: list[TranscriptCourseResponse]
    total_sks: int
    total_mutu: float
    ips: float


class TranscriptResponse(BaseModel):
    biodata: TranscriptBiodataResponse
    semesters: list[TranscriptSemesterResponse]
    total_sks_tempuh: int
    total_sks_lulus: int
    total_mutu_lulus: float
    ipk: float
    predikat: str
    generated_at: datetime


class StudentOptionResponse(BaseModel):
    nim: str
    nama: str
    program_studi: str
    angkatan: str
    ipk: float
    total_sks_lulus: int
    predikat: str


# ============================================================
# Schemas Modul Keuangan (SPP) — Minggu 11
# ============================================================


class PaymentMethodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    category: str
    is_active: bool


class GenerateBillingRequest(BaseModel):
    semester: str = Field(..., min_length=5, max_length=5, pattern=r"^\d{5}$")
    semester_start: date | None = None


class PaymentRecordResponse(BaseModel):
    id: int
    billing_id: int
    transaction_id: str
    amount: int
    status: str
    source: str
    method_code: str
    method_name: str
    paid_at: datetime
    receipt_sent: bool
    receipt_email: str | None = None
    notes: str | None = None


class BillingItemResponse(BaseModel):
    id: int
    nim: str
    nama_mahasiswa: str | None = None
    semester: str
    amount: int
    late_fee: int
    total: int
    paid_amount: int
    remaining_amount: int
    due_date: date
    status: str
    last_reminded_at: datetime | None = None
    paid_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    payments: list[PaymentRecordResponse] = []


class GenerateBillingResponse(BaseModel):
    semester: str
    semester_start: date
    due_date: date
    created_count: int
    skipped_count: int
    total_active_students: int
    billings: list[BillingItemResponse]


class ManualPaymentRequest(BaseModel):
    billing_id: int = Field(..., ge=1)
    amount: int = Field(..., gt=0)
    method: str = Field(..., min_length=1, max_length=30)
    transaction_id: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=500)


class PaymentProcessResponse(BaseModel):
    duplicate: bool
    payment: PaymentRecordResponse
    billing: BillingItemResponse


class LateFeeCheckRequest(BaseModel):
    as_of: date | None = None


class LateFeeCheckResponse(BaseModel):
    as_of: date
    checked_count: int
    updated_count: int
    total_late_fees: int


class ReminderRequest(BaseModel):
    as_of: date | None = None
    min_days_overdue: int = Field(default=0, ge=0)


class ReminderItemResponse(BaseModel):
    billing_id: int
    nim: str
    nama: str
    email: str
    semester: str
    days_overdue: int
    remaining_amount: int


class ReminderRunResponse(BaseModel):
    as_of: date
    min_days_overdue: int
    reminded_count: int
    reminders: list[ReminderItemResponse]


class WebhookPaymentResponse(BaseModel):
    ok: bool
    duplicate: bool
    message: str
    transaction_id: str
    gateway_status: str
    billing_id: int
    billing_status: str
    paid_amount: int
    remaining_amount: int
    receipt_sent: bool
    receipt_email: str | None = None


class WebhookSignRequest(BaseModel):
    transaction_id: str = Field(..., min_length=1, max_length=100)
    billing_id: int = Field(..., ge=1)
    amount: int = Field(..., gt=0)
    status: str = Field(default="paid")
    method: str = Field(default="gateway_va")


class WebhookSignResponse(BaseModel):
    payload_json: str
    signature: str
    header_name: str
    curl_example: str


class StudentFinanceSummaryResponse(BaseModel):
    nim: str
    nama: str
    email: str
    program_studi_kode: str
    program_studi: str
    jumlah_tagihan: int
    total_pokok: int
    total_denda: int
    total_tagihan: int
    sudah_bayar: int
    sisa: int
    status: str
    krs_blocked: bool


class ProdiCollectionRateResponse(BaseModel):
    kode_prodi: str
    program_studi: str
    fakultas: str
    jumlah_mahasiswa: int
    mahasiswa_lunas: int
    mahasiswa_belum_lunas: int
    total_tagihan: int
    total_terbayar: int
    total_sisa: int
    collection_rate: float
    collection_rate_nominal: float


class MonthlyPaymentPointResponse(BaseModel):
    month: str
    label: str
    jumlah_transaksi: int
    total_bayar: int


class FinanceSummaryTotalsResponse(BaseModel):
    total_mahasiswa: int
    mahasiswa_lunas: int
    mahasiswa_menunggak: int
    total_pokok: int
    total_denda: int
    total_tagihan: int
    total_sudah_bayar: int
    total_sisa: int
    collection_rate: float
    collection_rate_nominal: float
    status_counts: dict[str, int]


class FinanceDashboardResponse(BaseModel):
    semester_filter: str | None = None
    generated_at: datetime
    summary: FinanceSummaryTotalsResponse
    per_mahasiswa: list[StudentFinanceSummaryResponse]
    per_prodi: list[ProdiCollectionRateResponse]
    monthly_payments: list[MonthlyPaymentPointResponse]


class TopArrearsItemResponse(BaseModel):
    rank: int
    nim: str
    nama: str
    total_tunggakan: int
    tunggakan_formatted: str
    lama_tunggakan_hari: int
    status: str


class AIFinancialReportRequest(BaseModel):
    csv_data: str = Field(..., min_length=1)
    reference_date: date | None = None


class AIFinancialReportResponse(BaseModel):
    periode: str
    source: str
    engine: str
    generated_at: datetime
    total_revenue_bulan_ini: int
    total_revenue_formatted: str
    collection_rate: float
    collection_rate_summary: str
    total_mahasiswa: int
    mahasiswa_sudah_bayar: int
    top_10_tunggakan: list[TopArrearsItemResponse]
    tunggakan_lebih_2_bulan_count: int
    rekomendasi: str
    ringkasan_eksekutif: str


class SchedulerJobResponse(BaseModel):
    id: str
    name: str
    trigger: str
    next_run_time: str | None = None


class SchedulerStatusResponse(BaseModel):
    running: bool
    current_semester_inferred: str
    jobs: list[SchedulerJobResponse]


class EmailLogResponse(BaseModel):
    to_email: str
    subject: str
    body: str
    category: str
    sent_at: datetime




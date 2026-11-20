from dataclasses import dataclass
from datetime import datetime, time, timedelta
import json
import os
from typing import Protocol, Sequence

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Jadwal, KRSDetail, Kelas, Ruangan
from app.services.conflict_detector import (
    JadwalLike,
    JadwalSlot,
    detect_schedule_conflict,
)
from app.services.krs_validators import HARI_NAMA
from app.services.schedule_errors import ScheduleNotFoundError

# Blok mulai standar kampus (prioritas pagi -> siang -> sore).
DEFAULT_START_TIMES: tuple[time, ...] = (
    time(8, 0),
    time(9, 40),
    time(11, 20),
    time(13, 0),
    time(14, 40),
)
MAX_AI_SUGGESTIONS = 3


class SaranJadwal(BaseModel):
    """Model Pydantic (Saringan 2) untuk memvalidasi satu item saran dari LLM."""

    hari: int = Field(ge=1, le=6)
    jam_mulai: time
    jam_selesai: time
    ruangan_kode: str = Field(min_length=1, max_length=20)
    alasan: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validasi_jam(self) -> "SaranJadwal":
        if self.jam_mulai >= self.jam_selesai:
            raise ValueError("jam_mulai harus lebih awal daripada jam_selesai")
        return self


@dataclass(frozen=True)
class AvailableSlot:
    hari: int
    jam_mulai: time
    jam_selesai: time
    ruangan_kode: str
    kapasitas: int


class LLMClient(Protocol):
    """Abstraksi klien LLM agar mudah diuji secara deterministik dengan FakeLLMClient."""

    def complete(self, prompt: str) -> str: ...


class FakeLLMClient:
    """Test double LLM deterministik untuk unit/integration test tanpa panggilan API nyata."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.last_prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response_text


class GeminiLLMClient:
    """Implementasi tier gratis Google AI Studio (Gemini Flash) dengan temperature rendah & JSON mode."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        timeout_seconds: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds

    def complete(self, prompt: str) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        response = httpx.post(
            url,
            params={"key": self._api_key},
            json=payload,
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


class OpenRouterLLMClient:
    """Implementasi alternatif OpenRouter free-tier dengan temperature rendah."""

    def __init__(
        self,
        api_key: str,
        model: str = "google/gemini-2.0-flash-exp:free",
        timeout_seconds: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds

    def complete(self, prompt: str) -> str:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


class HeuristicFallbackLLMClient:
    """
    Fallback lokal offline ketika env GEMINI_API_KEY / OPENROUTER_API_KEY belum disetel.
    Memilih maksimal 3 slot dari daftar slot tersedia di prompt (prioritas pagi/siang)
    dan menghasilkan JSON agar tetap melewati 4 lapis saringan guardrail yang sama.
    """

    def complete(self, prompt: str) -> str:
        marker = "DAFTAR_SLOT_TERSEDIA_JSON:"
        idx = prompt.find(marker)
        if idx == -1:
            return "[]"
        raw_part = prompt[idx + len(marker) :]
        start = raw_part.find("[")
        if start == -1:
            return "[]"
        try:
            slots, _ = json.JSONDecoder().raw_decode(raw_part[start:])
        except json.JSONDecodeError:
            return "[]"
        if not isinstance(slots, list):
            return "[]"

        # Prioritaskan slot pagi/siang (jam mulai <= 13:00), lalu kapasitas paling pas.
        ranked = sorted(
            slots,
            key=lambda s: (
                0 if str(s.get("jam_mulai", "23:59")) <= "13:00" else 1,
                int(s.get("hari", 7)),
                str(s.get("jam_mulai", "23:59")),
                int(s.get("kapasitas", 9999)),
            ),
        )[:MAX_AI_SUGGESTIONS]

        suggestions = []
        for s in ranked:
            hari_nama = HARI_NAMA.get(int(s["hari"]), f"Hari {s['hari']}")
            waktu_label = (
                "pagi/siang (gangguan minimal)"
                if str(s["jam_mulai"]) <= "13:00"
                else "sore"
            )
            suggestions.append(
                {
                    "hari": int(s["hari"]),
                    "jam_mulai": s["jam_mulai"],
                    "jam_selesai": s["jam_selesai"],
                    "ruangan_kode": s["ruangan_kode"],
                    "alasan": (
                        f"Slot {waktu_label} pada {hari_nama} {s['jam_mulai']}-{s['jam_selesai']} "
                        f"di {s['ruangan_kode']} (kapasitas {s['kapasitas']}) bebas bentrok ruangan & dosen."
                    ),
                }
            )
        return json.dumps(suggestions, ensure_ascii=False)


def build_llm_client_from_env() -> LLMClient:
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()
        return GeminiLLMClient(api_key=gemini_key, model=model)

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if openrouter_key:
        model = os.getenv(
            "OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free"
        ).strip()
        return OpenRouterLLMClient(api_key=openrouter_key, model=model)

    return HeuristicFallbackLLMClient()


def _duration_minutes(start: time, end: time) -> int:
    dt_start = datetime(2026, 1, 1, start.hour, start.minute, start.second)
    dt_end = datetime(2026, 1, 1, end.hour, end.minute, end.second)
    return int((dt_end - dt_start).total_seconds() // 60)


def _add_minutes(start: time, minutes: int) -> time | None:
    dt_start = datetime(2026, 1, 1, start.hour, start.minute, start.second)
    dt_end = dt_start + timedelta(minutes=minutes)
    if dt_end.date() != dt_start.date() or dt_end.hour > 18:
        return None
    return dt_end.time()


def find_available_slots(
    *,
    target: Jadwal,
    required_capacity: int,
    ruangans: Sequence[Ruangan],
    other_jadwals: Sequence[JadwalLike],
    start_times: Sequence[time] = DEFAULT_START_TIMES,
    max_slots: int = 25,
) -> list[AvailableSlot]:
    """
    Menghitung slot tersedia secara deterministik di kode sendiri (TIDAK dikarang AI):
    semua kombinasi (hari 1..6, blok jam, ruangan berkapasitas cukup) yang lolos
    `detect_schedule_conflict` dari Minggu 7.
    """
    durasi = _duration_minutes(target.jam_mulai, target.jam_selesai)
    eligible_rooms = sorted(
        (r for r in ruangans if r.kapasitas >= required_capacity),
        key=lambda r: (r.kapasitas, r.kode),
    )

    available: list[AvailableSlot] = []
    for hari in range(1, 7):
        for jam_mulai in start_times:
            jam_selesai = _add_minutes(jam_mulai, durasi)
            if jam_selesai is None:
                continue
            for ruangan in eligible_rooms:
                candidate = JadwalSlot(
                    id=-1,
                    hari=hari,
                    jam_mulai=jam_mulai,
                    jam_selesai=jam_selesai,
                    ruangan_kode=ruangan.kode,
                    dosen_id=target.dosen_id,
                )
                conflicts = detect_schedule_conflict([*other_jadwals, candidate])
                if any(-1 in c.affected for c in conflicts):
                    continue
                available.append(
                    AvailableSlot(
                        hari=hari,
                        jam_mulai=jam_mulai,
                        jam_selesai=jam_selesai,
                        ruangan_kode=ruangan.kode,
                        kapasitas=ruangan.kapasitas,
                    )
                )
                if len(available) >= max_slots:
                    return available
    return available


def build_reschedule_prompt(
    *,
    target: Jadwal,
    kelas: Kelas,
    required_capacity: int,
    available_slots: Sequence[AvailableSlot],
) -> str:
    """Menyusun prompt terstruktur untuk AI Scheduling Assistant."""
    hari_asal = HARI_NAMA.get(target.hari, f"Hari {target.hari}")
    slot_payload = [
        {
            "hari": s.hari,
            "hari_nama": HARI_NAMA.get(s.hari, f"Hari {s.hari}"),
            "jam_mulai": s.jam_mulai.strftime("%H:%M"),
            "jam_selesai": s.jam_selesai.strftime("%H:%M"),
            "ruangan_kode": s.ruangan_kode,
            "kapasitas": s.kapasitas,
        }
        for s in available_slots
    ]

    return (
        "Anda adalah Scheduling Assistant akademik universitas.\n"
        "Tugas: pilih maksimal 3 slot pengganti terbaik dari daftar slot tersedia di bawah.\n"
        "Syarat wajib:\n"
        "1. Kapasitas ruangan cukup untuk kebutuhan kursi kelas.\n"
        "2. Tidak bentrok dengan jadwal ruangan maupun jadwal dosen.\n"
        "3. Gangguan minimal bagi mahasiswa dengan prioritas jadwal pagi atau siang.\n"
        "4. Hanya pilih dari daftar slot tersedia di bawah, jangan mengarang ruangan.\n\n"
        f"Jadwal yang bentrok:\n"
        f"- jadwal_id: {target.id}\n"
        f"- kelas: {kelas.kode_mk}-{kelas.nama_kelas} (semester {kelas.semester})\n"
        f"- dosen_id: {target.dosen_id}\n"
        f"- kebutuhan_kursi: {required_capacity}\n"
        f"- slot_lama: {hari_asal} {target.jam_mulai.strftime('%H:%M')}-"
        f"{target.jam_selesai.strftime('%H:%M')} di {target.ruangan_kode}\n\n"
        "DAFTAR_SLOT_TERSEDIA_JSON:\n"
        f"{json.dumps(slot_payload, ensure_ascii=False)}\n\n"
        "Balas HANYA array JSON valid tanpa teks tambahan dengan skema tepat seperti ini:\n"
        '[{"hari": 2, "jam_mulai": "08:00", "jam_selesai": "09:40", '
        '"ruangan_kode": "A101", "alasan": "Slot pagi bebas bentrok dengan kapasitas cukup"}]'
    )


def _extract_json_array(raw_text: str) -> list[object]:
    """Saringan 1 (Parsing): ambil bagian antara '[' pertama dan ']' terakhir tanpa crash."""
    if not raw_text:
        return []
    start = raw_text.find("[")
    end = raw_text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(raw_text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def filter_ai_suggestions(
    raw_text: str,
    *,
    target_dosen_id: int,
    required_capacity: int,
    rooms_by_code: dict[str, Ruangan],
    other_jadwals: Sequence[JadwalLike],
    max_results: int = MAX_AI_SUGGESTIONS,
) -> list[SaranJadwal]:
    """
    4 Lapis Saringan (Guardrail) keluaran AI:
      1. Parsing JSON antara '[' pertama dan ']' terakhir (gagal -> []).
      2. Validasi skema Pydantic `SaranJadwal` (item rusak dibuang satu per satu).
      3. Validasi ruangan ada di database & kapasitas >= kebutuhan kursi.
      4. Validasi bentrok memakai `detect_schedule_conflict` Minggu 7 dengan calon id=-1.
    AI boleh menyarankan, validator kitalah yang memutuskan (maksimal 3 saran).
    """
    # Saringan 1: Parsing JSON aman
    raw_items = _extract_json_array(raw_text)
    if not raw_items:
        return []

    validated: list[SaranJadwal] = []
    seen_slots: set[tuple[int, time, time, str]] = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        # Saringan 2: Validasi skema Pydantic per item
        try:
            saran = SaranJadwal.model_validate(item)
        except ValidationError:
            continue

        # Saringan 3: Ruangan eksis di DB & kapasitas cukup
        ruangan = rooms_by_code.get(saran.ruangan_kode)
        if ruangan is None or ruangan.kapasitas < required_capacity:
            continue

        # Saringan 4: Deteksi bentrok 3D (ruangan & dosen) memakai mesin Minggu 7 (id=-1)
        candidate = JadwalSlot(
            id=-1,
            hari=saran.hari,
            jam_mulai=saran.jam_mulai,
            jam_selesai=saran.jam_selesai,
            ruangan_kode=saran.ruangan_kode,
            dosen_id=target_dosen_id,
        )
        conflicts = detect_schedule_conflict([*other_jadwals, candidate])
        if any(-1 in c.affected for c in conflicts):
            continue

        dedup_key = (
            saran.hari,
            saran.jam_mulai,
            saran.jam_selesai,
            saran.ruangan_kode,
        )
        if dedup_key in seen_slots:
            continue
        seen_slots.add(dedup_key)
        validated.append(saran)

        if len(validated) >= max_results:
            break

    return validated


class AIRescheduler:
    """Layanan rekomendasi penjadwalan ulang berbasis LLM + 4 lapis guardrail."""

    def __init__(self, db: Session, llm_client: LLMClient) -> None:
        self.db = db
        self.llm_client = llm_client

    def _required_capacity(self, kelas: Kelas) -> int:
        terdaftar = (
            self.db.scalar(
                select(func.count(KRSDetail.id)).where(
                    KRSDetail.kelas_id == kelas.id
                )
            )
            or 0
        )
        return max(kelas.kuota, terdaftar)

    def suggest_alternatives(
        self, jadwal_id: int
    ) -> tuple[Jadwal, list[AvailableSlot], list[SaranJadwal]]:
        target = self.db.get(Jadwal, jadwal_id)
        if target is None:
            raise ScheduleNotFoundError(
                f"Jadwal dengan id={jadwal_id} tidak ditemukan"
            )

        kelas = self.db.get(Kelas, target.kelas_id)
        if kelas is None:
            raise ScheduleNotFoundError(
                f"Kelas dengan id={target.kelas_id} tidak ditemukan"
            )

        required_capacity = self._required_capacity(kelas)
        ruangans = list(
            self.db.scalars(select(Ruangan).order_by(Ruangan.kode)).all()
        )
        rooms_by_code = {r.kode: r for r in ruangans}
        other_jadwals = list(
            self.db.scalars(
                select(Jadwal).where(Jadwal.id != jadwal_id).order_by(Jadwal.id)
            ).all()
        )

        available_slots = find_available_slots(
            target=target,
            required_capacity=required_capacity,
            ruangans=ruangans,
            other_jadwals=other_jadwals,
        )

        prompt = build_reschedule_prompt(
            target=target,
            kelas=kelas,
            required_capacity=required_capacity,
            available_slots=available_slots,
        )

        try:
            raw_response = self.llm_client.complete(prompt)
        except Exception:
            raw_response = "[]"

        suggestions = filter_ai_suggestions(
            raw_response,
            target_dosen_id=target.dosen_id,
            required_capacity=required_capacity,
            rooms_by_code=rooms_by_code,
            other_jadwals=other_jadwals,
        )
        return target, available_slots, suggestions

from __future__ import annotations

import base64
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import io
import math
import os
import threading
import time
from typing import Any
import uuid

import jwt
import qrcode

from app.services.attendance_errors import (
    AttendanceRateLimitError,
    AttendanceTokenError,
    AttendanceValidationError,
)

ATTENDANCE_JWT_SECRET = os.getenv(
    "ATTENDANCE_JWT_SECRET",
    "sia-mini-attendance-qr-secret-key-2026-pbo-minggu12",
)
ATTENDANCE_JWT_ALGORITHM = "HS256"
DEFAULT_QR_VALID_MINUTES = 15
ON_TIME_WINDOW_MINUTES = 15
MAX_LATE_WINDOW_MINUTES = 30
EARLY_CHECKIN_BUFFER_MINUTES = 15
EARTH_RADIUS_METERS = 6_371_000.0


@dataclass(frozen=True)
class AttendanceTokenClaims:
    schedule_id: int
    session_id: int | None
    jti: str
    session_date: str | None
    iat: datetime
    exp: datetime


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_naive_utc(dt: datetime) -> datetime:
    return _ensure_utc(dt).replace(tzinfo=None)


def generate_attendance_qr(
    schedule_id: int,
    valid_minutes: int = DEFAULT_QR_VALID_MINUTES,
    *,
    session_id: int | None = None,
    jti: str | None = None,
    session_date: date | None = None,
    now: datetime | None = None,
    secret_key: str | None = None,
) -> tuple[Any, str]:
    """Generate QR code image dan signed JWT HS256 untuk satu sesi kuliah.

    Mengikuti kontrak Step 1 dosen (`schedule_id`, `iat`, `exp`) ditambah `jti`
    unik per sesi dan `session_id` untuk mencegah replay attack.
    """
    if valid_minutes <= 0 or valid_minutes > 60:
        raise AttendanceValidationError(
            "Durasi validitas QR harus antara 1 sampai 60 menit"
        )

    issued_at = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=valid_minutes)
    token_jti = jti or uuid.uuid4().hex

    payload: dict[str, Any] = {
        "schedule_id": int(schedule_id),
        "jti": token_jti,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if session_id is not None:
        payload["session_id"] = int(session_id)
    if session_date is not None:
        payload["session_date"] = session_date.isoformat()

    key = secret_key or ATTENDANCE_JWT_SECRET
    token = jwt.encode(payload, key, algorithm=ATTENDANCE_JWT_ALGORITHM)
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    qr = qrcode.make(token)
    return qr, token


def qr_image_to_png_bytes(qr_image: Any) -> bytes:
    """Konversi objek gambar QR menjadi byte PNG siap kirim lewat HTTP."""
    buf = io.BytesIO()
    qr_image.save(buf, format="PNG")
    return buf.getvalue()


def qr_image_to_base64(qr_image: Any) -> str:
    """Konversi objek gambar QR menjadi data URL base64 untuk preview web UI."""
    raw = qr_image_to_png_bytes(qr_image)
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def decode_attendance_token(
    token: str,
    *,
    now: datetime | None = None,
    secret_key: str | None = None,
) -> AttendanceTokenClaims:
    """Decode dan verifikasi JWT QR code (signature HS256 + expiration)."""
    if not token or not token.strip():
        raise AttendanceTokenError("Token QR tidak boleh kosong")

    key = secret_key or ATTENDANCE_JWT_SECRET
    # Jika `now` di-inject (untuk pengujian/simulasi deterministik), kita verifikasi
    # signature terlebih dahulu lalu bandingkan `iat` & `exp` terhadap `now`.
    verify_time_builtin = now is None
    try:
        payload = jwt.decode(
            token.strip(),
            key,
            algorithms=[ATTENDANCE_JWT_ALGORITHM],
            options={
                "require": ["schedule_id", "iat", "exp"],
                "verify_exp": verify_time_builtin,
                "verify_iat": verify_time_builtin,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise AttendanceTokenError(
            "QR code sudah kedaluwarsa (expired > 15 menit)"
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise AttendanceTokenError(f"Token QR tidak valid: {exc}") from exc

    iat_ts = int(payload["iat"])
    exp_ts = int(payload["exp"])
    if now is not None:
        ref_ts = int(_ensure_utc(now).timestamp())
        if ref_ts < iat_ts:
            raise AttendanceTokenError("Token QR belum aktif (iat)")
        if ref_ts > exp_ts:
            raise AttendanceTokenError(
                "QR code sudah kedaluwarsa (expired > 15 menit)"
            )

    jti = str(payload.get("jti", "")).strip()
    if not jti:
        raise AttendanceTokenError("Token QR tidak memiliki identitas sesi unik (jti)")

    session_id_raw = payload.get("session_id")
    session_id = int(session_id_raw) if session_id_raw is not None else None
    session_date_raw = payload.get("session_date")
    session_date = str(session_date_raw) if session_date_raw is not None else None

    return AttendanceTokenClaims(
        schedule_id=int(payload["schedule_id"]),
        session_id=session_id,
        jti=jti,
        session_date=session_date,
        iat=datetime.fromtimestamp(iat_ts, tz=timezone.utc).replace(tzinfo=None),
        exp=datetime.fromtimestamp(exp_ts, tz=timezone.utc).replace(tzinfo=None),
    )


def haversine_distance_meters(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Menghitung jarak permukaan bumi (meter) antara dua koordinat GPS."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return round(EARTH_RADIUS_METERS * c, 2)


class ScanRateLimiter:
    """Sliding-window in-memory rate limiter untuk mencegah spam scan QR."""

    def __init__(self, max_requests: int = 5, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check_and_record(
        self, key: str, *, now_monotonic: float | None = None
    ) -> None:
        now_ts = time.monotonic() if now_monotonic is None else now_monotonic
        cutoff = now_ts - self.window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                retry_after = max(
                    1, int(math.ceil(self.window_seconds - (now_ts - bucket[0])))
                )
                raise AttendanceRateLimitError(
                    f"Terlalu banyak percobaan scan presensi. Coba lagi dalam {retry_after} detik.",
                    retry_after=retry_after,
                )

            bucket.append(now_ts)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_DEFAULT_SCAN_RATE_LIMITER = ScanRateLimiter(max_requests=5, window_seconds=60)


def get_scan_rate_limiter() -> ScanRateLimiter:
    return _DEFAULT_SCAN_RATE_LIMITER


def reset_scan_rate_limiter() -> None:
    _DEFAULT_SCAN_RATE_LIMITER.reset()

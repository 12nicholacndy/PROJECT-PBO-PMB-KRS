from typing import Any


class AttendanceError(Exception):
    """Base domain error untuk Modul Presensi QR Code (Minggu 12)."""

    status_code: int = 400

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code

    def body(self) -> dict[str, Any]:
        return {"detail": self.message}

    def headers(self) -> dict[str, str]:
        return {}


class AttendanceValidationError(AttendanceError):
    """Input tidak valid, waktu di luar jadwal/window kuliah, atau di luar radius lokasi."""

    status_code = 400


class AttendanceTokenError(AttendanceError):
    """JWT QR code kedaluwarsa (expired), signature tidak valid, atau replay attack."""

    status_code = 401


class AttendanceForbiddenError(AttendanceError):
    """Mahasiswa tidak terdaftar di MK/kelas ini atau peran tidak diizinkan."""

    status_code = 403


class AttendanceNotFoundError(AttendanceError):
    """Jadwal, sesi presensi, mahasiswa, atau mata kuliah tidak ditemukan."""

    status_code = 404


class AttendanceConflictError(AttendanceError):
    """Mahasiswa sudah melakukan presensi pada sesi ini (duplikat scan)."""

    status_code = 409


class AttendanceRateLimitError(AttendanceError):
    """Terlalu banyak percobaan scan dalam jendela waktu singkat (prevent spam scan)."""

    status_code = 429

    def __init__(self, message: str, retry_after: int = 60) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = max(1, int(retry_after))

    def body(self) -> dict[str, Any]:
        return {
            "detail": self.message,
            "retry_after_seconds": self.retry_after,
        }

    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}

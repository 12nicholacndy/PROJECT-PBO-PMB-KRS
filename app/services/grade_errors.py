from typing import Any


class GradeError(Exception):
    """Base domain error untuk Modul Nilai & Transkrip (Minggu 9–10)."""

    status_code: int = 400

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code

    def body(self) -> dict[str, Any]:
        return {"detail": self.message}


class GradeValidationError(GradeError):
    status_code = 400


class AttendanceRequirementError(GradeError):
    """Dilempar ketika presensi mahasiswa < 75% sehingga nilai tidak boleh diinput."""

    status_code = 400

    def __init__(
        self,
        presensi_persen: float,
        minimum_persen: float = 75.0,
    ) -> None:
        self.presensi_persen = presensi_persen
        self.minimum_persen = minimum_persen
        super().__init__(
            f"Nilai tidak dapat diinput: presensi {presensi_persen:.1f}% "
            f"kurang dari batas minimal {minimum_persen:.0f}%",
            status_code=400,
        )

    def body(self) -> dict[str, Any]:
        return {
            "detail": self.message,
            "presensi_persen": self.presensi_persen,
            "minimum_persen": self.minimum_persen,
        }


class GradeForbiddenError(GradeError):
    status_code = 403


class GradeNotFoundError(GradeError):
    status_code = 404


class GradeConflictError(GradeError):
    status_code = 409

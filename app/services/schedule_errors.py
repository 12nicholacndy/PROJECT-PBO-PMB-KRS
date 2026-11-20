from typing import Any

from app.services.conflict_detector import Conflict


class ScheduleError(Exception):
    """Base domain error untuk modul Jadwal & Ruangan dengan HTTP response polimorfik."""

    status_code: int = 400

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code

    def body(self) -> dict[str, Any]:
        return {"detail": self.message}


class ScheduleNotFoundError(ScheduleError):
    status_code = 404


class RoomCapacityError(ScheduleError):
    status_code = 409


class ScheduleConflictError(ScheduleError):
    status_code = 409

    def __init__(self, message: str, conflicts: list[Conflict]) -> None:
        super().__init__(message, status_code=409)
        self.conflicts = conflicts

    def body(self) -> dict[str, Any]:
        return {
            "detail": self.message,
            "conflicts": [
                {"type": c.type, "affected": list(c.affected)}
                for c in self.conflicts
            ],
        }

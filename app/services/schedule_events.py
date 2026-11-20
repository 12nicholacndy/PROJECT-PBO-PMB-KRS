from dataclasses import dataclass
from datetime import datetime, time, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class EventType(str, Enum):
    """Jenis event perubahan jadwal yang dipublikasikan ke observer."""

    SCHEDULE_CHANGED = "schedule_changed"
    SCHEDULE_DELETED = "schedule_deleted"


@dataclass(frozen=True)
class ScheduleEvent:
    """
    Event immutable (frozen=True) agar tidak dapat dimodifikasi oleh satu observer
    saat event yang sama masih dibaca oleh observer lain.
    """

    event_id: str
    event_type: EventType
    jadwal_id: int
    kelas_id: int
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    occurred_at: str

    @classmethod
    def create(
        cls,
        *,
        event_type: EventType,
        jadwal_id: int,
        kelas_id: int,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        event_id: str | None = None,
        occurred_at: str | None = None,
    ) -> "ScheduleEvent":
        return cls(
            event_id=event_id or uuid4().hex,
            event_type=event_type,
            jadwal_id=jadwal_id,
            kelas_id=kelas_id,
            before=before,
            after=after,
            occurred_at=occurred_at or datetime.now(timezone.utc).isoformat(),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "jadwal_id": self.jadwal_id,
            "kelas_id": self.kelas_id,
            "before": self.before,
            "after": self.after,
            "occurred_at": self.occurred_at,
        }

    @classmethod
    def from_payload(cls, event_id: str, payload: dict[str, Any]) -> "ScheduleEvent":
        return cls(
            event_id=event_id,
            event_type=EventType(payload["event_type"]),
            jadwal_id=int(payload["jadwal_id"]),
            kelas_id=int(payload["kelas_id"]),
            before=payload.get("before"),
            after=payload.get("after"),
            occurred_at=str(payload["occurred_at"]),
        )


def _format_time(value: time | str) -> str:
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    return str(value)


def snapshot_jadwal(jadwal: Any) -> dict[str, Any]:
    """Membuat snapshot dictionary JSON-serializable dari objek ORM Jadwal."""
    kelas = getattr(jadwal, "kelas", None)
    return {
        "id": jadwal.id,
        "kelas_id": jadwal.kelas_id,
        "kode_mk": getattr(kelas, "kode_mk", None),
        "nama_kelas": getattr(kelas, "nama_kelas", None),
        "ruangan_kode": jadwal.ruangan_kode,
        "dosen_id": jadwal.dosen_id,
        "hari": jadwal.hari,
        "jam_mulai": _format_time(jadwal.jam_mulai),
        "jam_selesai": _format_time(jadwal.jam_selesai),
        "sequence": getattr(jadwal, "sequence", 0),
    }

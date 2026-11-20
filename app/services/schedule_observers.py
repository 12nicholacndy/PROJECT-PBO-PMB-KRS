from contextvars import ContextVar
import logging
from typing import Literal, Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import KRS, KRSDetail
from app.services.krs_validators import HARI_NAMA
from app.services.schedule_events import EventType, ScheduleEvent

logger = logging.getLogger("app.notifications")

# ContextVar agar StudentObserver yang di-attach SEKALI saat aplikasi start tetap memakai
# sesi DB aktif milik OutboxDispatcher (termasuk in-memory DB saat pengujian pytest).
current_observer_db: ContextVar[Session | None] = ContextVar(
    "current_observer_db", default=None
)

ChannelName = Literal["email", "sms", "push"]


class NotificationChannel(Protocol):
    """Kontrak kanal pengiriman notifikasi (Strategy-like) yang mudah ditukar."""

    name: str

    def send(self, recipient: str, message: str, *, key: str) -> bool: ...


class LogChannel:
    """
    Kanal simulasi (email/sms/push) yang menulis ke logger dan menjaga idempotensi
    berdasarkan kunci unik `key` (at-least-once delivery + deduplication).
    """

    def __init__(
        self,
        name: ChannelName,
        *,
        sent_keys: set[str] | None = None,
        records: list[dict[str, str]] | None = None,
        channel_logger: logging.Logger | None = None,
    ) -> None:
        self.name = name
        self._sent_keys: set[str] = sent_keys if sent_keys is not None else set()
        self._records: list[dict[str, str]] = records if records is not None else []
        self._logger = channel_logger or logger

    @property
    def sent_keys(self) -> set[str]:
        return self._sent_keys

    @property
    def records(self) -> list[dict[str, str]]:
        return self._records

    def send(self, recipient: str, message: str, *, key: str) -> bool:
        if key in self._sent_keys:
            return False

        tag = self.name.upper()
        self._logger.info("[%s] to=%s key=%s | %s", tag, recipient, key, message)
        self._sent_keys.add(key)
        self._records.append(
            {
                "channel": self.name,
                "recipient": recipient,
                "key": key,
                "message": message,
            }
        )
        return True


class ParticipantRepository(Protocol):
    """Kontrak repository untuk mengambil daftar NIM peserta suatu kelas."""

    def get_student_nims(self, kelas_id: int) -> list[str]: ...


class SQLParticipantRepository:
    """Implementasi SQLAlchemy untuk mencari seluruh NIM mahasiswa yang mengambil kelas."""

    def __init__(self, session_factory=SessionLocal) -> None:
        self._session_factory = session_factory

    def get_student_nims(self, kelas_id: int) -> list[str]:
        active_db = current_observer_db.get()
        if active_db is not None:
            return self._query(active_db, kelas_id)

        db = self._session_factory()
        try:
            return self._query(db, kelas_id)
        finally:
            db.close()

    @staticmethod
    def _query(db: Session, kelas_id: int) -> list[str]:
        stmt = (
            select(KRS.nim)
            .join(KRSDetail, KRSDetail.krs_id == KRS.id)
            .where(KRSDetail.kelas_id == kelas_id)
            .distinct()
            .order_by(KRS.nim)
        )
        return list(db.scalars(stmt).all())


class ScheduleObserver(Protocol):
    """Kontrak Observer berbasis typing.Protocol (structural subtyping)."""

    def update(self, event: ScheduleEvent) -> None: ...


def _format_slot_text(data: dict | None) -> str:
    if not data:
        return "-"
    hari = HARI_NAMA.get(int(data["hari"]), f"Hari {data['hari']}")
    jam_mulai = str(data["jam_mulai"])[:5]
    jam_selesai = str(data["jam_selesai"])[:5]
    ruangan = data["ruangan_kode"]
    return f"{hari} {jam_mulai}-{jam_selesai} di {ruangan}"


def _format_event_message(event: ScheduleEvent) -> str:
    ref = event.after or event.before or {}
    kode_mk = ref.get("kode_mk") or f"Kelas#{event.kelas_id}"
    nama_kelas = ref.get("nama_kelas")
    label = f"{kode_mk}-{nama_kelas}" if nama_kelas else str(kode_mk)

    if event.event_type == EventType.SCHEDULE_DELETED:
        return (
            f"Jadwal {label} (ID {event.jadwal_id}) pada "
            f"{_format_slot_text(event.before)} telah dihapus."
        )
    return (
        f"Jadwal {label} (ID {event.jadwal_id}) dipindah dari "
        f"{_format_slot_text(event.before)} ke {_format_slot_text(event.after)}."
    )


class StudentObserver:
    """Mengirim notifikasi perubahan/penghapusan jadwal ke seluruh mahasiswa peserta kelas."""

    def __init__(
        self,
        participant_repo: ParticipantRepository,
        channels: Sequence[NotificationChannel],
    ) -> None:
        self._participant_repo = participant_repo
        self._channels = list(channels)

    def update(self, event: ScheduleEvent) -> None:
        nims = self._participant_repo.get_student_nims(event.kelas_id)
        message = _format_event_message(event)
        for nim in nims:
            recipient = f"mahasiswa:{nim}"
            for channel in self._channels:
                key = f"{event.event_id}:student:{nim}:{channel.name}"
                channel.send(recipient, message, key=key)


class LecturerObserver:
    """
    Mengirim notifikasi ke dosen pengampu aktif dan juga dosen lama apabila
    dosen pengampu diganti pada saat perubahan jadwal.
    """

    def __init__(self, channels: Sequence[NotificationChannel]) -> None:
        self._channels = list(channels)

    def update(self, event: ScheduleEvent) -> None:
        dosen_ids: list[int] = []
        if event.after and event.after.get("dosen_id") is not None:
            dosen_ids.append(int(event.after["dosen_id"]))
        if event.before and event.before.get("dosen_id") is not None:
            old_id = int(event.before["dosen_id"])
            if old_id not in dosen_ids:
                dosen_ids.append(old_id)

        message = _format_event_message(event)
        for dosen_id in dosen_ids:
            recipient = f"dosen:{dosen_id}"
            for channel in self._channels:
                key = f"{event.event_id}:lecturer:{dosen_id}:{channel.name}"
                channel.send(recipient, message, key=key)


class AdminObserver:
    """Mengirim ringkasan jejak perubahan jadwal ke admin akademik."""

    def __init__(
        self,
        channels: Sequence[NotificationChannel],
        admin_recipient: str = "admin_akademik",
    ) -> None:
        self._channels = list(channels)
        self._admin_recipient = admin_recipient

    def update(self, event: ScheduleEvent) -> None:
        summary = f"[AUDIT {event.event_type.value}] {_format_event_message(event)}"
        for channel in self._channels:
            key = f"{event.event_id}:admin:{channel.name}"
            channel.send(self._admin_recipient, summary, key=key)


class ScheduleSubject:
    """
    Subject pada Observer Pattern:
    - attach() menolak duplikasi observer agar tidak terjadi notifikasi ganda.
    - notify() mengiterasi salinan daftar observer & membungkus tiap update()
      dengan try/except agar satu observer gagal tidak menghentikan observer lain.
    """

    def __init__(self) -> None:
        self._observers: list[ScheduleObserver] = []

    @property
    def observers(self) -> tuple[ScheduleObserver, ...]:
        return tuple(self._observers)

    def attach(self, observer: ScheduleObserver) -> bool:
        if observer in self._observers:
            return False
        self._observers.append(observer)
        return True

    def detach(self, observer: ScheduleObserver) -> bool:
        if observer not in self._observers:
            return False
        self._observers.remove(observer)
        return True

    def notify(self, event: ScheduleEvent) -> list[str]:
        failed: list[str] = []
        for observer in list(self._observers):
            try:
                observer.update(event)
            except Exception:
                observer_name = type(observer).__name__
                logger.exception(
                    "Observer %s gagal memproses event %s",
                    observer_name,
                    event.event_id,
                )
                failed.append(observer_name)
        return failed


# Singleton aplikasi (dirangkai sekali saat aplikasi start untuk mencegah memory leak).
_default_channels: list[LogChannel] | None = None
_default_subject: ScheduleSubject | None = None


def build_default_schedule_subject(
    participant_repo: ParticipantRepository | None = None,
    channels: Sequence[NotificationChannel] | None = None,
) -> tuple[ScheduleSubject, list[NotificationChannel]]:
    active_channels: list[NotificationChannel] = (
        list(channels)
        if channels is not None
        else [LogChannel("email"), LogChannel("sms"), LogChannel("push")]
    )
    repo = participant_repo or SQLParticipantRepository()
    subject = ScheduleSubject()
    subject.attach(StudentObserver(repo, active_channels))
    subject.attach(LecturerObserver(active_channels))
    subject.attach(AdminObserver(active_channels))
    return subject, active_channels


def init_default_schedule_subject() -> ScheduleSubject:
    global _default_subject, _default_channels
    if _default_subject is None:
        channels = [LogChannel("email"), LogChannel("sms"), LogChannel("push")]
        subject, _ = build_default_schedule_subject(channels=channels)
        _default_channels = channels
        _default_subject = subject
    return _default_subject


def get_schedule_subject() -> ScheduleSubject:
    return init_default_schedule_subject()


def get_default_channels() -> list[LogChannel]:
    init_default_schedule_subject()
    assert _default_channels is not None
    return _default_channels


def reset_default_schedule_subject() -> None:
    """Helper khusus pengujian agar state singleton & log kanal bersih antar test."""
    global _default_subject, _default_channels
    _default_subject = None
    _default_channels = None

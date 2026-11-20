from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OutboxEvent, OutboxStatus
from app.services.schedule_observers import ScheduleSubject, current_observer_db

MAX_OUTBOX_ATTEMPTS = 5


class OutboxDispatcher:
    """
    Memproses baris OutboxEvent berstatus `pending` SETELAH transaksi utama di-commit.
    - Mengunci baris dengan `with_for_update(skip_locked=True)` agar worker paralel
      tidak memproses baris yang sama secara ganda.
    - Retry hingga `max_attempts` (default 5); jika masih gagal, tandai `failed`.
    - Deduplikasi ditangani oleh kunci idempotensi pada tiap NotificationChannel.
    """

    def __init__(
        self,
        db: Session,
        subject: ScheduleSubject,
        *,
        max_attempts: int = MAX_OUTBOX_ATTEMPTS,
    ) -> None:
        self.db = db
        self.subject = subject
        self.max_attempts = max_attempts

    def run(self, limit: int = 50) -> dict[str, int]:
        rows = list(
            self.db.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.status == OutboxStatus.PENDING)
                .order_by(OutboxEvent.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )

        summary = {"processed": 0, "sent": 0, "retried": 0, "failed": 0}
        if not rows:
            return summary

        token = current_observer_db.set(self.db)
        try:
            for row in rows:
                summary["processed"] += 1
                event = row.to_event()
                failed_observers = self.subject.notify(event)
                row.attempts += 1

                if not failed_observers:
                    row.status = OutboxStatus.SENT
                    row.last_error = None
                    row.processed_at = datetime.now(timezone.utc)
                    summary["sent"] += 1
                else:
                    row.last_error = (
                        f"Observer gagal: {', '.join(failed_observers)}"
                    )
                    if row.attempts >= self.max_attempts:
                        row.status = OutboxStatus.FAILED
                        row.processed_at = datetime.now(timezone.utc)
                        summary["failed"] += 1
                    else:
                        row.status = OutboxStatus.PENDING
                        summary["retried"] += 1

            self.db.commit()
        finally:
            current_observer_db.reset(token)

        return summary

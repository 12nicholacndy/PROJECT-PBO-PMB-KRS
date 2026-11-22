from __future__ import annotations

from datetime import date
import logging
import os
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.billing_service import BillingService

logger = logging.getLogger("app.finance.scheduler")


def infer_semester_code(today: date | None = None) -> str:
    """Menentukan kode semester akademik 5 digit (YYYY1 = Ganjil, YYYY2 = Genap)."""
    ref = today or date.today()
    if 2 <= ref.month <= 7:
        return f"{ref.year - 1}2"
    if ref.month == 1:
        return f"{ref.year - 1}1"
    return f"{ref.year}1"


def run_scheduled_semester_billing(
    session_factory: Callable[[], Session] = SessionLocal,
    today: date | None = None,
) -> dict[str, Any]:
    """Job terjadwal APScheduler: generate tagihan SPP awal semester untuk mahasiswa aktif."""
    ref = today or date.today()
    semester = infer_semester_code(ref)
    db = session_factory()
    try:
        result = BillingService().generate_billing(
            db, semester=semester, semester_start=ref
        )
        logger.info(
            "[SCHEDULER] Generate billing semester %s selesai: created=%d skipped=%d",
            semester,
            result["created_count"],
            result["skipped_count"],
        )
        return {
            "semester": result["semester"],
            "due_date": result["due_date"].isoformat(),
            "created_count": result["created_count"],
            "skipped_count": result["skipped_count"],
        }
    finally:
        db.close()


def run_scheduled_overdue_reminder(
    session_factory: Callable[[], Session] = SessionLocal,
    today: date | None = None,
) -> dict[str, Any]:
    """Job terjadwal APScheduler: hitung denda keterlambatan & kirim reminder otomatis."""
    ref = today or date.today()
    db = session_factory()
    try:
        result = BillingService().send_overdue_reminders(db, as_of=ref)
        logger.info(
            "[SCHEDULER] Overdue reminder selesai per %s: reminded=%d",
            ref.isoformat(),
            result["reminded_count"],
        )
        return {
            "as_of": result["as_of"].isoformat(),
            "reminded_count": result["reminded_count"],
        }
    finally:
        db.close()


class BillingSchedulerManager:
    """Pengelola cron job APScheduler untuk billing awal semester dan reminder harian."""

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._session_factory = session_factory
        self._scheduler: BackgroundScheduler | None = None

    @property
    def is_running(self) -> bool:
        return self._scheduler is not None and bool(self._scheduler.running)

    def start(self) -> None:
        if os.getenv("DISABLE_BILLING_SCHEDULER", "0") == "1":
            logger.info("[SCHEDULER] Dinonaktifkan melalui DISABLE_BILLING_SCHEDULER=1")
            return
        if self.is_running:
            return

        scheduler = BackgroundScheduler(timezone="Asia/Jakarta")
        scheduler.add_job(
            run_scheduled_semester_billing,
            trigger=CronTrigger(month="2,8", day=1, hour=0, minute=5),
            kwargs={"session_factory": self._session_factory},
            id="semester_billing_generator",
            name="Generate Tagihan SPP Otomatis Awal Semester (1 Feb & 1 Agu)",
            replace_existing=True,
        )
        scheduler.add_job(
            run_scheduled_overdue_reminder,
            trigger=CronTrigger(hour=7, minute=0),
            kwargs={"session_factory": self._session_factory},
            id="daily_overdue_reminder",
            name="Cek Overdue, Denda Keterlambatan & Reminder SPP Harian (07:00 WIB)",
            replace_existing=True,
        )
        scheduler.start()
        self._scheduler = scheduler
        logger.info("[SCHEDULER] APScheduler Modul Keuangan aktif dengan 2 cron job")

    def shutdown(self) -> None:
        if self._scheduler is not None:
            if self._scheduler.running:
                self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def get_status(self) -> dict[str, Any]:
        jobs: list[dict[str, Any]] = []
        if self._scheduler is not None:
            for job in self._scheduler.get_jobs():
                jobs.append(
                    {
                        "id": job.id,
                        "name": job.name,
                        "trigger": str(job.trigger),
                        "next_run_time": (
                            job.next_run_time.isoformat()
                            if job.next_run_time is not None
                            else None
                        ),
                    }
                )
        return {
            "running": self.is_running,
            "current_semester_inferred": infer_semester_code(),
            "jobs": jobs,
        }


_default_scheduler = BillingSchedulerManager()


def get_billing_scheduler() -> BillingSchedulerManager:
    return _default_scheduler

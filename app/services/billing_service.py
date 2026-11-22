from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import math
import os
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Billing,
    BillingStatus,
    CalonMahasiswa,
    Notifikasi,
    Payment,
    PaymentMethod,
    PaymentStatus,
    ProgramStudi,
    StatusPMB,
)
from app.services.finance_errors import (
    FinanceConflictError,
    FinanceNotFoundError,
    FinanceValidationError,
    WebhookSignatureError,
)

logger = logging.getLogger("app.finance")

DEFAULT_WEBHOOK_SECRET = "sevima-mini-webhook-secret-2026"
SPP_DUE_DAYS = 14
LATE_FEE_WEEKLY_RATE = 0.01
LATE_FEE_MAX_RATE = 0.10

# Tarif SPP berbasis program studi (sesuai spesifikasi Minggu 11: Teknik 5jt, Ekonomi 4jt).
SPP_TARIF_TEKNIK = 5_000_000
SPP_TARIF_EKONOMI = 4_000_000
SPP_TARIF_DEFAULT = 4_500_000

DEFAULT_PAYMENT_METHODS: tuple[dict[str, str], ...] = (
    {
        "code": "va_bca",
        "name": "Virtual Account BCA",
        "category": "virtual_account",
    },
    {
        "code": "va_bni",
        "name": "Virtual Account BNI",
        "category": "virtual_account",
    },
    {
        "code": "gateway_va",
        "name": "Payment Gateway Virtual Account",
        "category": "virtual_account",
    },
    {
        "code": "transfer_bank",
        "name": "Transfer Bank Manual",
        "category": "bank_transfer",
    },
    {
        "code": "qris",
        "name": "QRIS Kampus",
        "category": "qris",
    },
    {
        "code": "ewallet",
        "name": "E-Wallet (GoPay / OVO / Dana)",
        "category": "ewallet",
    },
    {
        "code": "tunai",
        "name": "Loket Keuangan (Tunai)",
        "category": "cash",
    },
)

UNPAID_BILLING_STATUSES: tuple[BillingStatus, ...] = (
    BillingStatus.UNPAID,
    BillingStatus.PARTIAL,
    BillingStatus.OVERDUE,
)

WEBHOOK_SUCCESS_STATUSES: frozenset[str] = frozenset(
    {"paid", "settlement", "success", "capture", "completed"}
)
WEBHOOK_FAILED_STATUSES: frozenset[str] = frozenset(
    {"failed", "deny", "cancel", "expire", "expired"}
)


def format_rupiah(amount: int) -> str:
    """Memformat nominal integer Rupiah dengan pemisah titik ribuan (contoh: Rp 5.000.000)."""
    sign = "-" if amount < 0 else ""
    formatted = f"{abs(int(amount)):,}".replace(",", ".")
    return f"{sign}Rp {formatted}"


def get_webhook_secret() -> str:
    return os.getenv("PAYMENT_WEBHOOK_SECRET", DEFAULT_WEBHOOK_SECRET)


def compute_webhook_signature(raw_body: bytes, secret: str | None = None) -> str:
    """Menghitung HMAC-SHA256 hex digest dari raw JSON payload webhook."""
    key = (secret if secret is not None else get_webhook_secret()).encode("utf-8")
    return hmac.new(key, raw_body, hashlib.sha256).hexdigest()


def verify_webhook_signature(
    raw_body: bytes,
    signature: str | None,
    secret: str | None = None,
) -> bool:
    """Memverifikasi HMAC-SHA256 webhook secara constant-time (mendukung prefix sha256=)."""
    if not signature or not signature.strip():
        return False
    provided = signature.strip()
    if provided.lower().startswith("sha256="):
        provided = provided[7:].strip()
    expected = compute_webhook_signature(raw_body, secret=secret)
    return hmac.compare_digest(provided.lower(), expected.lower())


def resolve_spp_amount(prodi: ProgramStudi) -> int:
    """Menentukan nominal SPP per semester berdasarkan program studi / fakultas.

    - Teknik (nama/fakultas mengandung 'teknik') -> Rp 5.000.000
    - Ekonomi (nama/fakultas mengandung 'ekonomi', 'manajemen', 'akuntansi') -> Rp 4.000.000
    - Lainnya -> Rp 4.500.000
    """
    text = f"{prodi.nama} {prodi.fakultas}".lower()
    if "teknik" in text:
        return SPP_TARIF_TEKNIK
    if any(kw in text for kw in ("ekonomi", "manajemen", "akuntansi", "bisnis")):
        return SPP_TARIF_EKONOMI
    return SPP_TARIF_DEFAULT


def calculate_late_fee(billing: Billing, as_of: date | None = None) -> int:
    """Menghitung denda keterlambatan SPP berdasarkan hari lewat jatuh tempo.

    Aturan:
    - Jika sudah lunas (`paid`) atau belum melewati `due_date`, denda = `billing.late_fee` (atau 0).
    - Jika melewati `due_date`: 1% per minggu keterlambatan (dibulatkan ke atas per minggu),
      maksimal 10% dari pokok tagihan (`billing.amount`).
    """
    check_date = as_of or date.today()
    if billing.status == BillingStatus.PAID:
        return billing.late_fee
    if check_date <= billing.due_date:
        return 0
    days_late = (check_date - billing.due_date).days
    weeks_late = math.ceil(days_late / 7)
    rate = min(weeks_late * LATE_FEE_WEEKLY_RATE, LATE_FEE_MAX_RATE)
    return int(round(billing.amount * rate))


@dataclass(frozen=True)
class EmailDispatchRecord:
    to_email: str
    subject: str
    body: str
    category: str
    sent_at: datetime


class EmailNotificationSimulator:
    """Simulasi pengiriman email kwitansi & reminder SPP tanpa dependensi SMTP eksternal."""

    def __init__(self) -> None:
        self._sent: list[EmailDispatchRecord] = []

    def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        category: str = "receipt",
    ) -> EmailDispatchRecord:
        record = EmailDispatchRecord(
            to_email=to_email,
            subject=subject,
            body=body,
            category=category,
            sent_at=datetime.now(timezone.utc),
        )
        self._sent.append(record)
        logger.info(
            "[EMAIL %s] to=%s subject=%s body=%s",
            category.upper(),
            to_email,
            subject,
            body,
        )
        return record

    def history(self) -> list[EmailDispatchRecord]:
        return list(self._sent)

    def clear(self) -> None:
        self._sent.clear()


_default_email_simulator = EmailNotificationSimulator()


def get_email_simulator() -> EmailNotificationSimulator:
    return _default_email_simulator


def reset_email_simulator() -> None:
    _default_email_simulator.clear()


class BillingService:
    """Service utama Modul Keuangan (SPP): generator, pembayaran, denda, webhook, & blokir KRS."""

    def __init__(
        self,
        email_simulator: EmailNotificationSimulator | None = None,
    ) -> None:
        self.email_simulator = email_simulator or get_email_simulator()

    def ensure_default_payment_methods(self, db: Session) -> list[PaymentMethod]:
        """Memastikan metode pembayaran default tersedia di tabel payment_method (idempoten)."""
        existing_codes = set(db.scalars(select(PaymentMethod.code)).all())
        added = False
        for item in DEFAULT_PAYMENT_METHODS:
            if item["code"] not in existing_codes:
                db.add(
                    PaymentMethod(
                        code=item["code"],
                        name=item["name"],
                        category=item["category"],
                        is_active=True,
                    )
                )
                added = True
        if added:
            db.flush()
        return list(
            db.scalars(
                select(PaymentMethod).order_by(PaymentMethod.id.asc())
            ).all()
        )

    def list_payment_methods(self, db: Session) -> list[PaymentMethod]:
        methods = self.ensure_default_payment_methods(db)
        db.commit()
        return methods

    def _resolve_payment_method(
        self, db: Session, method: str | int
    ) -> PaymentMethod:
        self.ensure_default_payment_methods(db)
        pm: PaymentMethod | None = None
        if isinstance(method, int):
            pm = db.get(PaymentMethod, method)
        else:
            code = str(method).strip().lower()
            if code.isdigit():
                pm = db.get(PaymentMethod, int(code))
            if pm is None:
                pm = db.scalars(
                    select(PaymentMethod).where(PaymentMethod.code == code)
                ).one_or_none()
        if pm is None or not pm.is_active:
            raise FinanceNotFoundError(
                f"Metode pembayaran '{method}' tidak ditemukan atau tidak aktif"
            )
        return pm

    def generate_billing(
        self,
        db: Session,
        semester: str,
        semester_start: date | None = None,
    ) -> dict[str, Any]:
        """Generate tagihan SPP awal semester untuk seluruh mahasiswa aktif secara idempoten.

        - Tarif berdasar program_studi (Teknik: 5jt, Ekonomi: 4jt, lainnya: 4.5jt).
        - Due date: 2 minggu (14 hari) setelah semester_start.
        - Status awal: 'unpaid'.
        """
        sem = semester.strip()
        if len(sem) != 5 or not sem.isdigit():
            raise FinanceValidationError(
                "Format semester harus 5 digit angka (contoh: 20261)"
            )

        start_date = semester_start or date.today()
        due_date = start_date + timedelta(days=SPP_DUE_DAYS)

        active_students = db.execute(
            select(CalonMahasiswa, ProgramStudi)
            .join(ProgramStudi, ProgramStudi.id == CalonMahasiswa.program_studi_id)
            .where(
                CalonMahasiswa.status == StatusPMB.APPROVED,
                CalonMahasiswa.nim.is_not(None),
            )
            .order_by(CalonMahasiswa.nim.asc())
        ).all()

        existing_nims = set(
            db.scalars(
                select(Billing.nim).where(Billing.semester == sem)
            ).all()
        )

        created_billings: list[Billing] = []
        skipped_count = 0
        now = datetime.now(timezone.utc)

        try:
            for mahasiswa, prodi in active_students:
                if mahasiswa.nim is None:
                    continue
                if mahasiswa.nim in existing_nims:
                    skipped_count += 1
                    continue
                amount = resolve_spp_amount(prodi)
                billing = Billing(
                    nim=mahasiswa.nim,
                    semester=sem,
                    amount=amount,
                    late_fee=0,
                    paid_amount=0,
                    due_date=due_date,
                    status=BillingStatus.UNPAID,
                    updated_at=now,
                )
                db.add(billing)
                created_billings.append(billing)
            db.commit()
        except Exception:
            db.rollback()
            raise

        for billing in created_billings:
            db.refresh(billing)

        return {
            "semester": sem,
            "semester_start": start_date,
            "due_date": due_date,
            "created_count": len(created_billings),
            "skipped_count": skipped_count,
            "total_active_students": len(active_students),
            "billings": created_billings,
        }

    def calculate_and_apply_late_fees(
        self,
        db: Session,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """Menghitung denda keterlambatan dan menandai tagihan lewat jatuh tempo menjadi 'overdue'."""
        check_date = as_of or date.today()
        candidates = list(
            db.scalars(
                select(Billing)
                .where(
                    Billing.status.in_(UNPAID_BILLING_STATUSES),
                    Billing.due_date < check_date,
                )
                .with_for_update()
            ).all()
        )

        updated_count = 0
        total_late_fees = 0
        now = datetime.now(timezone.utc)

        try:
            for billing in candidates:
                fee = calculate_late_fee(billing, as_of=check_date)
                changed = False
                if billing.late_fee != fee:
                    billing.late_fee = fee
                    changed = True
                if billing.status != BillingStatus.OVERDUE:
                    billing.status = BillingStatus.OVERDUE
                    changed = True
                if changed:
                    billing.updated_at = now
                    updated_count += 1
                total_late_fees += billing.late_fee
            db.commit()
        except Exception:
            db.rollback()
            raise

        return {
            "as_of": check_date,
            "checked_count": len(candidates),
            "updated_count": updated_count,
            "total_late_fees": total_late_fees,
        }

    def send_overdue_reminders(
        self,
        db: Session,
        as_of: date | None = None,
        min_days_overdue: int = 0,
    ) -> dict[str, Any]:
        """Mengirim pengingat otomatis (email simulasi + notifikasi DB) ke mahasiswa menunggak."""
        check_date = as_of or date.today()
        # Pastikan status overdue & denda terbaru sudah diterapkan terlebih dahulu.
        self.calculate_and_apply_late_fees(db, as_of=check_date)

        cutoff_date = check_date - timedelta(days=max(0, min_days_overdue))
        rows = db.execute(
            select(Billing, CalonMahasiswa)
            .join(CalonMahasiswa, CalonMahasiswa.nim == Billing.nim)
            .where(
                Billing.status == BillingStatus.OVERDUE,
                Billing.due_date <= cutoff_date,
            )
            .order_by(Billing.due_date.asc(), Billing.nim.asc())
        ).all()

        now = datetime.now(timezone.utc)
        reminders: list[dict[str, Any]] = []

        try:
            for billing, mahasiswa in rows:
                days_overdue = (check_date - billing.due_date).days
                subject = (
                    f"[Reminder SPP] Tunggakan Semester {billing.semester} - "
                    f"{mahasiswa.nama_lengkap} ({billing.nim})"
                )
                body = (
                    f"Yth. {mahasiswa.nama_lengkap} ({billing.nim}), tagihan SPP semester "
                    f"{billing.semester} Anda telah melewati jatuh tempo ({billing.due_date.isoformat()}, "
                    f"terlambat {days_overdue} hari). Sisa kewajiban termasuk denda "
                    f"{format_rupiah(billing.late_fee)}: {format_rupiah(billing.remaining_amount)}. "
                    f"Harap segera melunasi agar pengisian KRS tidak diblokir."
                )
                self.email_simulator.send(
                    to_email=mahasiswa.email,
                    subject=subject,
                    body=body,
                    category="reminder",
                )
                db.add(
                    Notifikasi(
                        nim=billing.nim,
                        judul=f"Reminder Tunggakan SPP Semester {billing.semester}",
                        pesan=body[:500],
                        dibaca=False,
                    )
                )
                billing.last_reminded_at = now
                billing.updated_at = now
                reminders.append(
                    {
                        "billing_id": billing.id,
                        "nim": billing.nim,
                        "nama": mahasiswa.nama_lengkap,
                        "email": mahasiswa.email,
                        "semester": billing.semester,
                        "days_overdue": days_overdue,
                        "remaining_amount": billing.remaining_amount,
                    }
                )
            db.commit()
        except Exception:
            db.rollback()
            raise

        return {
            "as_of": check_date,
            "min_days_overdue": min_days_overdue,
            "reminded_count": len(reminders),
            "reminders": reminders,
        }

    def process_payment(
        self,
        db: Session,
        billing_id: int,
        amount: int,
        method: str | int,
        *,
        transaction_id: str | None = None,
        source: str = "manual",
        notes: str | None = None,
        raw_payload: dict[str, Any] | None = None,
        paid_at: datetime | None = None,
        send_receipt: bool = True,
    ) -> tuple[Payment, Billing, bool]:
        """Memproses pembayaran SPP dalam satu transaksi database atomik.

        Mengembalikan `(payment, billing, is_duplicate)`.
        - Jika akumulasi `paid_amount + amount < billing.total` -> status `'partial'`.
        - Jika akumulasi `paid_amount + amount >= billing.total` -> status `'paid'`.
        - Mencatat record `payment`, memperbarui `billing`, serta mengirim kwitansi email simulasi.
        """
        if amount <= 0:
            raise FinanceValidationError("Nominal pembayaran harus lebih besar dari 0")
        if source not in ("manual", "webhook"):
            raise FinanceValidationError("Sumber pembayaran harus 'manual' atau 'webhook'")

        tx_id = (
            transaction_id.strip()
            if transaction_id and transaction_id.strip()
            else f"PAY-{billing_id}-{uuid4().hex[:12].upper()}"
        )

        # Idempotensi webhook berdasarkan transaction_id.
        existing_payment = db.scalars(
            select(Payment)
            .options(selectinload(Payment.payment_method))
            .where(Payment.transaction_id == tx_id)
        ).one_or_none()
        if existing_payment is not None:
            if source == "webhook":
                existing_billing = self.get_billing(db, existing_payment.billing_id)
                return existing_payment, existing_billing, True
            raise FinanceConflictError(
                f"Transaction ID '{tx_id}' sudah pernah dicatat"
            )

        try:
            billing = db.scalars(
                select(Billing)
                .where(Billing.id == billing_id)
                .with_for_update()
            ).one_or_none()
            if billing is None:
                raise FinanceNotFoundError(
                    f"Tagihan SPP dengan ID {billing_id} tidak ditemukan"
                )

            if billing.status == BillingStatus.PAID:
                raise FinanceConflictError(
                    f"Tagihan SPP ID {billing_id} (NIM {billing.nim}, semester "
                    f"{billing.semester}) sudah berstatus lunas ('paid')"
                )

            pm = self._resolve_payment_method(db, method)
            mahasiswa = db.scalars(
                select(CalonMahasiswa).where(CalonMahasiswa.nim == billing.nim)
            ).one_or_none()

            now = paid_at or datetime.now(timezone.utc)
            receipt_email = mahasiswa.email if mahasiswa is not None else None

            payment = Payment(
                billing_id=billing.id,
                payment_method_id=pm.id,
                transaction_id=tx_id,
                amount=amount,
                status=PaymentStatus.SUCCESS,
                source=source,
                paid_at=now,
                receipt_sent=False,
                receipt_email=receipt_email,
                notes=notes,
                raw_payload=raw_payload,
            )
            db.add(payment)

            billing.paid_amount += amount
            if billing.paid_amount >= billing.total:
                billing.status = BillingStatus.PAID
                billing.paid_at = now
            else:
                billing.status = BillingStatus.PARTIAL
            billing.updated_at = now

            if send_receipt and receipt_email:
                nama_mhs = mahasiswa.nama_lengkap if mahasiswa else billing.nim
                subject = (
                    f"[Kwitansi SPP] Pembayaran {billing.semester} - {tx_id}"
                )
                body = (
                    f"Terima kasih {nama_mhs} ({billing.nim}). Pembayaran SPP semester "
                    f"{billing.semester} sebesar {format_rupiah(amount)} melalui {pm.name} "
                    f"(Ref: {tx_id}) telah diterima. Total terbayar: "
                    f"{format_rupiah(billing.paid_amount)} / {format_rupiah(billing.total)}. "
                    f"Status tagihan saat ini: {billing.status.value.upper()}."
                )
                self.email_simulator.send(
                    to_email=receipt_email,
                    subject=subject,
                    body=body,
                    category="receipt",
                )
                payment.receipt_sent = True
                db.add(
                    Notifikasi(
                        nim=billing.nim,
                        judul=f"Kwitansi Pembayaran SPP Semester {billing.semester}",
                        pesan=body[:500],
                        dibaca=False,
                    )
                )

            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise FinanceConflictError(
                f"Konflik integritas pembayaran untuk transaksi '{tx_id}'"
            ) from exc
        except Exception:
            db.rollback()
            raise

        payment_loaded = db.scalars(
            select(Payment)
            .options(selectinload(Payment.payment_method))
            .where(Payment.id == payment.id)
        ).one()
        billing_loaded = self.get_billing(db, billing.id)
        return payment_loaded, billing_loaded, False

    def handle_webhook(
        self,
        db: Session,
        raw_body: bytes,
        signature: str | None,
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Menangani notifikasi webhook dari payment gateway (simulasi).

        1. Verifikasi HMAC-SHA256 signature (`X-Webhook-Signature`).
        2. Parse & ekstrak `transaction_id`, `billing_id`, `amount`, `status`, `method`.
        3. Panggil `process_payment` secara atomik (atau catat gagal/pending jika status non-sukses).
        4. Kirim kwitansi via email simulasi & kembalikan respon 200 OK.
        """
        if not verify_webhook_signature(raw_body, signature, secret=secret):
            raise WebhookSignatureError(
                "Webhook signature tidak valid atau header X-Webhook-Signature tidak disertakan"
            )

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FinanceValidationError(
                "Payload webhook bukan JSON yang valid"
            ) from exc

        if not isinstance(payload, dict):
            raise FinanceValidationError("Payload webhook harus berupa objek JSON")

        transaction_id = str(payload.get("transaction_id", "")).strip()
        gateway_status = str(payload.get("status", "")).strip().lower()
        method_code = str(payload.get("method", "gateway_va")).strip().lower() or "gateway_va"

        if not transaction_id:
            raise FinanceValidationError("Field 'transaction_id' wajib diisi pada webhook")
        if not gateway_status:
            raise FinanceValidationError("Field 'status' wajib diisi pada webhook")

        try:
            billing_id = int(payload["billing_id"])
            amount = int(payload["amount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FinanceValidationError(
                "Field 'billing_id' dan 'amount' wajib berupa bilangan bulat valid"
            ) from exc

        if amount <= 0:
            raise FinanceValidationError("Field 'amount' pada webhook harus lebih besar dari 0")

        if gateway_status in WEBHOOK_SUCCESS_STATUSES:
            payment, billing, is_duplicate = self.process_payment(
                db,
                billing_id=billing_id,
                amount=amount,
                method=method_code,
                transaction_id=transaction_id,
                source="webhook",
                notes=f"Webhook gateway status={gateway_status}",
                raw_payload=payload,
                send_receipt=True,
            )
            return {
                "ok": True,
                "duplicate": is_duplicate,
                "message": (
                    "Webhook sudah pernah diproses sebelumnya (idempoten)"
                    if is_duplicate
                    else "Pembayaran webhook berhasil diproses dan kwitansi email terkirim"
                ),
                "transaction_id": payment.transaction_id,
                "gateway_status": gateway_status,
                "billing_id": billing.id,
                "billing_status": billing.status.value,
                "paid_amount": billing.paid_amount,
                "remaining_amount": billing.remaining_amount,
                "receipt_sent": payment.receipt_sent,
                "receipt_email": payment.receipt_email,
            }

        # Status non-sukses (pending / failed / expired): catat transaksi tanpa menambah paid_amount.
        payment_status = (
            PaymentStatus.FAILED
            if gateway_status in WEBHOOK_FAILED_STATUSES
            else PaymentStatus.PENDING
        )

        existing = db.scalars(
            select(Payment).where(Payment.transaction_id == transaction_id)
        ).one_or_none()
        billing = self.get_billing(db, billing_id)

        if existing is None:
            try:
                pm = self._resolve_payment_method(db, method_code)
                db.add(
                    Payment(
                        billing_id=billing.id,
                        payment_method_id=pm.id,
                        transaction_id=transaction_id,
                        amount=amount,
                        status=payment_status,
                        source="webhook",
                        paid_at=datetime.now(timezone.utc),
                        receipt_sent=False,
                        notes=f"Webhook gateway status={gateway_status}",
                        raw_payload=payload,
                    )
                )
                db.commit()
            except Exception:
                db.rollback()
                raise

        return {
            "ok": True,
            "duplicate": existing is not None,
            "message": f"Webhook diterima dengan status gateway '{gateway_status}' (tagihan belum dilunasi)",
            "transaction_id": transaction_id,
            "gateway_status": gateway_status,
            "billing_id": billing.id,
            "billing_status": billing.status.value,
            "paid_amount": billing.paid_amount,
            "remaining_amount": billing.remaining_amount,
            "receipt_sent": False,
            "receipt_email": None,
        }

    def get_student_arrears(self, db: Session, nim: str) -> list[Billing]:
        """Mengambil seluruh tagihan SPP mahasiswa yang belum lunas (unpaid/partial/overdue)."""
        return list(
            db.scalars(
                select(Billing)
                .where(
                    Billing.nim == nim,
                    Billing.status.in_(UNPAID_BILLING_STATUSES),
                )
                .order_by(Billing.semester.asc(), Billing.id.asc())
            ).all()
        )

    def has_unpaid_billing(self, db: Session, nim: str) -> bool:
        return len(self.get_student_arrears(db, nim)) > 0

    def get_billing(self, db: Session, billing_id: int) -> Billing:
        billing = db.scalars(
            select(Billing)
            .options(
                selectinload(Billing.mahasiswa),
                selectinload(Billing.payments).selectinload(Payment.payment_method),
            )
            .where(Billing.id == billing_id)
        ).one_or_none()
        if billing is None:
            raise FinanceNotFoundError(
                f"Tagihan SPP dengan ID {billing_id} tidak ditemukan"
            )
        return billing

    def list_billings(
        self,
        db: Session,
        *,
        nim: str | None = None,
        semester: str | None = None,
        status: str | None = None,
    ) -> list[Billing]:
        stmt = (
            select(Billing)
            .options(
                selectinload(Billing.mahasiswa),
                selectinload(Billing.payments).selectinload(Payment.payment_method),
            )
            .order_by(Billing.semester.desc(), Billing.id.asc())
        )
        if nim:
            stmt = stmt.where(Billing.nim == nim)
        if semester:
            stmt = stmt.where(Billing.semester == semester)
        if status:
            try:
                status_enum = BillingStatus(status.strip().lower())
            except ValueError as exc:
                raise FinanceValidationError(
                    f"Status billing '{status}' tidak valid "
                    "(gunakan: unpaid, partial, paid, overdue)"
                ) from exc
            stmt = stmt.where(Billing.status == status_enum)
        return list(db.scalars(stmt).all())

    def list_payments(
        self,
        db: Session,
        *,
        billing_id: int | None = None,
        nim: str | None = None,
    ) -> list[Payment]:
        stmt = (
            select(Payment)
            .join(Billing, Billing.id == Payment.billing_id)
            .options(
                selectinload(Payment.payment_method),
                selectinload(Payment.billing),
            )
            .order_by(Payment.paid_at.desc(), Payment.id.desc())
        )
        if billing_id is not None:
            stmt = stmt.where(Payment.billing_id == billing_id)
        if nim:
            stmt = stmt.where(Billing.nim == nim)
        return list(db.scalars(stmt).all())

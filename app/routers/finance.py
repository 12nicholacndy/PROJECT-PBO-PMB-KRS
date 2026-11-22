from datetime import date
import json

from fastapi import APIRouter, Body, Depends, Header, Query, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Billing, Payment
from app.schemas import (
    AIFinancialReportRequest,
    AIFinancialReportResponse,
    BillingItemResponse,
    EmailLogResponse,
    FinanceDashboardResponse,
    GenerateBillingRequest,
    GenerateBillingResponse,
    LateFeeCheckRequest,
    LateFeeCheckResponse,
    ManualPaymentRequest,
    PaymentMethodResponse,
    PaymentProcessResponse,
    PaymentRecordResponse,
    ReminderRequest,
    ReminderRunResponse,
    SchedulerStatusResponse,
    WebhookPaymentResponse,
    WebhookSignRequest,
    WebhookSignResponse,
)
from app.services.ai_financial_report import (
    generate_financial_insights_from_csv,
    generate_financial_insights_from_db,
)
from app.services.billing_scheduler import get_billing_scheduler
from app.services.billing_service import (
    BillingService,
    compute_webhook_signature,
    get_email_simulator,
)
from app.services.finance_dashboard import get_finance_dashboard

router = APIRouter(prefix="/api/finance", tags=["Keuangan (SPP)"])

_service = BillingService()


def _to_payment_response(payment: Payment) -> PaymentRecordResponse:
    return PaymentRecordResponse(
        id=payment.id,
        billing_id=payment.billing_id,
        transaction_id=payment.transaction_id,
        amount=payment.amount,
        status=payment.status.value,
        source=payment.source,
        method_code=payment.payment_method.code if payment.payment_method else "-",
        method_name=payment.payment_method.name if payment.payment_method else "-",
        paid_at=payment.paid_at,
        receipt_sent=payment.receipt_sent,
        receipt_email=payment.receipt_email,
        notes=payment.notes,
    )


def _to_billing_response(billing: Billing) -> BillingItemResponse:
    payments = sorted(
        billing.payments,
        key=lambda p: (p.paid_at, p.id),
    )
    return BillingItemResponse(
        id=billing.id,
        nim=billing.nim,
        nama_mahasiswa=(
            billing.mahasiswa.nama_lengkap if billing.mahasiswa is not None else None
        ),
        semester=billing.semester,
        amount=billing.amount,
        late_fee=billing.late_fee,
        total=billing.total,
        paid_amount=billing.paid_amount,
        remaining_amount=billing.remaining_amount,
        due_date=billing.due_date,
        status=billing.status.value,
        last_reminded_at=billing.last_reminded_at,
        paid_at=billing.paid_at,
        created_at=billing.created_at,
        updated_at=billing.updated_at,
        payments=[_to_payment_response(p) for p in payments],
    )


@router.get(
    "/payment-methods",
    response_model=list[PaymentMethodResponse],
    summary="Daftar metode pembayaran SPP yang tersedia",
)
def list_payment_methods(db: Session = Depends(get_db)):
    return _service.list_payment_methods(db)


@router.post(
    "/billing/generate",
    response_model=GenerateBillingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate tagihan SPP awal semester untuk semua mahasiswa aktif (idempoten)",
)
def generate_semester_billing(
    payload: GenerateBillingRequest,
    db: Session = Depends(get_db),
):
    result = _service.generate_billing(
        db,
        semester=payload.semester,
        semester_start=payload.semester_start,
    )
    loaded = [_service.get_billing(db, b.id) for b in result["billings"]]
    return GenerateBillingResponse(
        semester=result["semester"],
        semester_start=result["semester_start"],
        due_date=result["due_date"],
        created_count=result["created_count"],
        skipped_count=result["skipped_count"],
        total_active_students=result["total_active_students"],
        billings=[_to_billing_response(b) for b in loaded],
    )


@router.get(
    "/billing",
    response_model=list[BillingItemResponse],
    summary="Daftar tagihan SPP (filter opsional berdasarkan NIM, semester, dan status)",
)
def list_billings(
    nim: str | None = Query(default=None),
    semester: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
):
    billings = _service.list_billings(
        db, nim=nim, semester=semester, status=status_filter
    )
    return [_to_billing_response(b) for b in billings]


@router.get(
    "/billing/{billing_id}",
    response_model=BillingItemResponse,
    summary="Detail satu tagihan SPP beserta histori pembayarannya",
)
def get_billing_detail(billing_id: int, db: Session = Depends(get_db)):
    return _to_billing_response(_service.get_billing(db, billing_id))


@router.post(
    "/billing/apply-late-fees",
    response_model=LateFeeCheckResponse,
    summary="Hitung & terapkan denda keterlambatan serta ubah status tagihan jatuh tempo ke overdue",
)
def apply_late_fees(
    payload: LateFeeCheckRequest = Body(default_factory=LateFeeCheckRequest),
    db: Session = Depends(get_db),
):
    result = _service.calculate_and_apply_late_fees(db, as_of=payload.as_of)
    return LateFeeCheckResponse(**result)


@router.post(
    "/billing/send-reminders",
    response_model=ReminderRunResponse,
    summary="Jalankan pengiriman reminder otomatis ke mahasiswa yang memiliki tunggakan overdue",
)
def send_reminders(
    payload: ReminderRequest = Body(default_factory=ReminderRequest),
    db: Session = Depends(get_db),
):
    result = _service.send_overdue_reminders(
        db,
        as_of=payload.as_of,
        min_days_overdue=payload.min_days_overdue,
    )
    return ReminderRunResponse(**result)


@router.post(
    "/payments",
    response_model=PaymentProcessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Catat pembayaran SPP manual secara atomik (partial/paid + kirim kwitansi email)",
)
def record_manual_payment(
    payload: ManualPaymentRequest,
    db: Session = Depends(get_db),
):
    payment, billing, duplicate = _service.process_payment(
        db,
        billing_id=payload.billing_id,
        amount=payload.amount,
        method=payload.method,
        transaction_id=payload.transaction_id,
        source="manual",
        notes=payload.notes,
        send_receipt=True,
    )
    return PaymentProcessResponse(
        duplicate=duplicate,
        payment=_to_payment_response(payment),
        billing=_to_billing_response(billing),
    )


@router.get(
    "/payments",
    response_model=list[PaymentRecordResponse],
    summary="Daftar transaksi pembayaran SPP",
)
def list_payments(
    billing_id: int | None = Query(default=None),
    nim: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return [
        _to_payment_response(p)
        for p in _service.list_payments(db, billing_id=billing_id, nim=nim)
    ]


@router.post(
    "/webhook/payment",
    response_model=WebhookPaymentResponse,
    status_code=status.HTTP_200_OK,
    summary="Endpoint Webhook Payment Gateway (verifikasi HMAC-SHA256, update billing, kirim email receipt)",
)
async def payment_gateway_webhook(
    request: Request,
    x_webhook_signature: str | None = Header(default=None, alias="X-Webhook-Signature"),
    db: Session = Depends(get_db),
):
    raw_body = await request.body()
    result = _service.handle_webhook(
        db,
        raw_body=raw_body,
        signature=x_webhook_signature,
    )
    return WebhookPaymentResponse(**result)


@router.post(
    "/webhook/sign",
    response_model=WebhookSignResponse,
    summary="Helper simulasi untuk membuat signature HMAC-SHA256 dan contoh perintah curl webhook",
)
def sign_webhook_payload(payload: WebhookSignRequest):
    body_str = json.dumps(payload.model_dump(), separators=(",", ":"))
    signature = compute_webhook_signature(body_str.encode("utf-8"))
    curl_cmd = (
        f'curl -X POST "http://127.0.0.1:8000/api/finance/webhook/payment" '
        f'-H "Content-Type: application/json" '
        f'-H "X-Webhook-Signature: sha256={signature}" '
        f"-d '{body_str}'"
    )
    return WebhookSignResponse(
        payload_json=body_str,
        signature=f"sha256={signature}",
        header_name="X-Webhook-Signature",
        curl_example=curl_cmd,
    )


@router.get(
    "/dashboard",
    response_model=FinanceDashboardResponse,
    summary="Data real-time Dashboard Keuangan (per mahasiswa, collection rate per prodi, grafik bulanan)",
)
def finance_dashboard_data(
    semester: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return FinanceDashboardResponse(**get_finance_dashboard(db, semester=semester))


@router.get(
    "/ai-insights",
    response_model=AIFinancialReportResponse,
    summary="AI Financial Report & Insights otomatis dalam format JSON dari database",
)
def ai_financial_insights_db(
    reference_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return AIFinancialReportResponse(
        **generate_financial_insights_from_db(db, reference_date=reference_date)
    )


@router.post(
    "/ai-insights/csv",
    response_model=AIFinancialReportResponse,
    summary="AI Financial Report & Insights dari input CSV (kolom: nim, jumlah, tanggal, status)",
)
def ai_financial_insights_csv(payload: AIFinancialReportRequest):
    return AIFinancialReportResponse(
        **generate_financial_insights_from_csv(
            payload.csv_data,
            reference_date=payload.reference_date,
        )
    )


@router.get(
    "/scheduler/status",
    response_model=SchedulerStatusResponse,
    summary="Status cron job APScheduler untuk billing otomatis semester & reminder harian",
)
def scheduler_status():
    return SchedulerStatusResponse(**get_billing_scheduler().get_status())


@router.get(
    "/emails",
    response_model=list[EmailLogResponse],
    summary="Log pengiriman email kwitansi & reminder simulasi",
)
def list_simulated_emails():
    return [
        EmailLogResponse(
            to_email=rec.to_email,
            subject=rec.subject,
            body=rec.body,
            category=rec.category,
            sent_at=rec.sent_at,
        )
        for rec in reversed(get_email_simulator().history())
    ]

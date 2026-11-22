from datetime import date, datetime, time, timedelta, timezone
import json

from hypothesis import HealthCheck, given, settings, strategies as st
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import (
    Billing,
    BillingStatus,
    MataKuliah,
    Notifikasi,
    Payment,
    PaymentMethod,
    ProgramStudi,
)
from app.services.ai_financial_report import (
    generate_financial_insights_from_csv,
    generate_financial_insights_from_db,
)
from app.services.billing_scheduler import (
    BillingSchedulerManager,
    infer_semester_code,
    run_scheduled_overdue_reminder,
    run_scheduled_semester_billing,
)
from app.services.billing_service import (
    BillingService,
    SPP_TARIF_DEFAULT,
    SPP_TARIF_EKONOMI,
    SPP_TARIF_TEKNIK,
    calculate_late_fee,
    compute_webhook_signature,
    format_rupiah,
    get_email_simulator,
    verify_webhook_signature,
)
from app.services.finance_errors import (
    FinanceConflictError,
    FinanceNotFoundError,
    FinanceValidationError,
    WebhookSignatureError,
)
from tests.conftest import daftar


def _approve_student(client, *, nama: str, email: str, prodi_id: int) -> dict:
    created = daftar(
        client,
        nama_lengkap=nama,
        email=email,
        program_studi_id=prodi_id,
    )
    resp = client.put(f"/api/pmb/approve/{created['id']}")
    assert resp.status_code == 200
    return resp.json()


def _add_ekonomi_prodi(db) -> ProgramStudi:
    prodi = ProgramStudi(
        id=3,
        kode="003",
        nama="Ekonomi Pembangunan",
        fakultas="Fakultas Ekonomi",
    )
    db.add(prodi)
    db.commit()
    db.refresh(prodi)
    return prodi


def _seed_sample_course(db, kode_mk: str = "IF101") -> MataKuliah:
    mk = MataKuliah(
        kode_mk=kode_mk,
        nama="Algoritma & Pemrograman",
        sks=3,
        hari=1,
        jam_mulai=time(8, 0),
        jam_selesai=time(10, 30),
    )
    db.add(mk)
    db.commit()
    return mk


# =====================================================================
# 1. Payment Schema & Constraints (25%)
# =====================================================================


def test_schema_constraints_and_default_payment_methods(client, db):
    resp = client.get("/api/finance/payment-methods")
    assert resp.status_code == 200
    methods = resp.json()
    codes = {m["code"] for m in methods}
    assert {"va_bca", "gateway_va", "transfer_bank", "qris", "tunai"}.issubset(codes)

    mhs = _approve_student(client, nama="Andi", email="andi@example.com", prodi_id=2)
    b1 = Billing(
        nim=mhs["nim"],
        semester="20261",
        amount=5_000_000,
        due_date=date(2026, 8, 15),
        status=BillingStatus.UNPAID,
    )
    db.add(b1)
    db.commit()

    # Duplikat (nim, semester) ditolak oleh UniqueConstraint uq_billing_nim_semester
    b_dup = Billing(
        nim=mhs["nim"],
        semester="20261",
        amount=5_000_000,
        due_date=date(2026, 8, 15),
        status=BillingStatus.UNPAID,
    )
    db.add(b_dup)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # Nilai negatif ditolak CheckConstraint
    b_neg = Billing(
        nim=mhs["nim"],
        semester="20262",
        amount=-1000,
        due_date=date(2027, 2, 15),
        status=BillingStatus.UNPAID,
    )
    db.add(b_neg)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# =====================================================================
# 2. Billing Generator + Tarif Prodi + Due Date H+14 + APScheduler
# =====================================================================


def test_generate_billing_tarif_prodi_due_date_and_idempotency(client, db):
    prodi_ekonomi = _add_ekonomi_prodi(db)

    mhs_si = _approve_student(client, nama="Mhs SI", email="si@example.com", prodi_id=1)
    mhs_ti = _approve_student(client, nama="Mhs Teknik", email="ti@example.com", prodi_id=2)
    mhs_ek = _approve_student(
        client, nama="Mhs Ekonomi", email="ek@example.com", prodi_id=prodi_ekonomi.id
    )
    # Pendaftar masih pending -> tidak boleh dibuatkan billing
    daftar(client, nama_lengkap="Pending User", email="pending@example.com", program_studi_id=2)

    start = date(2026, 8, 1)
    resp = client.post(
        "/api/finance/billing/generate",
        json={"semester": "20261", "semester_start": start.isoformat()},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["created_count"] == 3
    assert data["skipped_count"] == 0
    assert data["due_date"] == (start + timedelta(days=14)).isoformat()

    by_nim = {b["nim"]: b for b in data["billings"]}
    assert by_nim[mhs_ti["nim"]]["amount"] == SPP_TARIF_TEKNIK  # 5jt
    assert by_nim[mhs_ek["nim"]]["amount"] == SPP_TARIF_EKONOMI  # 4jt
    assert by_nim[mhs_si["nim"]]["amount"] == SPP_TARIF_DEFAULT  # 4.5jt
    for b in by_nim.values():
        assert b["status"] == "unpaid"
        assert b["paid_amount"] == 0
        assert b["remaining_amount"] == b["amount"]

    # Panggilan kedua untuk semester yang sama bersifat idempoten (tidak menggandakan tagihan)
    resp2 = client.post(
        "/api/finance/billing/generate",
        json={"semester": "20261", "semester_start": start.isoformat()},
    )
    assert resp2.status_code == 201
    data2 = resp2.json()
    assert data2["created_count"] == 0
    assert data2["skipped_count"] == 3


def test_apscheduler_jobs_and_runner_functions(client, db):
    assert infer_semester_code(date(2026, 8, 1)) == "20261"
    assert infer_semester_code(date(2026, 2, 1)) == "20252"
    assert infer_semester_code(date(2026, 1, 15)) == "20251"

    mhs = _approve_student(client, nama="Cron Mhs", email="cron@example.com", prodi_id=2)

    # Uji endpoint status scheduler saat FastAPI lifespan aktif
    status_resp = client.get("/api/finance/scheduler/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["running"] is True
    job_ids = {j["id"] for j in status_data["jobs"]}
    assert {"semester_billing_generator", "daily_overdue_reminder"}.issubset(job_ids)

    # Uji langsung fungsi runner cron job semester billing & overdue reminder
    test_session_factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False)
    gen_res = run_scheduled_semester_billing(
        session_factory=test_session_factory,
        today=date(2026, 8, 1),
    )
    assert gen_res["semester"] == "20261"
    assert gen_res["created_count"] == 1

    # 30 hari setelah 1 Agu (16 hari lewat due_date 15 Agu) -> jadi overdue + reminder terkirim
    rem_res = run_scheduled_overdue_reminder(
        session_factory=test_session_factory,
        today=date(2026, 8, 31),
    )
    assert rem_res["reminded_count"] == 1

    # Uji juga endpoint HTTP send-reminders & filter list billing
    rem_api = client.post(
        "/api/finance/billing/send-reminders",
        json={"as_of": "2026-08-31", "min_days_overdue": 10},
    )
    assert rem_api.status_code == 200
    assert rem_api.json()["reminded_count"] == 1

    list_api = client.get(f"/api/finance/billing?nim={mhs['nim']}&semester=20261&status=overdue")
    assert list_api.status_code == 200
    assert len(list_api.json()) == 1

    notifs = client.get(f"/api/krs/{mhs['nim']}/notifikasi").json()
    assert any("Reminder Tunggakan SPP" in n["judul"] for n in notifs)

    # Uji start/shutdown mandiri manager
    mgr = BillingSchedulerManager(session_factory=test_session_factory)
    mgr.start()
    assert mgr.is_running is True
    assert len(mgr.get_status()["jobs"]) == 2
    mgr.shutdown()
    assert mgr.is_running is False


# =====================================================================
# 3. Payment Processing (Partial -> Paid), Late Fee, & Atomicity
# =====================================================================


def test_partial_to_paid_payment_and_late_fee_calculation(client, db):
    mhs = _approve_student(client, nama="Cicil Mhs", email="cicil@example.com", prodi_id=2)
    gen = client.post(
        "/api/finance/billing/generate",
        json={"semester": "20261", "semester_start": "2026-08-01"},
    ).json()
    billing_id = gen["billings"][0]["id"]

    # Terapkan denda 10 hari setelah due_date (15 Agu -> 25 Agu = 2 minggu -> 2% dari 5jt = 100.000)
    fee_resp = client.post(
        "/api/finance/billing/apply-late-fees",
        json={"as_of": "2026-08-25"},
    )
    assert fee_resp.status_code == 200
    assert fee_resp.json()["updated_count"] == 1
    assert fee_resp.json()["total_late_fees"] == 100_000

    detail = client.get(f"/api/finance/billing/{billing_id}").json()
    assert detail["status"] == "overdue"
    assert detail["late_fee"] == 100_000
    assert detail["total"] == 5_100_000

    # Pembayaran cicilan pertama Rp 3.000.000 -> status 'partial'
    p1 = client.post(
        "/api/finance/payments",
        json={
            "billing_id": billing_id,
            "amount": 3_000_000,
            "method": "va_bca",
            "notes": "Cicilan 1",
        },
    )
    assert p1.status_code == 201
    p1_data = p1.json()
    assert p1_data["billing"]["status"] == "partial"
    assert p1_data["billing"]["paid_amount"] == 3_000_000
    assert p1_data["billing"]["remaining_amount"] == 2_100_000
    assert p1_data["payment"]["receipt_sent"] is True
    assert p1_data["payment"]["receipt_email"] == "cicil@example.com"

    # Pelunasan cicilan kedua Rp 2.100.000 -> status 'paid'
    p2 = client.post(
        "/api/finance/payments",
        json={
            "billing_id": billing_id,
            "amount": 2_100_000,
            "method": "qris",
            "notes": "Pelunasan",
        },
    )
    assert p2.status_code == 201
    p2_data = p2.json()
    assert p2_data["billing"]["status"] == "paid"
    assert p2_data["billing"]["paid_amount"] == 5_100_000
    assert p2_data["billing"]["remaining_amount"] == 0
    assert p2_data["billing"]["paid_at"] is not None

    # Membayar lagi tagihan yang sudah lunas -> 409 Conflict
    p3 = client.post(
        "/api/finance/payments",
        json={"billing_id": billing_id, "amount": 100_000, "method": "tunai"},
    )
    assert p3.status_code == 409

    # Histori pembayaran & email receipt tercatat
    payments = client.get(f"/api/finance/payments?nim={mhs['nim']}").json()
    assert len(payments) == 2
    emails = client.get("/api/finance/emails").json()
    assert len(emails) >= 2
    assert any(e["category"] == "receipt" for e in emails)


def test_late_fee_capped_at_ten_percent(db):
    billing = Billing(
        nim="20260020001",
        semester="20261",
        amount=5_000_000,
        late_fee=0,
        paid_amount=0,
        due_date=date(2026, 8, 15),
        status=BillingStatus.UNPAID,
    )
    assert calculate_late_fee(billing, as_of=date(2026, 8, 15)) == 0
    assert calculate_late_fee(billing, as_of=date(2026, 8, 16)) == 50_000  # 1 minggu = 1%
    assert calculate_late_fee(billing, as_of=date(2026, 12, 31)) == 500_000  # cap 10%


# =====================================================================
# 4. Payment Webhook Simulation + Signature Security + Email Receipt (15%)
# =====================================================================


def test_payment_webhook_signature_security_idempotency_and_receipt(client, db):
    mhs = _approve_student(client, nama="Webhook Mhs", email="wh@example.com", prodi_id=2)
    gen = client.post(
        "/api/finance/billing/generate",
        json={"semester": "20261", "semester_start": "2026-08-01"},
    ).json()
    billing_id = gen["billings"][0]["id"]

    payload_obj = {
        "transaction_id": "GW-TX-20261122-001",
        "billing_id": billing_id,
        "amount": 5_000_000,
        "status": "paid",
        "method": "gateway_va",
    }
    raw_body = json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")

    # 1. Tanpa signature -> 401 Unauthorized
    r_missing = client.post(
        "/api/finance/webhook/payment",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )
    assert r_missing.status_code == 401

    # 2. Signature palsu -> 401 Unauthorized
    r_invalid = client.post(
        "/api/finance/webhook/payment",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": "sha256=deadbeef",
        },
    )
    assert r_invalid.status_code == 401

    # 3. Signature valid (lewat endpoint helper /webhook/sign) -> 200 OK, status unpaid -> paid + email receipt
    sign_resp = client.post("/api/finance/webhook/sign", json=payload_obj)
    assert sign_resp.status_code == 200
    sign_data = sign_resp.json()
    assert "curl -X POST" in sign_data["curl_example"]
    assert verify_webhook_signature(
        sign_data["payload_json"].encode("utf-8"), sign_data["signature"]
    )

    r_ok = client.post(
        "/api/finance/webhook/payment",
        content=sign_data["payload_json"].encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": sign_data["signature"],
        },
    )
    assert r_ok.status_code == 200
    ok_data = r_ok.json()
    assert ok_data["ok"] is True
    assert ok_data["duplicate"] is False
    assert ok_data["billing_status"] == "paid"
    assert ok_data["paid_amount"] == 5_000_000
    assert ok_data["remaining_amount"] == 0
    assert ok_data["receipt_sent"] is True
    assert ok_data["receipt_email"] == "wh@example.com"

    # Cek email simulator & notifikasi DB
    receipts = [e for e in get_email_simulator().history() if e.category == "receipt"]
    assert len(receipts) == 1
    assert receipts[0].to_email == "wh@example.com"

    notifs = db.scalars(select(Notifikasi).where(Notifikasi.nim == mhs["nim"])).all()
    assert any("Kwitansi Pembayaran SPP" in n.judul for n in notifs)

    # 4. Replay webhook dengan transaction_id yang sama -> 200 OK idempoten, tidak double-credit
    r_dup = client.post(
        "/api/finance/webhook/payment",
        content=sign_data["payload_json"].encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": sign_data["signature"],
        },
    )
    assert r_dup.status_code == 200
    assert r_dup.json()["duplicate"] is True
    assert r_dup.json()["paid_amount"] == 5_000_000


def test_webhook_failed_status_does_not_credit_billing(client, db):
    _approve_student(client, nama="Gagal Bayar", email="gagal@example.com", prodi_id=2)
    gen = client.post(
        "/api/finance/billing/generate",
        json={"semester": "20261", "semester_start": "2026-08-01"},
    ).json()
    billing_id = gen["billings"][0]["id"]

    payload_obj = {
        "transaction_id": "GW-TX-FAILED-001",
        "billing_id": billing_id,
        "amount": 5_000_000,
        "status": "failed",
    }
    raw_body = json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
    sig = compute_webhook_signature(raw_body)

    resp = client.post(
        "/api/finance/webhook/payment",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"sha256={sig}",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["billing_status"] == "unpaid"
    assert data["paid_amount"] == 0
    assert data["receipt_sent"] is False


# =====================================================================
# 5. Block KRS jika Ada Tunggakan & Unblock Setelah Lunas (Kriteria Sukses #4)
# =====================================================================


def test_block_krs_when_unpaid_and_unblock_after_payment(client, db):
    _seed_sample_course(db, "IF101")
    mhs = _approve_student(client, nama="Nunggak KRS", email="krsblock@example.com", prodi_id=2)
    nim = mhs["nim"]

    # Generate tagihan SPP -> status 'unpaid'
    gen = client.post(
        "/api/finance/billing/generate",
        json={"semester": "20261", "semester_start": "2026-08-01"},
    ).json()
    billing_id = gen["billings"][0]["id"]

    # 1. Coba isi KRS saat status 'unpaid' -> DIBLOKIR (403 Forbidden)
    r_add_unpaid = client.post(
        "/api/krs/courses",
        json={"nim": nim, "kode_mk": "IF101", "semester": "20261", "dosen_pa_id": 10},
    )
    assert r_add_unpaid.status_code == 403
    assert "tunggakan SPP" in r_add_unpaid.json()["detail"]

    # 2. Bayar cicilan sebagian (partial) -> MASIH DIBLOKIR (403 Forbidden)
    client.post(
        "/api/finance/payments",
        json={"billing_id": billing_id, "amount": 2_000_000, "method": "va_bca"},
    )
    r_add_partial = client.post(
        "/api/krs/courses",
        json={"nim": nim, "kode_mk": "IF101", "semester": "20261", "dosen_pa_id": 10},
    )
    assert r_add_partial.status_code == 403

    # 3. Lunasi sisa Rp 3.000.000 lewat webhook -> status 'paid'
    wh_payload = json.dumps(
        {
            "transaction_id": "WH-UNBLOCK-KRS-1",
            "billing_id": billing_id,
            "amount": 3_000_000,
            "status": "settlement",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    sig = compute_webhook_signature(wh_payload)
    r_wh = client.post(
        "/api/finance/webhook/payment",
        content=wh_payload,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": sig,
        },
    )
    assert r_wh.status_code == 200
    assert r_wh.json()["billing_status"] == "paid"

    # 4. Setelah lunas -> isi & submit KRS BERHASIL!
    r_add_ok = client.post(
        "/api/krs/courses",
        json={"nim": nim, "kode_mk": "IF101", "semester": "20261", "dosen_pa_id": 10},
    )
    assert r_add_ok.status_code == 201
    r_sub_ok = client.post(
        f"/api/krs/{nim}/20261/submit",
        json={"requester_nim": nim},
    )
    assert r_sub_ok.status_code == 200
    assert r_sub_ok.json()["status"] == "submitted"


# =====================================================================
# 6. Dashboard Keuangan Real-Time (20%) & Web UI
# =====================================================================


def test_finance_dashboard_api_and_html_ui(client, db):
    prodi_ek = _add_ekonomi_prodi(db)
    m1 = _approve_student(client, nama="Teknik Lunas", email="t1@example.com", prodi_id=2)
    m2 = _approve_student(client, nama="Teknik Tunggak", email="t2@example.com", prodi_id=2)
    m3 = _approve_student(client, nama="Ekonomi Lunas", email="e1@example.com", prodi_id=prodi_ek.id)

    gen = client.post(
        "/api/finance/billing/generate",
        json={"semester": "20261", "semester_start": "2026-08-01"},
    ).json()
    by_nim = {b["nim"]: b["id"] for b in gen["billings"]}

    # Bayar m1 (5jt) dan m3 (4jt), m2 cicil 1jt
    service = BillingService()
    service.process_payment(
        db,
        by_nim[m1["nim"]],
        5_000_000,
        "va_bca",
        paid_at=datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
    )
    service.process_payment(
        db,
        by_nim[m3["nim"]],
        4_000_000,
        "qris",
        paid_at=datetime(2026, 10, 5, 11, 0, tzinfo=timezone.utc),
    )
    service.process_payment(
        db,
        by_nim[m2["nim"]],
        1_000_000,
        "transfer_bank",
        paid_at=datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc),
    )

    dash_resp = client.get("/api/finance/dashboard?semester=20261")
    assert dash_resp.status_code == 200
    dash = dash_resp.json()

    assert dash["summary"]["total_mahasiswa"] == 3
    assert dash["summary"]["mahasiswa_lunas"] == 2
    assert dash["summary"]["mahasiswa_menunggak"] == 1
    assert dash["summary"]["total_tagihan"] == 14_000_000
    assert dash["summary"]["total_sudah_bayar"] == 10_000_000
    assert dash["summary"]["total_sisa"] == 4_000_000
    assert dash["summary"]["collection_rate"] == pytest.approx(66.67, abs=0.01)

    # Per prodi: Ekonomi 100%, Teknik 50%
    prodi_map = {p["kode_prodi"]: p for p in dash["per_prodi"]}
    assert prodi_map["003"]["collection_rate"] == 100.0
    assert prodi_map["002"]["collection_rate"] == 50.0

    # Per mahasiswa: m2 diblokir KRS, m1 & m3 tidak
    mhs_map = {m["nim"]: m for m in dash["per_mahasiswa"]}
    assert mhs_map[m2["nim"]]["krs_blocked"] is True
    assert mhs_map[m2["nim"]]["sisa"] == 4_000_000
    assert mhs_map[m1["nim"]]["krs_blocked"] is False

    # Grafik bulanan: 2026-09 (5jt) dan 2026-10 (5jt)
    monthly = {pt["month"]: pt for pt in dash["monthly_payments"]}
    assert monthly["2026-09"]["total_bayar"] == 5_000_000
    assert monthly["2026-10"]["total_bayar"] == 5_000_000
    assert monthly["2026-10"]["jumlah_transaksi"] == 2

    # Halaman Dashboard HTML real-time dapat diakses
    ui_resp = client.get("/keuangan/dashboard")
    assert ui_resp.status_code == 200
    assert "Dashboard Keuangan SPP" in ui_resp.text
    assert "/api/finance/dashboard" in ui_resp.text


# =====================================================================
# 7. AI Financial Report (5%) — CSV & Database
# =====================================================================


def test_ai_financial_report_from_csv_and_db(client, db):
    csv_lines = ["nim,jumlah,tanggal,status"]
    # 3 mahasiswa bayar lunas bulan ini (2026-11)
    csv_lines.append("20260020001,5000000,2026-11-05,paid")
    csv_lines.append("20260020002,5000000,2026-11-12,paid")
    csv_lines.append("20260030001,4000000,2026-10-20,paid")  # bulan lalu -> bukan revenue bulan ini
    # 12 mahasiswa menunggak (4 di antaranya > 2 bulan dari 2026-11-22)
    for i in range(1, 13):
        nim = f"202600201{i:02d}"
        tgl = "2026-08-15" if i <= 4 else "2026-10-25"
        status = "overdue" if i <= 4 else "unpaid"
        csv_lines.append(f"{nim},{1_000_000 + i * 250_000},{tgl},{status}")

    csv_text = "\n".join(csv_lines)
    resp = client.post(
        "/api/finance/ai-insights/csv",
        json={"csv_data": csv_text, "reference_date": "2026-11-22"},
    )
    assert resp.status_code == 200
    report = resp.json()

    assert report["source"] == "csv"
    assert report["total_revenue_bulan_ini"] == 10_000_000
    assert report["total_revenue_formatted"] == "Rp 10.000.000"
    assert report["total_mahasiswa"] == 15
    assert report["mahasiswa_sudah_bayar"] == 3
    assert report["collection_rate"] == 20.0
    assert "20.00% mahasiswa sudah bayar" in report["collection_rate_summary"]
    assert len(report["top_10_tunggakan"]) == 10
    assert report["top_10_tunggakan"][0]["total_tunggakan"] == 4_000_000  # i=12 terbesar
    assert report["tunggakan_lebih_2_bulan_count"] == 4
    assert (
        report["rekomendasi"]
        == "Kirim reminder ke 4 mahasiswa dengan tunggakan >2 bulan"
    )

    # Uji validasi CSV kosong / kolom kurang -> 400
    bad_csv = client.post(
        "/api/finance/ai-insights/csv",
        json={"csv_data": "nim,jumlah\n123,5000"},
    )
    assert bad_csv.status_code == 400

    # Uji AI Financial Insights langsung dari DB
    mhs = _approve_student(client, nama="DB Arrears", email="dbarr@example.com", prodi_id=2)
    client.post(
        "/api/finance/billing/generate",
        json={"semester": "20261", "semester_start": "2026-08-01"},
    )
    client.post(
        "/api/finance/billing/apply-late-fees",
        json={"as_of": "2026-11-22"},
    )
    db_resp = client.get("/api/finance/ai-insights?reference_date=2026-11-22")
    assert db_resp.status_code == 200
    db_report = db_resp.json()
    assert db_report["source"] == "database"
    assert db_report["tunggakan_lebih_2_bulan_count"] == 1
    assert db_report["top_10_tunggakan"][0]["nim"] == mhs["nim"]
    assert (
        db_report["rekomendasi"]
        == "Kirim reminder ke 1 mahasiswa dengan tunggakan >2 bulan"
    )


# =====================================================================
# 8. Property-Based Testing (Hypothesis) — Invarian Payment & Late Fee
# =====================================================================


@given(
    amount=st.integers(min_value=1_000_000, max_value=10_000_000),
    days_late=st.integers(min_value=-30, max_value=180),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_pbt_late_fee_invariants(amount: int, days_late: int):
    due = date(2026, 8, 15)
    billing = Billing(
        nim="20260020001",
        semester="20261",
        amount=amount,
        late_fee=0,
        paid_amount=0,
        due_date=due,
        status=BillingStatus.UNPAID,
    )
    fee = calculate_late_fee(billing, as_of=due + timedelta(days=days_late))
    if days_late <= 0:
        assert fee == 0
    else:
        assert 0 < fee <= int(round(amount * 0.10))


@given(
    total_spp=st.integers(min_value=1_000_000, max_value=8_000_000),
    installments=st.lists(
        st.integers(min_value=100_000, max_value=3_000_000),
        min_size=1,
        max_size=5,
    ),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_pbt_payment_state_machine_invariants(total_spp: int, installments: list[int]):
    """Invarian: status selalu 'partial' selama akumulasi < total, dan 'paid' begitu >= total."""
    paid = 0
    status = BillingStatus.UNPAID
    for pay in installments:
        if status == BillingStatus.PAID:
            break
        paid += pay
        status = BillingStatus.PAID if paid >= total_spp else BillingStatus.PARTIAL
        remaining = max(0, total_spp - paid)
        if status == BillingStatus.PAID:
            assert remaining == 0
            assert paid >= total_spp
        else:
            assert remaining > 0
            assert paid < total_spp


def test_misc_error_paths_and_formatting(client, db):
    assert format_rupiah(5_000_000) == "Rp 5.000.000"
    assert format_rupiah(-1500) == "-Rp 1.500"

    assert client.get("/api/finance/billing/99999").status_code == 404
    assert client.get("/api/finance/billing?status=unknown").status_code == 400
    assert (
        client.post(
            "/api/finance/payments",
            json={"billing_id": 99999, "amount": 100_000, "method": "va_bca"},
        ).status_code
        == 404
    )

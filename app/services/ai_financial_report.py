from __future__ import annotations

from collections import defaultdict
import csv
from datetime import date, datetime, timezone
import io
import json
import os
from typing import Any
import urllib.error
import urllib.request

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Billing, BillingStatus, CalonMahasiswa, Payment, PaymentStatus
from app.services.billing_service import UNPAID_BILLING_STATUSES, format_rupiah
from app.services.finance_errors import FinanceValidationError

OVERDUE_REMINDER_THRESHOLD_DAYS = 60  # > 2 bulan


def _parse_date(value: str) -> date:
    raw = value.strip()
    if not raw:
        raise FinanceValidationError("Kolom 'tanggal' pada CSV tidak boleh kosong")
    # Dukung YYYY-MM-DD dan ISO datetime YYYY-MM-DDTHH:MM:SS
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        try:
            return date.fromisoformat(raw[:10])
        except ValueError as exc:
            raise FinanceValidationError(
                f"Format tanggal '{raw}' tidak valid (gunakan YYYY-MM-DD)"
            ) from exc
    for fmt in ("%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise FinanceValidationError(
        f"Format tanggal '{raw}' tidak valid (gunakan YYYY-MM-DD)"
    )


def _optional_gemini_narrative(report_facts: dict[str, Any]) -> tuple[str, str]:
    """Membuat narasi eksekutif ringkas.

    Angka keuangan tetap 100% dihitung secara deterministik agar bebas halusinasi.
    Jika GEMINI_API_KEY tersedia, opsional memperkaya kalimat narasi; jika tidak,
    menggunakan generator ringkasan offline deterministik.
    """
    fallback = (
        f"Periode {report_facts['periode']}: total penerimaan SPP bulan ini sebesar "
        f"{report_facts['total_revenue_formatted']} dengan collection rate "
        f"{report_facts['collection_rate']:.2f}% ({report_facts['mahasiswa_sudah_bayar']} "
        f"dari {report_facts['total_mahasiswa']} mahasiswa sudah melunasi). "
        f"Terdapat {len(report_facts['top_10_tunggakan'])} mahasiswa dalam daftar tunggakan "
        f"prioritas. {report_facts['rekomendasi']}."
    )

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or os.getenv("DISABLE_AI_EXTERNAL", "0") == "1":
        return fallback, "deterministic"

    prompt = (
        "Tuliskan ringkasan eksekutif keuangan kampus 2 kalimat dalam Bahasa Indonesia "
        "berdasarkan fakta JSON berikut tanpa mengubah satu pun angka:\n"
        + json.dumps(report_facts, ensure_ascii=False)
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={api_key}"
    )
    req_body = json.dumps(
        {"contents": [{"parts": [{"text": prompt}]}]}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
        )
        if text:
            return text, "gemini"
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
        pass
    return fallback, "deterministic"


def generate_financial_insights_from_csv(
    csv_text: str,
    reference_date: date | None = None,
) -> dict[str, Any]:
    """Menganalisis data pembayaran berbentuk CSV (`nim, jumlah, tanggal, status`)
    dan menghasilkan laporan ringkas JSON sesuai Step 3 Minggu 11.

    Output wajib:
    - Total revenue bulan ini: Rp X
    - Collection rate: Y% mahasiswa sudah bayar
    - Top 10 mahasiswa dengan tunggakan terbesar
    - Rekomendasi: 'Kirim reminder ke Z mahasiswa dengan tunggakan >2 bulan'
    """
    cleaned = csv_text.strip()
    if not cleaned:
        raise FinanceValidationError("Data CSV pembayaran tidak boleh kosong")

    reader = csv.DictReader(io.StringIO(cleaned))
    if not reader.fieldnames:
        raise FinanceValidationError("Header CSV tidak ditemukan")

    normalized_fields = {f.strip().lower() for f in reader.fieldnames if f}
    required_fields = {"nim", "jumlah", "tanggal", "status"}
    missing = required_fields - normalized_fields
    if missing:
        raise FinanceValidationError(
            f"Kolom wajib CSV belum lengkap: {', '.join(sorted(missing))} "
            "(kolom wajib: nim, jumlah, tanggal, status)"
        )

    ref_date = reference_date or date.today()
    current_ym = ref_date.strftime("%Y-%m")

    total_revenue_bulan_ini = 0
    all_students: set[str] = set()
    paid_students: set[str] = set()
    arrears_by_nim: dict[str, dict[str, Any]] = {}
    student_names: dict[str, str] = {}

    row_count = 0
    for idx, raw_row in enumerate(reader, start=2):
        row = {
            (k.strip().lower() if k else ""): (v.strip() if v is not None else "")
            for k, v in raw_row.items()
        }
        if not any(row.values()):
            continue
        row_count += 1

        nim = row.get("nim", "")
        if not nim:
            raise FinanceValidationError(f"Baris {idx}: kolom 'nim' kosong")

        try:
            jumlah = int(float(row.get("jumlah", "0")))
        except ValueError as exc:
            raise FinanceValidationError(
                f"Baris {idx}: kolom 'jumlah' bukan angka valid"
            ) from exc
        if jumlah < 0:
            raise FinanceValidationError(
                f"Baris {idx}: kolom 'jumlah' tidak boleh negatif"
            )

        tgl = _parse_date(row.get("tanggal", ""))
        status = row.get("status", "").lower()
        nama = row.get("nama", "") or f"Mahasiswa {nim}"
        student_names[nim] = nama
        all_students.add(nim)

        if status in ("paid", "lunas", "success", "settlement"):
            paid_students.add(nim)
            if tgl.strftime("%Y-%m") == current_ym:
                total_revenue_bulan_ini += jumlah
        elif status in ("unpaid", "partial", "overdue", "tunggakan", "belum_bayar"):
            age_days = max(0, (ref_date - tgl).days)
            if nim not in arrears_by_nim:
                arrears_by_nim[nim] = {
                    "nim": nim,
                    "nama": student_names[nim],
                    "total_tunggakan": 0,
                    "lama_tunggakan_hari": 0,
                    "status": status,
                }
            arrears_by_nim[nim]["total_tunggakan"] += jumlah
            arrears_by_nim[nim]["lama_tunggakan_hari"] = max(
                arrears_by_nim[nim]["lama_tunggakan_hari"], age_days
            )
            if status == "overdue":
                arrears_by_nim[nim]["status"] = "overdue"
        else:
            raise FinanceValidationError(
                f"Baris {idx}: status '{status}' tidak dikenali "
                "(gunakan: paid, unpaid, partial, overdue)"
            )

    if row_count == 0:
        raise FinanceValidationError("CSV tidak memiliki baris data pembayaran")

    # Jika mahasiswa punya baris tunggakan aktif, maka belum terhitung lunas penuh.
    fully_paid_students = paid_students - set(arrears_by_nim.keys())
    total_mahasiswa = len(all_students)
    collection_rate = (
        round((len(fully_paid_students) / total_mahasiswa) * 100.0, 2)
        if total_mahasiswa > 0
        else 0.0
    )

    sorted_arrears = sorted(
        arrears_by_nim.values(),
        key=lambda item: (-item["total_tunggakan"], -item["lama_tunggakan_hari"], item["nim"]),
    )
    top_10 = [
        {
            "rank": idx,
            "nim": item["nim"],
            "nama": item["nama"],
            "total_tunggakan": item["total_tunggakan"],
            "tunggakan_formatted": format_rupiah(item["total_tunggakan"]),
            "lama_tunggakan_hari": item["lama_tunggakan_hari"],
            "status": item["status"],
        }
        for idx, item in enumerate(sorted_arrears[:10], start=1)
    ]

    overdue_gt_2_months = sum(
        1
        for item in arrears_by_nim.values()
        if item["lama_tunggakan_hari"] > OVERDUE_REMINDER_THRESHOLD_DAYS
    )
    rekomendasi = (
        f"Kirim reminder ke {overdue_gt_2_months} mahasiswa dengan tunggakan >2 bulan"
    )

    facts = {
        "periode": current_ym,
        "total_revenue_bulan_ini": total_revenue_bulan_ini,
        "total_revenue_formatted": format_rupiah(total_revenue_bulan_ini),
        "collection_rate": collection_rate,
        "collection_rate_summary": f"{collection_rate:.2f}% mahasiswa sudah bayar",
        "total_mahasiswa": total_mahasiswa,
        "mahasiswa_sudah_bayar": len(fully_paid_students),
        "top_10_tunggakan": top_10,
        "tunggakan_lebih_2_bulan_count": overdue_gt_2_months,
        "rekomendasi": rekomendasi,
    }
    narasi, engine = _optional_gemini_narrative(facts)

    return {
        **facts,
        "source": "csv",
        "engine": engine,
        "generated_at": datetime.now(timezone.utc),
        "ringkasan_eksekutif": narasi,
    }


def generate_financial_insights_from_db(
    db: Session,
    reference_date: date | None = None,
) -> dict[str, Any]:
    """Menghasilkan AI Financial Insights langsung dari data tabel billing & payment."""
    ref_date = reference_date or date.today()
    current_ym = ref_date.strftime("%Y-%m")

    # 1. Total revenue bulan ini dari transaksi payment berstatus success
    payments = list(
        db.scalars(
            select(Payment).where(Payment.status == PaymentStatus.SUCCESS)
        ).all()
    )
    total_revenue_bulan_ini = sum(
        pay.amount for pay in payments if pay.paid_at.strftime("%Y-%m") == current_ym
    )

    # 2. Collection rate & daftar tunggakan dari tabel billing
    billing_rows = db.execute(
        select(Billing, CalonMahasiswa)
        .join(CalonMahasiswa, CalonMahasiswa.nim == Billing.nim)
    ).all()

    student_status_map: dict[str, list[BillingStatus]] = defaultdict(list)
    arrears_map: dict[str, dict[str, Any]] = {}

    for billing, mahasiswa in billing_rows:
        student_status_map[billing.nim].append(billing.status)
        if billing.status in UNPAID_BILLING_STATUSES and billing.remaining_amount > 0:
            days_overdue = max(0, (ref_date - billing.due_date).days)
            if billing.nim not in arrears_map:
                arrears_map[billing.nim] = {
                    "nim": billing.nim,
                    "nama": mahasiswa.nama_lengkap,
                    "total_tunggakan": 0,
                    "lama_tunggakan_hari": 0,
                    "status": billing.status.value,
                }
            arrears_map[billing.nim]["total_tunggakan"] += billing.remaining_amount
            arrears_map[billing.nim]["lama_tunggakan_hari"] = max(
                arrears_map[billing.nim]["lama_tunggakan_hari"], days_overdue
            )
            if billing.status == BillingStatus.OVERDUE:
                arrears_map[billing.nim]["status"] = BillingStatus.OVERDUE.value

    total_mahasiswa = len(student_status_map)
    mahasiswa_lunas = sum(
        1
        for statuses in student_status_map.values()
        if all(st == BillingStatus.PAID for st in statuses)
    )
    collection_rate = (
        round((mahasiswa_lunas / total_mahasiswa) * 100.0, 2)
        if total_mahasiswa > 0
        else 0.0
    )

    sorted_arrears = sorted(
        arrears_map.values(),
        key=lambda item: (-item["total_tunggakan"], -item["lama_tunggakan_hari"], item["nim"]),
    )
    top_10 = [
        {
            "rank": idx,
            "nim": item["nim"],
            "nama": item["nama"],
            "total_tunggakan": item["total_tunggakan"],
            "tunggakan_formatted": format_rupiah(item["total_tunggakan"]),
            "lama_tunggakan_hari": item["lama_tunggakan_hari"],
            "status": item["status"],
        }
        for idx, item in enumerate(sorted_arrears[:10], start=1)
    ]

    overdue_gt_2_months = sum(
        1
        for item in arrears_map.values()
        if item["lama_tunggakan_hari"] > OVERDUE_REMINDER_THRESHOLD_DAYS
    )
    rekomendasi = (
        f"Kirim reminder ke {overdue_gt_2_months} mahasiswa dengan tunggakan >2 bulan"
    )

    facts = {
        "periode": current_ym,
        "total_revenue_bulan_ini": total_revenue_bulan_ini,
        "total_revenue_formatted": format_rupiah(total_revenue_bulan_ini),
        "collection_rate": collection_rate,
        "collection_rate_summary": f"{collection_rate:.2f}% mahasiswa sudah bayar",
        "total_mahasiswa": total_mahasiswa,
        "mahasiswa_sudah_bayar": mahasiswa_lunas,
        "top_10_tunggakan": top_10,
        "tunggakan_lebih_2_bulan_count": overdue_gt_2_months,
        "rekomendasi": rekomendasi,
    }
    narasi, engine = _optional_gemini_narrative(facts)

    return {
        **facts,
        "source": "database",
        "engine": engine,
        "generated_at": datetime.now(timezone.utc),
        "ringkasan_eksekutif": narasi,
    }

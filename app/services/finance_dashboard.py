from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Billing,
    BillingStatus,
    CalonMahasiswa,
    Payment,
    PaymentStatus,
    ProgramStudi,
)
from app.services.billing_service import UNPAID_BILLING_STATUSES

BULAN_INDONESIA: dict[int, str] = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "Mei",
    6: "Jun",
    7: "Jul",
    8: "Agu",
    9: "Sep",
    10: "Okt",
    11: "Nov",
    12: "Des",
}


def _format_month_label(ym: str) -> str:
    try:
        year_str, month_str = ym.split("-")
        month_name = BULAN_INDONESIA.get(int(month_str), month_str)
        return f"{month_name} {year_str}"
    except ValueError:
        return ym


def get_finance_dashboard(
    db: Session,
    semester: str | None = None,
) -> dict[str, Any]:
    """Mengagregasi data real-time Dashboard Keuangan:
    - Ringkasan global (total tagihan, terbayar, sisa, collection rate, distribusi status)
    - Per mahasiswa: total tagihan, sudah bayar, sisa, status, krs_blocked
    - Per program studi: collection rate (% mahasiswa lunas & % nominal terkumpul)
    - Grafik pembayaran per bulan (hanya transaksi payment berstatus success)
    """
    billing_stmt = (
        select(Billing, CalonMahasiswa, ProgramStudi)
        .join(CalonMahasiswa, CalonMahasiswa.nim == Billing.nim)
        .join(ProgramStudi, ProgramStudi.id == CalonMahasiswa.program_studi_id)
        .order_by(Billing.nim.asc(), Billing.semester.asc())
    )
    if semester:
        billing_stmt = billing_stmt.where(Billing.semester == semester.strip())

    rows = db.execute(billing_stmt).all()

    status_counts: dict[str, int] = {
        BillingStatus.UNPAID.value: 0,
        BillingStatus.PARTIAL.value: 0,
        BillingStatus.PAID.value: 0,
        BillingStatus.OVERDUE.value: 0,
    }

    student_agg: dict[str, dict[str, Any]] = {}
    prodi_agg: dict[str, dict[str, Any]] = {}
    prodi_student_paid: dict[str, dict[str, bool]] = defaultdict(dict)

    total_pokok = 0
    total_denda = 0
    total_tagihan = 0
    total_sudah_bayar = 0

    for billing, mahasiswa, prodi in rows:
        status_val = billing.status.value
        status_counts[status_val] = status_counts.get(status_val, 0) + 1

        bill_total = billing.total
        bill_paid = billing.paid_amount
        bill_remaining = billing.remaining_amount

        total_pokok += billing.amount
        total_denda += billing.late_fee
        total_tagihan += bill_total
        total_sudah_bayar += bill_paid

        # Agregasi per mahasiswa
        if billing.nim not in student_agg:
            student_agg[billing.nim] = {
                "nim": billing.nim,
                "nama": mahasiswa.nama_lengkap,
                "email": mahasiswa.email,
                "program_studi_kode": prodi.kode,
                "program_studi": prodi.nama,
                "jumlah_tagihan": 0,
                "total_pokok": 0,
                "total_denda": 0,
                "total_tagihan": 0,
                "sudah_bayar": 0,
                "sisa": 0,
                "statuses": [],
                "krs_blocked": False,
            }
        s_item = student_agg[billing.nim]
        s_item["jumlah_tagihan"] += 1
        s_item["total_pokok"] += billing.amount
        s_item["total_denda"] += billing.late_fee
        s_item["total_tagihan"] += bill_total
        s_item["sudah_bayar"] += bill_paid
        s_item["sisa"] += bill_remaining
        s_item["statuses"].append(status_val)
        if billing.status in UNPAID_BILLING_STATUSES:
            s_item["krs_blocked"] = True

        # Agregasi per program studi
        if prodi.kode not in prodi_agg:
            prodi_agg[prodi.kode] = {
                "kode_prodi": prodi.kode,
                "program_studi": prodi.nama,
                "fakultas": prodi.fakultas,
                "total_tagihan": 0,
                "total_terbayar": 0,
                "total_sisa": 0,
            }
        p_item = prodi_agg[prodi.kode]
        p_item["total_tagihan"] += bill_total
        p_item["total_terbayar"] += bill_paid
        p_item["total_sisa"] += bill_remaining

        # Mahasiswa di prodi dianggap lunas jika SEMUA tagihannya pada filter ini berstatus paid.
        prev_paid = prodi_student_paid[prodi.kode].get(billing.nim, True)
        prodi_student_paid[prodi.kode][billing.nim] = prev_paid and (
            billing.status == BillingStatus.PAID
        )

    # Finalisasi daftar per mahasiswa
    per_mahasiswa: list[dict[str, Any]] = []
    mahasiswa_lunas_total = 0
    for nim, item in sorted(student_agg.items(), key=lambda pair: (-pair[1]["sisa"], pair[0])):
        statuses = set(item["statuses"])
        if BillingStatus.OVERDUE.value in statuses:
            overall_status = BillingStatus.OVERDUE.value
        elif BillingStatus.PARTIAL.value in statuses:
            overall_status = BillingStatus.PARTIAL.value
        elif BillingStatus.UNPAID.value in statuses:
            overall_status = BillingStatus.UNPAID.value
        else:
            overall_status = BillingStatus.PAID.value
            mahasiswa_lunas_total += 1

        per_mahasiswa.append(
            {
                "nim": item["nim"],
                "nama": item["nama"],
                "email": item["email"],
                "program_studi_kode": item["program_studi_kode"],
                "program_studi": item["program_studi"],
                "jumlah_tagihan": item["jumlah_tagihan"],
                "total_pokok": item["total_pokok"],
                "total_denda": item["total_denda"],
                "total_tagihan": item["total_tagihan"],
                "sudah_bayar": item["sudah_bayar"],
                "sisa": item["sisa"],
                "status": overall_status,
                "krs_blocked": item["krs_blocked"],
            }
        )

    # Finalisasi daftar per program studi (collection rate)
    per_prodi: list[dict[str, Any]] = []
    for kode, item in sorted(prodi_agg.items()):
        mhs_map = prodi_student_paid[kode]
        jumlah_mhs = len(mhs_map)
        lunas_count = sum(1 for is_paid in mhs_map.values() if is_paid)
        belum_lunas = jumlah_mhs - lunas_count
        rate_mhs = round((lunas_count / jumlah_mhs) * 100.0, 2) if jumlah_mhs > 0 else 0.0
        rate_nominal = (
            round((item["total_terbayar"] / item["total_tagihan"]) * 100.0, 2)
            if item["total_tagihan"] > 0
            else 0.0
        )
        per_prodi.append(
            {
                "kode_prodi": item["kode_prodi"],
                "program_studi": item["program_studi"],
                "fakultas": item["fakultas"],
                "jumlah_mahasiswa": jumlah_mhs,
                "mahasiswa_lunas": lunas_count,
                "mahasiswa_belum_lunas": belum_lunas,
                "total_tagihan": item["total_tagihan"],
                "total_terbayar": item["total_terbayar"],
                "total_sisa": item["total_sisa"],
                "collection_rate": rate_mhs,
                "collection_rate_nominal": rate_nominal,
            }
        )

    # Agregasi grafik pembayaran per bulan
    payment_stmt = (
        select(Payment)
        .join(Billing, Billing.id == Payment.billing_id)
        .where(Payment.status == PaymentStatus.SUCCESS)
        .order_by(Payment.paid_at.asc())
    )
    if semester:
        payment_stmt = payment_stmt.where(Billing.semester == semester.strip())

    payments = list(db.scalars(payment_stmt).all())
    monthly_map: dict[str, dict[str, Any]] = {}
    for pay in payments:
        ym = pay.paid_at.strftime("%Y-%m")
        if ym not in monthly_map:
            monthly_map[ym] = {
                "month": ym,
                "label": _format_month_label(ym),
                "jumlah_transaksi": 0,
                "total_bayar": 0,
            }
        monthly_map[ym]["jumlah_transaksi"] += 1
        monthly_map[ym]["total_bayar"] += pay.amount

    monthly_payments = [monthly_map[k] for k in sorted(monthly_map.keys())]

    total_mahasiswa = len(per_mahasiswa)
    total_sisa = max(0, total_tagihan - total_sudah_bayar)
    collection_rate_mhs = (
        round((mahasiswa_lunas_total / total_mahasiswa) * 100.0, 2)
        if total_mahasiswa > 0
        else 0.0
    )
    collection_rate_nominal = (
        round((total_sudah_bayar / total_tagihan) * 100.0, 2)
        if total_tagihan > 0
        else 0.0
    )

    return {
        "semester_filter": semester.strip() if semester else None,
        "generated_at": datetime.now(timezone.utc),
        "summary": {
            "total_mahasiswa": total_mahasiswa,
            "mahasiswa_lunas": mahasiswa_lunas_total,
            "mahasiswa_menunggak": total_mahasiswa - mahasiswa_lunas_total,
            "total_pokok": total_pokok,
            "total_denda": total_denda,
            "total_tagihan": total_tagihan,
            "total_sudah_bayar": total_sudah_bayar,
            "total_sisa": total_sisa,
            "collection_rate": collection_rate_mhs,
            "collection_rate_nominal": collection_rate_nominal,
            "status_counts": status_counts,
        },
        "per_mahasiswa": per_mahasiswa,
        "per_prodi": per_prodi,
        "monthly_payments": monthly_payments,
    }

"""Seed data demo untuk Modul Keuangan SPP (Minggu 11).

Menjalankan secara idempoten:
    py -3.12 -m app.seed_finance
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    Billing,
    CalonMahasiswa,
    JalurMasuk,
    ProgramStudi,
    StatusPMB,
)
from app.services.billing_service import BillingService
from app.services.pmb_service import approve_pendaftar


def _ensure_prodi(db, kode: str, nama: str, fakultas: str) -> ProgramStudi:
    existing = db.scalar(select(ProgramStudi).where(ProgramStudi.kode == kode))
    if existing is not None:
        return existing
    prodi = ProgramStudi(kode=kode, nama=nama, fakultas=fakultas)
    db.add(prodi)
    db.flush()
    return prodi


def _ensure_jalur(db, nama: str) -> JalurMasuk:
    existing = db.scalar(select(JalurMasuk).where(JalurMasuk.nama == nama))
    if existing is not None:
        return existing
    jalur = JalurMasuk(nama=nama)
    db.add(jalur)
    db.flush()
    return jalur


def _ensure_approved_student(
    db,
    *,
    nama: str,
    email: str,
    no_hp: str,
    prodi_id: int,
    jalur_id: int,
) -> CalonMahasiswa:
    mhs = db.scalar(
        select(CalonMahasiswa).where(CalonMahasiswa.email == email)
    )
    if mhs is None:
        mhs = CalonMahasiswa(
            nama_lengkap=nama,
            email=email,
            no_hp=no_hp,
            tanggal_lahir=date(2006, 5, 10),
            alamat="Kampus Utama",
            program_studi_id=prodi_id,
            jalur_masuk_id=jalur_id,
            status=StatusPMB.PENDING,
        )
        db.add(mhs)
        db.commit()
        db.refresh(mhs)
    if mhs.status != StatusPMB.APPROVED or mhs.nim is None:
        mhs = approve_pendaftar(db, mhs.id)
    return mhs


def seed_finance_demo() -> None:
    db = SessionLocal()
    service = BillingService()
    try:
        # 1. Pastikan Program Studi Teknik (5jt) & Ekonomi (4jt) + Jalur Masuk tersedia
        prodi_si = _ensure_prodi(db, "001", "Sistem Informasi", "Fakultas Ilmu Komputer")
        prodi_teknik = _ensure_prodi(db, "002", "Teknik Informatika", "Fakultas Teknik")
        prodi_ekonomi = _ensure_prodi(db, "003", "Ekonomi Manajemen", "Fakultas Ekonomi")
        jalur_snbp = _ensure_jalur(db, "SNBP")
        db.commit()

        service.ensure_default_payment_methods(db)
        db.commit()

        # 2. Pastikan 6 mahasiswa aktif demo tersedia
        students_spec = [
            ("Raka Pratama", "raka.spp@example.com", "081200010001", prodi_teknik.id),
            ("Sinta Dewi", "sinta.spp@example.com", "081200010002", prodi_teknik.id),
            ("Bima Saputra", "bima.spp@example.com", "081200010003", prodi_teknik.id),
            ("Dina Lestari", "dina.spp@example.com", "081200010004", prodi_ekonomi.id),
            ("Fajar Nugroho", "fajar.spp@example.com", "081200010005", prodi_ekonomi.id),
            ("Ayu Wulandari", "ayu.spp@example.com", "081200010006", prodi_si.id),
        ]
        students: dict[str, CalonMahasiswa] = {}
        for nama, email, no_hp, prodi_id in students_spec:
            mhs = _ensure_approved_student(
                db,
                nama=nama,
                email=email,
                no_hp=no_hp,
                prodi_id=prodi_id,
                jalur_id=jalur_snbp.id,
            )
            students[email] = mhs

        # 3. Generate tagihan SPP semester 20261 (start 90 hari lalu agar ada kasus overdue > 2 bulan)
        today = date.today()
        semester_start = today - timedelta(days=90)
        service.generate_billing(db, semester="20261", semester_start=semester_start)

        # 4. Simulasikan beragam status (paid, partial, unpaid, overdue > 2 bulan) secara idempoten
        def _get_billing(email: str) -> Billing:
            nim = students[email].nim
            return db.scalar(
                select(Billing).where(Billing.nim == nim, Billing.semester == "20261")
            )

        # Raka (Teknik 5jt) -> PAID 2 bulan lalu via webhook
        b_raka = _get_billing("raka.spp@example.com")
        if b_raka and b_raka.paid_amount == 0:
            service.process_payment(
                db,
                billing_id=b_raka.id,
                amount=b_raka.amount,
                method="gateway_va",
                transaction_id="SEED-WH-RAKA-20261",
                source="webhook",
                paid_at=datetime.now(timezone.utc) - timedelta(days=62),
            )

        # Dina (Ekonomi 4jt) -> PAID 1 bulan lalu via VA BCA
        b_dina = _get_billing("dina.spp@example.com")
        if b_dina and b_dina.paid_amount == 0:
            service.process_payment(
                db,
                billing_id=b_dina.id,
                amount=b_dina.amount,
                method="va_bca",
                transaction_id="SEED-PAY-DINA-20261",
                source="manual",
                paid_at=datetime.now(timezone.utc) - timedelta(days=31),
            )

        # Ayu (SI 4.5jt) -> PAID bulan ini via QRIS
        b_ayu = _get_billing("ayu.spp@example.com")
        if b_ayu and b_ayu.paid_amount == 0:
            service.process_payment(
                db,
                billing_id=b_ayu.id,
                amount=b_ayu.amount,
                method="qris",
                transaction_id="SEED-PAY-AYU-20261",
                source="manual",
                paid_at=datetime.now(timezone.utc) - timedelta(days=2),
            )

        # Sinta (Teknik 5jt) -> PARTIAL (cicil 2.5jt bulan ini, due_date masih 7 hari lagi)
        b_sinta = _get_billing("sinta.spp@example.com")
        if b_sinta and b_sinta.paid_amount == 0:
            b_sinta.due_date = today + timedelta(days=7)
            db.commit()
            service.process_payment(
                db,
                billing_id=b_sinta.id,
                amount=2_500_000,
                method="transfer_bank",
                transaction_id="SEED-PAY-SINTA-20261",
                source="manual",
                paid_at=datetime.now(timezone.utc) - timedelta(days=1),
            )

        # Fajar (Ekonomi 4jt) -> UNPAID (due_date masih 10 hari lagi)
        b_fajar = _get_billing("fajar.spp@example.com")
        if b_fajar and b_fajar.paid_amount == 0:
            b_fajar.due_date = today + timedelta(days=10)
            db.commit()

        # Bima (Teknik 5jt) -> tetap jatuh tempo 76 hari lalu -> OVERDUE > 2 bulan + denda
        service.calculate_and_apply_late_fees(db, as_of=today)

        print("Seed demo Modul Keuangan (SPP) selesai:")
        for email, mhs in students.items():
            b = _get_billing(email)
            print(
                f"  - {mhs.nim} ({mhs.nama_lengkap}): "
                f"pokok=Rp {b.amount:,}, denda=Rp {b.late_fee:,}, "
                f"bayar=Rp {b.paid_amount:,}, status={b.status.value}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    seed_finance_demo()

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KRS, KRSAktor, KRSDetail, KRSLog, KRSStatus, Notifikasi

INVALIDATABLE_STATUSES = (KRSStatus.SUBMITTED, KRSStatus.APPROVED)


class KRSInvalidator:
    """
    Mengembalikan KRS yang memuat kelas terdampak (berstatus SUBMITTED atau APPROVED)
    ke status REVISION melalui pintu resmi `krs.transition(KRSStatus.REVISION, KRSAktor.SISTEM)`.

    Penting: kelas ini TIDAK memanggil `db.commit()` sendiri karena ikut berpartisipasi
    di dalam transaksi atomik milik `SchedulingService`.
    """

    def invalidate_for_kelas(
        self,
        db: Session,
        kelas_id: int,
        alasan: str,
    ) -> list[KRS]:
        affected_krs_ids = list(
            db.scalars(
                select(KRS.id)
                .join(KRSDetail, KRSDetail.krs_id == KRS.id)
                .where(
                    KRSDetail.kelas_id == kelas_id,
                    KRS.status.in_(INVALIDATABLE_STATUSES),
                )
                .distinct()
                .order_by(KRS.id)
            ).all()
        )
        if not affected_krs_ids:
            return []

        affected_krs = list(
            db.scalars(
                select(KRS)
                .where(KRS.id.in_(affected_krs_ids))
                .order_by(KRS.id)
                .with_for_update()
            ).all()
        )

        now = datetime.now(timezone.utc)
        for krs in affected_krs:
            dari_status = krs.status
            # Satu pintu resmi state machine KRS (tidak pernah menulis status langsung).
            krs.transition(KRSStatus.REVISION, KRSAktor.SISTEM)
            krs.catatan_revisi = alasan
            krs.version = (krs.version or 1) + 1
            krs.updated_at = now

            db.add(
                KRSLog(
                    krs_id=krs.id,
                    aktor=KRSAktor.SISTEM.value,
                    aktor_id="system:scheduling",
                    aksi="invalidate_schedule",
                    dari_status=dari_status.value,
                    ke_status=KRSStatus.REVISION.value,
                    catatan=alasan,
                )
            )
            db.add(
                Notifikasi(
                    nim=krs.nim,
                    judul="Jadwal Berubah — KRS Perlu Revisi",
                    pesan=alasan,
                    dibaca=False,
                )
            )

        return affected_krs

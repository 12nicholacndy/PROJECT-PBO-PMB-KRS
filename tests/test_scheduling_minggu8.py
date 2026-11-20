from datetime import date, time
import json
import logging

import pytest
from sqlalchemy import select

from app.main import app
from app.models import (
    CalonMahasiswa,
    Dosen,
    Jadwal,
    KRS,
    KRSDetail,
    KRSLog,
    KRSStatus,
    Kelas,
    MataKuliah,
    Notifikasi,
    OutboxEvent,
    OutboxStatus,
    Ruangan,
    StatusPMB,
)
from app.routers.jadwal import get_llm_client
from app.services.ai_rescheduler import (
    AIRescheduler,
    FakeLLMClient,
    HeuristicFallbackLLMClient,
    filter_ai_suggestions,
)
from app.services.conflict_detector import JadwalSlot
from app.services.ical_exporter import export_jadwals_to_ics
from app.services.outbox import OutboxDispatcher
from app.services.schedule_events import EventType, ScheduleEvent
from app.services.schedule_observers import (
    AdminObserver,
    LecturerObserver,
    LogChannel,
    ScheduleSubject,
    StudentObserver,
    get_default_channels,
)
from app.services.scheduling_service import JadwalData, SchedulingService

ADMIN_HEADERS = {"X-Role": "admin_akademik"}


# ---------------------------------------------------------------------------
# Helper & Test Doubles untuk Unit Test Observer (Tanpa Database)
# ---------------------------------------------------------------------------


class SpyObserver:
    """Observer palsu yang hanya mencatat seluruh event yang diterimanya."""

    def __init__(self) -> None:
        self.received: list[ScheduleEvent] = []

    def update(self, event: ScheduleEvent) -> None:
        self.received.append(event)


class BrokenObserver:
    """Observer rusak yang selalu melempar exception untuk menguji fault isolation."""

    def update(self, event: ScheduleEvent) -> None:
        raise RuntimeError("SMS gateway unreachable")


class FakeParticipantRepo:
    def __init__(self, mapping: dict[int, list[str]]) -> None:
        self._mapping = mapping

    def get_student_nims(self, kelas_id: int) -> list[str]:
        return list(self._mapping.get(kelas_id, []))


def _sample_event(
    *,
    event_type: EventType = EventType.SCHEDULE_CHANGED,
    dosen_before: int = 1,
    dosen_after: int = 1,
) -> ScheduleEvent:
    before = {
        "id": 7,
        "kelas_id": 1,
        "kode_mk": "IF102",
        "nama_kelas": "A",
        "ruangan_kode": "A101",
        "dosen_id": dosen_before,
        "hari": 1,
        "jam_mulai": "08:00:00",
        "jam_selesai": "09:40:00",
        "sequence": 0,
    }
    after = (
        None
        if event_type == EventType.SCHEDULE_DELETED
        else {
            **before,
            "hari": 3,
            "jam_mulai": "13:00:00",
            "jam_selesai": "14:40:00",
            "ruangan_kode": "B201",
            "dosen_id": dosen_after,
            "sequence": 1,
        }
    )
    return ScheduleEvent.create(
        event_type=event_type,
        jadwal_id=7,
        kelas_id=1,
        before=before,
        after=after,
    )


# ---------------------------------------------------------------------------
# 1. TES WAJIB #1 & #2 (UNIT TEST TANPA DATABASE) — Observer Pattern
# ---------------------------------------------------------------------------


def test_observer_called_once_even_if_attached_twice_and_supports_detach():
    """
    Tes Utama #1: SpyObserver sengaja di-attach dua kali;
    saat notify() dipanggil, ia wajib menerima tepat 1 event.
    """
    subject = ScheduleSubject()
    spy = SpyObserver()

    assert subject.attach(spy) is True
    assert subject.attach(spy) is False  # Duplikasi ditolak
    assert len(subject.observers) == 1

    event = _sample_event()
    failed = subject.notify(event)

    assert failed == []
    assert len(spy.received) == 1
    assert spy.received[0] == event

    # Detach berfungsi dan menghentikan pengiriman berikutnya
    assert subject.detach(spy) is True
    assert subject.detach(spy) is False
    subject.notify(_sample_event())
    assert len(spy.received) == 1


def test_broken_observer_does_not_stop_subsequent_observers():
    """
    Tes Utama #2: BrokenObserver diletakkan paling depan dan selalu melempar exception;
    SpyObserver di belakangnya tetap wajib dipanggil dan notify() melaporkan nama gagal.
    """
    subject = ScheduleSubject()
    broken = BrokenObserver()
    spy = SpyObserver()

    subject.attach(broken)
    subject.attach(spy)

    event = _sample_event()
    failed = subject.notify(event)

    assert failed == ["BrokenObserver"]
    assert len(spy.received) == 1
    assert spy.received[0] == event


def test_three_observers_and_three_channels_with_idempotency_and_lecturer_change(caplog):
    """
    Memastikan StudentObserver, LecturerObserver (termasuk dosen lama saat diganti),
    AdminObserver, dan 3 kanal simulasi (email/sms/push) menulis log serta menerapkan
    deduplikasi berdasarkan idempotency key saat event yang sama dikirim ulang.
    """
    channels = [LogChannel("email"), LogChannel("sms"), LogChannel("push")]
    repo = FakeParticipantRepo({1: ["20260010001", "20260010002"]})

    subject = ScheduleSubject()
    subject.attach(StudentObserver(repo, channels))
    subject.attach(LecturerObserver(channels))
    subject.attach(AdminObserver(channels))

    # Dosen diganti dari 1 -> 2, sehingga KEDUA dosen harus mendapat notifikasi
    event = _sample_event(dosen_before=1, dosen_after=2)
    with caplog.at_level(logging.INFO, logger="app.notifications"):
        assert subject.notify(event) == []
        # Pengulangan (retry) untuk event_id sama TIDAK boleh menghasilkan pesan ganda
        assert subject.notify(event) == []

    # 2 mahasiswa + 2 dosen (baru & lama) + 1 admin = 5 penerima per kanal
    for ch in channels:
        assert len(ch.records) == 5
        recipients = {r["recipient"] for r in ch.records}
        assert recipients == {
            "mahasiswa:20260010001",
            "mahasiswa:20260010002",
            "dosen:1",
            "dosen:2",
            "admin_akademik",
        }

    # Bukti log 3 kanal benar-benar tertulis di logger
    assert any("[EMAIL]" in rec.message for rec in caplog.records)
    assert any("[SMS]" in rec.message for rec in caplog.records)
    assert any("[PUSH]" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Fixture Lingkungan Lengkap Minggu 8 (Mahasiswa Rina + KRS Approved/Submitted/Draft)
# ---------------------------------------------------------------------------


@pytest.fixture()
def minggu8_env(db):
    """Menyiapkan ruangan, dosen, MK, kelas, jadwal, dan mahasiswa (Rina + Budi + Citra)."""
    db.add_all(
        [
            Ruangan(kode="A101", nama="Ruang A101", kapasitas=40),
            Ruangan(kode="A102", nama="Ruang A102", kapasitas=25),
            Ruangan(kode="B201", nama="Ruang B201", kapasitas=50),
            Dosen(id=1, nidn="0101018501", nama="Dr. Andi Wijaya"),
            Dosen(id=2, nidn="0102028602", nama="Dr. Rina Kusuma"),
            MataKuliah(
                kode_mk="IF102",
                nama="Pemrograman Berorientasi Objek",
                sks=3,
                hari=1,
                jam_mulai=time(8, 0),
                jam_selesai=time(9, 40),
            ),
            MataKuliah(
                kode_mk="IF101",
                nama="Basis Data",
                sks=3,
                hari=2,
                jam_mulai=time(8, 0),
                jam_selesai=time(9, 40),
            ),
        ]
    )
    db.flush()

    db.add_all(
        [
            Kelas(id=1, kode_mk="IF102", nama_kelas="A", kuota=35, semester="20261"),
            Kelas(id=2, kode_mk="IF101", nama_kelas="A", kuota=30, semester="20261"),
        ]
    )
    db.flush()

    jadwal_pbo = Jadwal(
        id=1,
        kelas_id=1,
        ruangan_kode="A101",
        dosen_id=1,
        hari=1,
        jam_mulai=time(8, 0),
        jam_selesai=time(9, 40),
        sequence=0,
    )
    jadwal_bd = Jadwal(
        id=2,
        kelas_id=2,
        ruangan_kode="A101",
        dosen_id=2,
        hari=2,
        jam_mulai=time(8, 0),
        jam_selesai=time(9, 40),
        sequence=0,
    )
    db.add_all([jadwal_pbo, jadwal_bd])

    # 3 mahasiswa mengambil kelas PBO (kelas_id=1) dengan status KRS berbeda:
    # - Rina: APPROVED -> wajib kembali ke REVISION
    # - Budi: SUBMITTED -> wajib kembali ke REVISION
    # - Citra: DRAFT -> tidak boleh disentuh (tetap DRAFT)
    students = [
        ("Rina Putri", "rina@example.com", "20260010001", KRSStatus.APPROVED),
        ("Budi Santoso", "budi@example.com", "20260010002", KRSStatus.SUBMITTED),
        ("Citra Dewi", "citra@example.com", "20260010003", KRSStatus.DRAFT),
    ]
    for nama, email, nim, status_krs in students:
        db.add(
            CalonMahasiswa(
                nama_lengkap=nama,
                email=email,
                no_hp="081234567890",
                tanggal_lahir=date(2007, 3, 15),
                alamat="Bogor",
                program_studi_id=1,
                jalur_masuk_id=1,
                status=StatusPMB.APPROVED,
                nim=nim,
            )
        )
        db.flush()
        krs = KRS(
            nim=nim,
            semester="20261",
            status=status_krs,
            dosen_pa_id=10,
            version=1,
        )
        db.add(krs)
        db.flush()
        db.add(KRSDetail(krs_id=krs.id, kode_mk="IF102", kelas_id=1))

    db.commit()
    return db


# ---------------------------------------------------------------------------
# 2. TES WAJIB #3 (INTEGRATION TEST) — Ubah Jadwal -> KRS Revision + Outbox + Log
# ---------------------------------------------------------------------------


def test_update_schedule_invalidates_approved_krs_and_writes_outbox_event(
    client, minggu8_env, caplog
):
    """
    Tes Utama #3: Mengubah jadwal MK PBO yang ada di KRS APPROVED milik Rina
    (dan SUBMITTED milik Budi) -> status KRS menjadi REVISION lewat aktor SISTEM,
    KRS DRAFT milik Citra tetap DRAFT, tercatat tepat 1 baris OutboxEvent (SENT),
    sequence jadwal naik menjadi 1, dan 3 kanal simulasi menulis log.
    """
    db = minggu8_env

    with caplog.at_level(logging.INFO, logger="app.notifications"):
        res = client.put(
            "/api/jadwal/1",
            headers=ADMIN_HEADERS,
            json={
                "kelas_id": 1,
                "ruangan_kode": "B201",
                "dosen_id": 1,
                "hari": 3,
                "jam_mulai": "13:00:00",
                "jam_selesai": "14:40:00",
            },
        )

    assert res.status_code == 200
    body = res.json()
    assert body["hari"] == 3
    assert body["ruangan_kode"] == "B201"
    assert body["sequence"] == 1

    db.expire_all()
    krs_rina = db.scalar(select(KRS).where(KRS.nim == "20260010001"))
    krs_budi = db.scalar(select(KRS).where(KRS.nim == "20260010002"))
    krs_citra = db.scalar(select(KRS).where(KRS.nim == "20260010003"))

    assert krs_rina is not None and krs_rina.status == KRSStatus.REVISION
    assert krs_rina.version == 2
    assert "IF102-A" in (krs_rina.catatan_revisi or "")

    assert krs_budi is not None and krs_budi.status == KRSStatus.REVISION
    assert krs_citra is not None and krs_citra.status == KRSStatus.DRAFT

    # Audit log KRS mencatat aktor='sistem'
    logs_rina = list(
        db.scalars(select(KRSLog).where(KRSLog.krs_id == krs_rina.id)).all()
    )
    assert len(logs_rina) == 1
    assert logs_rina[0].aktor == "sistem"
    assert logs_rina[0].dari_status == "approved"
    assert logs_rina[0].ke_status == "revision"

    # Kotak masuk Notifikasi mahasiswa Minggu 6 tetap terisi
    notif_rina = list(
        db.scalars(
            select(Notifikasi).where(Notifikasi.nim == "20260010001")
        ).all()
    )
    assert len(notif_rina) == 1

    # Tepat 1 baris OutboxEvent tertulis & sudah diproses menjadi SENT
    outbox_rows = list(db.scalars(select(OutboxEvent)).all())
    assert len(outbox_rows) == 1
    assert outbox_rows[0].event_type == EventType.SCHEDULE_CHANGED.value
    assert outbox_rows[0].status == OutboxStatus.SENT
    assert outbox_rows[0].attempts == 1

    # Semua 3 kanal (email, sms, push) menerima pengiriman untuk Rina, Budi, Citra, Dosen, Admin
    channels = get_default_channels()
    assert {c.name for c in channels} == {"email", "sms", "push"}
    for ch in channels:
        assert any(r["recipient"] == "mahasiswa:20260010001" for r in ch.records)
        assert any(r["recipient"] == "dosen:1" for r in ch.records)
        assert any(r["recipient"] == "admin_akademik" for r in ch.records)

    # Endpoint audit outbox juga menampilkan event & log kanal
    audit_res = client.get("/api/jadwal/outbox", headers=ADMIN_HEADERS)
    assert audit_res.status_code == 200
    assert len(audit_res.json()["events"]) == 1
    assert len(audit_res.json()["recent_channel_logs"]) > 0


def test_outbox_dispatcher_retries_5_times_then_marks_failed_without_duplicate_channel_sends(
    minggu8_env,
):
    """
    Memastikan mekanisme retry OutboxDispatcher:
    - Jika ada 1 observer rusak + 1 observer sehat, observer sehat mengirim tepat 1x (idempotent)
      saat retry berulang.
    - Setelah 5 percobaan gagal, status baris outbox berubah dari PENDING menjadi FAILED.
    - Perubahan jadwal tetap tersimpan walau dispatcher gagal.
    """
    db = minggu8_env
    channels = [LogChannel("email"), LogChannel("sms"), LogChannel("push")]
    subject = ScheduleSubject()
    subject.attach(AdminObserver(channels))
    subject.attach(BrokenObserver())

    service = SchedulingService(db, subject=subject, dispatch_after_commit=False)
    updated = service.update(
        1,
        JadwalData(
            kelas_id=1,
            ruangan_kode="B201",
            dosen_id=1,
            hari=4,
            jam_mulai=time(10, 0),
            jam_selesai=time(11, 40),
        ),
    )
    assert updated.hari == 4

    dispatcher = OutboxDispatcher(db, subject, max_attempts=5)

    # Percobaan 1..4: tetap PENDING, observer sehat tidak kirim duplikat
    for attempt in range(1, 5):
        summary = dispatcher.run()
        assert summary == {"processed": 1, "sent": 0, "retried": 1, "failed": 0}
        row = db.scalar(select(OutboxEvent))
        assert row is not None
        assert row.status == OutboxStatus.PENDING
        assert row.attempts == attempt

    # Percobaan ke-5: ditandai FAILED
    summary5 = dispatcher.run()
    assert summary5 == {"processed": 1, "sent": 0, "retried": 0, "failed": 1}
    row = db.scalar(select(OutboxEvent))
    assert row is not None
    assert row.status == OutboxStatus.FAILED
    assert row.attempts == 5
    assert "BrokenObserver" in (row.last_error or "")

    # Deduplikasi: walau dicoba 5 kali, tiap kanal hanya mengirim 1 pesan ke admin!
    for ch in channels:
        assert len(ch.records) == 1


def test_delete_schedule_invalidates_krs_and_emits_schedule_deleted_outbox(
    client, minggu8_env
):
    db = minggu8_env
    res = client.delete("/api/jadwal/1", headers=ADMIN_HEADERS)
    assert res.status_code == 200

    db.expire_all()
    krs_rina = db.scalar(select(KRS).where(KRS.nim == "20260010001"))
    assert krs_rina is not None and krs_rina.status == KRSStatus.REVISION

    row = db.scalar(select(OutboxEvent))
    assert row is not None
    assert row.event_type == EventType.SCHEDULE_DELETED.value
    assert row.status == OutboxStatus.SENT


# ---------------------------------------------------------------------------
# 3. TES WAJIB #4 — Saringan 4 Lapis Saran AI dengan FakeLLMClient
# ---------------------------------------------------------------------------


def test_ai_guardrail_filters_fake_llm_keeps_only_1_valid_out_of_3(
    client, minggu8_env
):
    """
    Tes Utama #4: FakeLLMClient mengembalikan 3 saran yang dibungkus teks pengantar:
      1. Valid: Rabu 08:00-09:40 di B201 (kapasitas 50 >= 35, bebas bentrok) -> LOLOS
      2. Ruangan fiktif: Z999 tidak ada di database -> DIBUANG (Saringan 3)
      3. Bentrok: Selasa 08:00-09:40 di A101 (bentrok dengan Jadwal #2) -> DIBUANG (Saringan 4)
    Hasil akhir wajib tepat 1 saran valid.
    """
    fake_raw_response = (
        "Berikut rekomendasi slot terbaik untuk Anda:\n"
        + json.dumps(
            [
                {
                    "hari": 3,
                    "jam_mulai": "08:00",
                    "jam_selesai": "09:40",
                    "ruangan_kode": "B201",
                    "alasan": "Slot pagi bebas bentrok dengan kapasitas 50 kursi.",
                },
                {
                    "hari": 3,
                    "jam_mulai": "10:00",
                    "jam_selesai": "11:40",
                    "ruangan_kode": "Z999",
                    "alasan": "Ruangan fiktif hasil halusinasi LLM.",
                },
                {
                    "hari": 2,
                    "jam_mulai": "08:00",
                    "jam_selesai": "09:40",
                    "ruangan_kode": "A101",
                    "alasan": "Bentrok dengan Jadwal #2 di A101.",
                },
            ]
        )
        + "\nSemoga membantu!"
    )
    fake_llm = FakeLLMClient(fake_raw_response)

    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    try:
        res = client.post("/api/jadwal/1/suggest", headers=ADMIN_HEADERS)
    finally:
        app.dependency_overrides.pop(get_llm_client, None)

    assert res.status_code == 200
    data = res.json()
    assert data["jadwal_id"] == 1
    assert data["available_slots_count"] > 0
    assert len(data["suggestions"]) == 1
    assert data["suggestions"][0]["hari"] == 3
    assert data["suggestions"][0]["ruangan_kode"] == "B201"
    assert data["suggestions"][0]["jam_mulai"] == "08:00:00"
    assert fake_llm.last_prompt is not None
    assert "DAFTAR_SLOT_TERSEDIA_JSON:" in fake_llm.last_prompt


def test_ai_guardrail_handles_malformed_json_bad_schema_and_small_room(minggu8_env):
    """Memastikan Saringan 1 (JSON rusak), Saringan 2 (skema rusak), dan Saringan 3 (kapasitas kecil) tidak crash."""
    rooms = {
        "A101": Ruangan(kode="A101", kapasitas=40),
        "A102": Ruangan(kode="A102", kapasitas=25),
    }
    others = [
        JadwalSlot(
            id=2,
            hari=2,
            jam_mulai=time(8, 0),
            jam_selesai=time(9, 40),
            ruangan_kode="A101",
            dosen_id=2,
        )
    ]

    # Saringan 1: bukan JSON valid -> daftar kosong tanpa exception
    assert (
        filter_ai_suggestions(
            "Maaf saya tidak bisa menjawab dalam JSON",
            target_dosen_id=1,
            required_capacity=35,
            rooms_by_code=rooms,
            other_jadwals=others,
        )
        == []
    )

    # Saringan 2 & 3: 1 item hari=9 (invalid), 1 item jam terbalik (invalid),
    # 1 item ruangan A102 kapasitas 25 < 35 (dibuang), 1 item valid -> tepat 1 lolos
    mixed_payload = json.dumps(
        [
            {
                "hari": 9,
                "jam_mulai": "08:00",
                "jam_selesai": "09:40",
                "ruangan_kode": "A101",
                "alasan": "Hari tidak valid",
            },
            {
                "hari": 4,
                "jam_mulai": "11:00",
                "jam_selesai": "09:00",
                "ruangan_kode": "A101",
                "alasan": "Jam terbalik",
            },
            {
                "hari": 4,
                "jam_mulai": "08:00",
                "jam_selesai": "09:40",
                "ruangan_kode": "A102",
                "alasan": "Kapasitas A102 (25) terlalu kecil untuk 35",
            },
            {
                "hari": 4,
                "jam_mulai": "08:00",
                "jam_selesai": "09:40",
                "ruangan_kode": "A101",
                "alasan": "Valid dan kapasitas cukup",
            },
        ]
    )
    filtered = filter_ai_suggestions(
        mixed_payload,
        target_dosen_id=1,
        required_capacity=35,
        rooms_by_code=rooms,
        other_jadwals=others,
    )
    assert len(filtered) == 1
    assert filtered[0].hari == 4
    assert filtered[0].ruangan_kode == "A101"

    # Fallback deterministik juga berjalan lancar dan lolos semua saringan
    rescheduler = AIRescheduler(minggu8_env, HeuristicFallbackLLMClient())
    _, available, fallback_suggestions = rescheduler.suggest_alternatives(1)
    assert len(available) > 0
    assert 1 <= len(fallback_suggestions) <= 3


# ---------------------------------------------------------------------------
# 4. Otorisasi Admin (403), Conflict Resolver UI, Single-Door 409, & Ekspor .ics
# ---------------------------------------------------------------------------


def test_admin_role_protection_returns_403_for_non_admin(client, minggu8_env):
    assert client.get("/api/jadwal/conflicts").status_code == 403
    assert (
        client.get(
            "/api/jadwal/conflicts", headers={"X-Role": "mahasiswa"}
        ).status_code
        == 403
    )
    assert client.post("/api/jadwal/1/suggest").status_code == 403
    assert client.get("/api/jadwal/outbox").status_code == 403
    assert (
        client.put(
            "/api/jadwal/1",
            json={
                "kelas_id": 1,
                "ruangan_kode": "B201",
                "dosen_id": 1,
                "hari": 3,
                "jam_mulai": "08:00:00",
                "jam_selesai": "09:40:00",
            },
        ).status_code
        == 403
    )
    assert client.delete("/api/jadwal/1").status_code == 403


def test_conflict_resolver_ui_and_single_door_apply_rechecks_409(
    client, minggu8_env
):
    # Dashboard HTML tersedia dan memuat elemen utama Conflict Resolver
    ui_res = client.get("/admin/conflict-resolver")
    assert ui_res.status_code == 200
    assert "text/html" in ui_res.headers["content-type"]
    assert "Conflict Resolver Dashboard" in ui_res.text
    assert "/api/jadwal/conflicts" in ui_res.text
    assert "/api/jadwal/export.ics" in ui_res.text

    # Skenario: admin meminta saran, lalu admin lain keburu mengisi slot itu ->
    # saat tombol Terapkan memanggil PUT /api/jadwal/1, sistem menolak dengan 409!
    res_other = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 2,
            "ruangan_kode": "B201",
            "dosen_id": 2,
            "hari": 3,
            "jam_mulai": "08:00:00",
            "jam_selesai": "09:40:00",
        },
    )
    assert res_other.status_code == 201

    res_apply_stale = client.put(
        "/api/jadwal/1",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 1,
            "ruangan_kode": "B201",
            "dosen_id": 1,
            "hari": 3,
            "jam_mulai": "08:00:00",
            "jam_selesai": "09:40:00",
        },
    )
    assert res_apply_stale.status_code == 409
    assert res_apply_stale.json()["conflicts"][0]["type"] == "room_conflict"

    # Anti-Bug #1: perubahan yang ditolak (409) TIDAK boleh menginvalidasi KRS,
    # menulis OutboxEvent, ataupun mengirim notifikasi ke kanal mana pun.
    minggu8_env.expire_all()
    krs_rina = minggu8_env.scalar(select(KRS).where(KRS.nim == "20260010001"))
    assert krs_rina is not None and krs_rina.status == KRSStatus.APPROVED
    assert list(minggu8_env.scalars(select(OutboxEvent)).all()) == []
    assert all(len(ch.records) == 0 for ch in get_default_channels())


def test_icalendar_export_format_crlf_uid_rrule_utc_and_sequence_increment(
    client, minggu8_env
):
    # Ekspor .ics awal (terfilter untuk NIM Rina -> hanya jadwal kelas PBO #1)
    res1 = client.get(
        "/api/jadwal/export.ics",
        params={
            "nim": "20260010001",
            "semester_start": "2026-09-01",
            "semester_end": "2026-12-20",
        },
    )
    assert res1.status_code == 200
    assert "text/calendar" in res1.headers["content-type"]
    ics1 = res1.text

    # Wajib memakai pemisah baris CRLF (\r\n)
    assert "\r\n" in ics1
    assert "\n" not in ics1.replace("\r\n", "")
    assert "BEGIN:VCALENDAR\r\n" in ics1
    assert "BEGIN:VEVENT\r\n" in ics1
    assert "UID:jadwal-1@sevima-mini\r\n" in ics1
    assert "SEQUENCE:0\r\n" in ics1
    # 2026-09-01 adalah Selasa; hari=1 (Senin) pertama jatuh pada 2026-09-07
    assert "DTSTART;TZID=Asia/Jakarta:20260907T080000\r\n" in ics1
    assert "DTEND;TZID=Asia/Jakarta:20260907T094000\r\n" in ics1
    # UNTIL wajib UTC (berakhiran Z)
    assert "RRULE:FREQ=WEEKLY;UNTIL=20261220T165959Z\r\n" in ics1
    assert "UID:jadwal-2@sevima-mini" not in ics1

    # Ubah Jadwal #1 -> SEQUENCE di ekspor .ics berikutnya harus naik ke 1 dengan UID sama
    upd = client.put(
        "/api/jadwal/1",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 1,
            "ruangan_kode": "B201",
            "dosen_id": 1,
            "hari": 3,
            "jam_mulai": "13:00:00",
            "jam_selesai": "14:40:00",
        },
    )
    assert upd.status_code == 200

    res2 = client.get(
        "/api/jadwal/export.ics",
        params={
            "nim": "20260010001",
            "semester_start": "2026-09-01",
            "semester_end": "2026-12-20",
        },
    )
    ics2 = res2.text
    assert "UID:jadwal-1@sevima-mini\r\n" in ics2
    assert "SEQUENCE:1\r\n" in ics2
    assert "LOCATION:B201\r\n" in ics2
    # 2026-09-01 Selasa -> Rabu pertama adalah 2026-09-02
    assert "DTSTART;TZID=Asia/Jakarta:20260902T130000\r\n" in ics2
    assert "DTEND;TZID=Asia/Jakarta:20260902T144000\r\n" in ics2

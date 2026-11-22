from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
import os
from typing import Any
import urllib.error
import urllib.request

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    CalonMahasiswa,
    Jadwal,
    KRS,
    KRSDetail,
    Kelas,
)
from app.services.attendance_errors import AttendanceValidationError
from app.services.attendance_service import ATTENDANCE_WARNING_THRESHOLD, HARI_NAMA

ATTENDED_STATUSES = {"hadir", "terlambat", "present", "late"}
ABSENT_STATUSES = {"alpha", "absent", "tidak_hadir", "absen"}
EXCUSED_STATUSES = {"izin", "sakit", "excused"}

HARI_ALIAS: dict[str, str] = {
    "1": "Senin",
    "senin": "Senin",
    "monday": "Senin",
    "2": "Selasa",
    "selasa": "Selasa",
    "tuesday": "Selasa",
    "3": "Rabu",
    "rabu": "Rabu",
    "wednesday": "Rabu",
    "4": "Kamis",
    "kamis": "Kamis",
    "thursday": "Kamis",
    "5": "Jumat",
    "jumat": "Jumat",
    "jum'at": "Jumat",
    "friday": "Jumat",
    "6": "Sabtu",
    "sabtu": "Sabtu",
    "saturday": "Sabtu",
    "7": "Minggu",
    "minggu": "Minggu",
    "sunday": "Minggu",
}

HIGH_CLASS_ABSENT_RATE_PCT = 30.0
HIGH_STUDENT_DAY_ABSENT_RATE_PCT = 50.0


def _normalize_day(record: dict[str, Any]) -> str:
    raw_day = record.get("hari") or record.get("day")
    if raw_day is not None and str(raw_day).strip():
        key = str(raw_day).strip().lower()
        if key in HARI_ALIAS:
            return HARI_ALIAS[key]
        return str(raw_day).strip().capitalize()

    raw_date = record.get("tanggal") or record.get("date") or record.get("session_date")
    if raw_date:
        try:
            d = date.fromisoformat(str(raw_date).strip()[:10])
            return HARI_NAMA.get(d.isoweekday(), "Tidak Diketahui")
        except ValueError:
            pass
    return "Tidak Diketahui"


def _normalize_time_slot(record: dict[str, Any]) -> str:
    raw_time = (
        record.get("jam_mulai")
        or record.get("jam")
        or record.get("time")
        or record.get("waktu")
    )
    if raw_time is None or not str(raw_time).strip():
        return "Tidak Diketahui"
    text = str(raw_time).strip()
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    return text


def _time_period_label(jam: str) -> str:
    if len(jam) >= 2 and jam[:2].isdigit():
        hour = int(jam[:2])
        if hour < 11:
            return "pagi"
        if hour < 15:
            return "siang"
        if hour < 18:
            return "sore"
        return "malam"
    return ""


def _normalize_status(raw_status: Any) -> str:
    val = str(raw_status or "").strip().lower()
    if val in ATTENDED_STATUSES:
        return "terlambat" if val in {"terlambat", "late"} else "hadir"
    if val in ABSENT_STATUSES:
        return "alpha"
    if val in EXCUSED_STATUSES:
        return "izin"
    raise AttendanceValidationError(
        f"Status presensi '{raw_status}' tidak dikenali "
        f"(gunakan hadir, terlambat, alpha/absent, atau izin)"
    )


def _optional_gemini_narrative(facts: dict[str, Any]) -> tuple[str, str]:
    summary = facts["summary"]
    top_action = (
        facts["actions"][0]["message"]
        if facts["actions"]
        else "Seluruh kelas berada pada tingkat kehadiran sehat."
    )
    fallback = (
        f"Dari {summary['total_records']} catatan presensi untuk {summary['total_students']} "
        f"mahasiswa, rata-rata kehadiran mencapai {summary['average_attendance_rate']:.2f}%. "
        f"{summary['early_warning']}. Prioritas tindak lanjut: {top_action}."
    )

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or os.getenv("DISABLE_AI_EXTERNAL", "0") == "1":
        return fallback, "deterministic"

    prompt = (
        "Tuliskan ringkasan analitik presensi mahasiswa 2 kalimat dalam Bahasa Indonesia "
        "berdasarkan fakta JSON berikut tanpa mengubah satu pun angka:\n"
        + json.dumps(facts, ensure_ascii=False)
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={api_key}"
    )
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
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


def analyze_attendance_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Step 3: Analisis data presensi (JSON array) -> structured JSON (`insights` + `actions`).

    Tugas yang dihitung secara 100% deterministik:
    1. Hitung persentase kehadiran per mahasiswa.
    2. Identifikasi mahasiswa dengan presensi < 75% (warning / early warning).
    3. Deteksi pattern: hari/jam dengan absen rate tinggi (baik per mahasiswa maupun per kelas).
    4. Generate recommendation terstruktur (`insights` + `actions`).
    """
    if not records:
        return {
            "summary": {
                "total_records": 0,
                "total_students": 0,
                "average_attendance_rate": 0.0,
                "at_risk_count": 0,
                "early_warning": "Belum ada data presensi untuk dianalisis",
            },
            "student_attendance": [],
            "at_risk_students": [],
            "patterns": [],
            "insights": ["Belum ada catatan presensi yang tersimpan."],
            "actions": [],
            "narrative": "Belum ada data presensi untuk dianalisis.",
            "ai_engine": "deterministic",
        }

    student_stats: dict[str, dict[str, Any]] = {}
    # (nim, hari, period) -> [absent, total]
    student_day_counts: dict[tuple[str, str, str], list[int]] = defaultdict(
        lambda: [0, 0]
    )
    # (kelas_label, hari, jam) -> [absent, total]
    class_slot_counts: dict[tuple[str, str, str], list[int]] = defaultdict(
        lambda: [0, 0]
    )
    # (hari, jam) -> [absent, total]
    overall_slot_counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])

    for idx, rec in enumerate(records):
        nim = str(rec.get("nim") or "").strip()
        if not nim:
            raise AttendanceValidationError(
                f"Record indeks {idx} tidak memiliki field 'nim'"
            )
        nama = str(
            rec.get("nama_mahasiswa") or rec.get("nama") or nim
        ).strip()
        kelas_label = str(
            rec.get("kelas")
            or rec.get("kode_mk")
            or rec.get("course")
            or "Kelas Umum"
        ).strip()
        hari = _normalize_day(rec)
        jam = _normalize_time_slot(rec)
        period = _time_period_label(jam)
        status = _normalize_status(rec.get("status"))

        st = student_stats.setdefault(
            nim,
            {
                "nim": nim,
                "nama_mahasiswa": nama,
                "total_sessions": 0,
                "hadir": 0,
                "terlambat": 0,
                "izin": 0,
                "alpha": 0,
            },
        )
        if st["nama_mahasiswa"] == nim and nama != nim:
            st["nama_mahasiswa"] = nama

        st["total_sessions"] += 1
        st[status] += 1

        is_absent = 1 if status == "alpha" else 0
        student_day_counts[(nim, hari, period)][0] += is_absent
        student_day_counts[(nim, hari, period)][1] += 1

        class_slot_counts[(kelas_label, hari, jam)][0] += is_absent
        class_slot_counts[(kelas_label, hari, jam)][1] += 1

        overall_slot_counts[(hari, jam)][0] += is_absent
        overall_slot_counts[(hari, jam)][1] += 1

    # 1 & 2. Persentase kehadiran per mahasiswa & identifikasi < 75%
    student_attendance: list[dict[str, Any]] = []
    at_risk_students: list[dict[str, Any]] = []

    for nim in sorted(student_stats.keys()):
        st = student_stats[nim]
        attended = st["hadir"] + st["terlambat"]
        total = st["total_sessions"]
        pct = round((attended / total) * 100.0, 2) if total > 0 else 0.0
        warning = total > 0 and pct < ATTENDANCE_WARNING_THRESHOLD
        item = {
            **st,
            "attended_count": attended,
            "attendance_percentage": pct,
            "warning": warning,
        }
        student_attendance.append(item)
        if warning:
            at_risk_students.append(item)

    at_risk_students.sort(key=lambda x: (x["attendance_percentage"], x["nim"]))
    avg_rate = round(
        sum(s["attendance_percentage"] for s in student_attendance)
        / len(student_attendance),
        2,
    )

    at_risk_count = len(at_risk_students)
    if at_risk_count > 0:
        early_warning = (
            f"{at_risk_count} mahasiswa berisiko tidak memenuhi syarat presensi (<75%)"
        )
    else:
        early_warning = "0 mahasiswa berisiko: seluruh mahasiswa memenuhi syarat presensi (>=75%)"

    patterns: list[dict[str, Any]] = []
    insights: list[str] = [early_warning]
    actions: list[dict[str, Any]] = []

    # 3A. Deteksi pola per mahasiswa: hari/waktu sering absent
    seen_student_actions: set[str] = set()
    for (nim, hari, period), (absent_cnt, total_cnt) in sorted(
        student_day_counts.items(),
        key=lambda kv: (-(kv[1][0] / kv[1][1]), -kv[1][0], kv[0]),
    ):
        if hari == "Tidak Diketahui" or absent_cnt < 2:
            continue
        absent_rate = round((absent_cnt / total_cnt) * 100.0, 2)
        if absent_rate < HIGH_STUDENT_DAY_ABSENT_RATE_PCT:
            continue

        nama = student_stats[nim]["nama_mahasiswa"]
        slot_desc = f"{hari} {period}".strip()
        pattern_msg = (
            f"Mahasiswa {nama} ({nim}) sering absent {slot_desc} "
            f"({absent_cnt}/{total_cnt} sesi, absen rate {absent_rate:.1f}%)"
        )
        patterns.append({
            "type": "student_day_pattern",
            "target": nim,
            "label": slot_desc,
            "absent_count": absent_cnt,
            "total_count": total_cnt,
            "absent_rate": absent_rate,
            "description": pattern_msg,
        })
        insights.append(pattern_msg)
        actions.append({
            "type": "counseling",
            "target": nim,
            "priority": "high" if student_stats[nim]["alpha"] >= 2 else "medium",
            "message": f"Mahasiswa {nama} ({nim}) sering absent {slot_desc} -> hubungi untuk konseling",
        })
        seen_student_actions.add(nim)

    # Tambahkan aksi konseling bagi mahasiswa berisiko (<75%) yang belum punya pola hari spesifik
    for st in at_risk_students:
        if st["nim"] not in seen_student_actions:
            actions.append({
                "type": "early_warning_counseling",
                "target": st["nim"],
                "priority": "high",
                "message": (
                    f"Mahasiswa {st['nama_mahasiswa']} ({st['nim']}) memiliki presensi "
                    f"{st['attendance_percentage']:.1f}% (<75%) -> hubungi untuk konseling akademik"
                ),
            })

    # 3B. Deteksi pola per kelas & jam dengan absen rate tinggi
    for (kelas_label, hari, jam), (absent_cnt, total_cnt) in sorted(
        class_slot_counts.items(),
        key=lambda kv: (-(kv[1][0] / kv[1][1]), -kv[1][0], kv[0]),
    ):
        if total_cnt < 2 or absent_cnt == 0:
            continue
        absent_rate = round((absent_cnt / total_cnt) * 100.0, 2)
        if absent_rate < HIGH_CLASS_ABSENT_RATE_PCT:
            continue

        time_desc = f"di jam {jam}" if jam != "Tidak Diketahui" else f"pada hari {hari}"
        day_prefix = f" ({hari})" if hari != "Tidak Diketahui" else ""
        pattern_msg = (
            f"Kelas {kelas_label}{day_prefix} {time_desc} memiliki absen rate "
            f"{absent_rate:.1f}% ({absent_cnt}/{total_cnt} catatan)"
        )
        patterns.append({
            "type": "class_time_pattern",
            "target": kelas_label,
            "label": f"{hari} {jam}".strip(),
            "absent_count": absent_cnt,
            "total_count": total_cnt,
            "absent_rate": absent_rate,
            "description": pattern_msg,
        })
        insights.append(pattern_msg)
        actions.append({
            "type": "reschedule_review",
            "target": kelas_label,
            "priority": "high" if absent_rate >= 40.0 else "medium",
            "message": (
                f"Kelas {kelas_label} {time_desc} absen rate {absent_rate:.1f}% "
                f"-> pertimbangkan reschedule"
            ),
        })

    # 3C. Highlight hari/jam dengan absen tertinggi secara keseluruhan
    if overall_slot_counts:
        worst_slot, (w_abs, w_tot) = max(
            overall_slot_counts.items(),
            key=lambda kv: (kv[1][0] / kv[1][1], kv[1][0]),
        )
        if w_abs > 0:
            worst_rate = round((w_abs / w_tot) * 100.0, 2)
            slot_label = f"{worst_slot[0]} {worst_slot[1]}".strip()
            insights.append(
                f"Slot waktu dengan tingkat absen tertinggi: {slot_label} "
                f"(absen rate {worst_rate:.1f}%)"
            )

    facts = {
        "summary": {
            "total_records": len(records),
            "total_students": len(student_attendance),
            "average_attendance_rate": avg_rate,
            "at_risk_count": at_risk_count,
            "early_warning": early_warning,
        },
        "student_attendance": student_attendance,
        "at_risk_students": at_risk_students,
        "patterns": patterns,
        "insights": insights,
        "actions": actions,
    }
    narrative, engine = _optional_gemini_narrative(facts)
    return {
        **facts,
        "narrative": narrative,
        "ai_engine": engine,
    }


def analyze_attendance_from_db(
    db: Session,
    *,
    kode_mk: str | None = None,
    semester: str | None = None,
) -> dict[str, Any]:
    """Mengambil data presensi dari DB (termasuk sesi terlewat sebagai alpha) lalu menganalisisnya."""
    sess_stmt = (
        select(AttendanceSession)
        .join(Jadwal, Jadwal.id == AttendanceSession.jadwal_id)
        .join(Kelas, Kelas.id == Jadwal.kelas_id)
        .options(
            joinedload(AttendanceSession.jadwal)
            .joinedload(Jadwal.kelas)
            .joinedload(Kelas.matakuliah),
            joinedload(AttendanceSession.records).joinedload(AttendanceRecord.mahasiswa),
        )
        .order_by(AttendanceSession.session_date, AttendanceSession.id)
    )
    if kode_mk:
        sess_stmt = sess_stmt.where(Kelas.kode_mk == kode_mk.strip().upper())
    if semester:
        sess_stmt = sess_stmt.where(Kelas.semester == semester)

    sessions = db.scalars(sess_stmt).unique().all()
    if not sessions:
        return analyze_attendance_records([])

    # Peta mahasiswa terdaftar per (kode_mk, semester, kelas_id)
    krs_rows = db.execute(
        select(
            KRS.nim,
            KRS.semester,
            KRSDetail.kode_mk,
            KRSDetail.kelas_id,
            CalonMahasiswa.nama_lengkap,
        )
        .join(KRSDetail, KRSDetail.krs_id == KRS.id)
        .join(CalonMahasiswa, CalonMahasiswa.nim == KRS.nim)
    ).all()

    records_payload: list[dict[str, Any]] = []
    for s in sessions:
        jadwal = s.jadwal
        kelas = jadwal.kelas
        kelas_label = f"{kelas.kode_mk}-{kelas.nama_kelas} ({kelas.matakuliah.nama})"
        hari_str = HARI_NAMA.get(jadwal.hari, str(jadwal.hari))
        jam_str = jadwal.jam_mulai.strftime("%H:%M")

        rec_by_nim = {r.nim: r for r in s.records}
        enrolled_for_session: dict[str, str] = {}
        for nim_val, sem_val, mk_val, det_kid, nama_mhs in krs_rows:
            if sem_val == kelas.semester and mk_val == kelas.kode_mk:
                if det_kid is None or det_kid == kelas.id:
                    enrolled_for_session[nim_val] = nama_mhs
        for r in s.records:
            if r.nim not in enrolled_for_session:
                enrolled_for_session[r.nim] = (
                    r.mahasiswa.nama_lengkap if r.mahasiswa else r.nim
                )

        for nim_val, nama_mhs in sorted(enrolled_for_session.items()):
            rec = rec_by_nim.get(nim_val)
            status_val = rec.status.value if rec is not None else AttendanceStatus.ALPHA.value
            records_payload.append({
                "nim": nim_val,
                "nama_mahasiswa": nama_mhs,
                "kode_mk": kelas.kode_mk,
                "kelas": kelas_label,
                "tanggal": s.session_date.isoformat(),
                "hari": hari_str,
                "jam_mulai": jam_str,
                "status": status_val,
            })

    return analyze_attendance_records(records_payload)

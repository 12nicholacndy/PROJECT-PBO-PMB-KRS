from datetime import date, datetime, time, timedelta, timezone
from typing import Sequence

from app.models import Jadwal

WIB_OFFSET = timezone(timedelta(hours=7))
CRLF = "\r\n"


def _escape_ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _first_occurrence_date(semester_start: date, hari: int) -> date:
    """
    Menghitung tanggal pertemuan pertama pada/sesudah `semester_start`
    untuk `hari` ISO (1=Senin .. 6=Sabtu).
    """
    delta_days = (hari - semester_start.isoweekday()) % 7
    return semester_start + timedelta(days=delta_days)


def _until_utc_string(semester_end: date) -> str:
    """
    RFC 5545: jika DTSTART memakai zona waktu (TZID), nilai UNTIL pada RRULE
    WAJIB ditulis dalam UTC berakhiran 'Z'.
    """
    end_local = datetime.combine(semester_end, time(23, 59, 59), tzinfo=WIB_OFFSET)
    end_utc = end_local.astimezone(timezone.utc)
    return end_utc.strftime("%Y%m%dT%H%M%SZ")


def export_jadwals_to_ics(
    jadwals: Sequence[Jadwal],
    *,
    semester_start: date = date(2026, 9, 1),
    semester_end: date = date(2026, 12, 20),
    tzid: str = "Asia/Jakarta",
    cal_name: str = "Jadwal Kuliah SEVIMA Mini",
) -> str:
    """
    Mengekspor daftar jadwal ke format iCalendar (.ics, RFC 5545):
    - Pemisah baris wajib CRLF (\\r\\n).
    - UID stabil (`jadwal-{id}@sevima-mini`) agar impor ulang memperbarui event lama.
    - RRULE mingguan dengan UNTIL berformat UTC ('Z').
    - SEQUENCE mengikuti `jadwal.sequence` yang naik setiap jadwal diperbarui.
    """
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    until_utc = _until_utc_string(semester_end)

    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SEVIMA Mini//Modul Jadwal PBO//ID",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape_ics_text(cal_name)}",
        f"X-WR-TIMEZONE:{tzid}",
    ]

    for jadwal in jadwals:
        first_date = _first_occurrence_date(semester_start, jadwal.hari)
        dtstart = datetime.combine(first_date, jadwal.jam_mulai).strftime(
            "%Y%m%dT%H%M%S"
        )
        dtend = datetime.combine(first_date, jadwal.jam_selesai).strftime(
            "%Y%m%dT%H%M%S"
        )

        kelas = getattr(jadwal, "kelas", None)
        mk = getattr(kelas, "matakuliah", None) if kelas is not None else None
        dosen = getattr(jadwal, "dosen", None)

        kode_mk = getattr(kelas, "kode_mk", f"KELAS-{jadwal.kelas_id}")
        nama_kelas = getattr(kelas, "nama_kelas", "")
        nama_mk = getattr(mk, "nama", "")
        summary_parts = [f"{kode_mk}-{nama_kelas}" if nama_kelas else str(kode_mk)]
        if nama_mk:
            summary_parts.append(nama_mk)
        summary = " ".join(summary_parts)

        dosen_nama = getattr(dosen, "nama", f"Dosen ID {jadwal.dosen_id}")
        description = (
            f"Kelas: {kode_mk}-{nama_kelas} | "
            f"Dosen: {dosen_nama} | Ruangan: {jadwal.ruangan_kode}"
        )

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:jadwal-{jadwal.id}@sevima-mini",
                f"DTSTAMP:{dtstamp}",
                f"SEQUENCE:{getattr(jadwal, 'sequence', 0)}",
                f"SUMMARY:{_escape_ics_text(summary)}",
                f"LOCATION:{_escape_ics_text(jadwal.ruangan_kode)}",
                f"DESCRIPTION:{_escape_ics_text(description)}",
                f"DTSTART;TZID={tzid}:{dtstart}",
                f"DTEND;TZID={tzid}:{dtend}",
                f"RRULE:FREQ=WEEKLY;UNTIL={until_utc}",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")
    return CRLF.join(lines) + CRLF

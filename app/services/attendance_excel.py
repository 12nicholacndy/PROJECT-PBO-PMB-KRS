from __future__ import annotations

from datetime import datetime
import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


def export_course_attendance_excel(report: dict[str, Any]) -> bytes:
    """Menghasilkan file Excel (.xlsx) rekap presensi per mata kuliah untuk dosen.

    Terdiri dari 3 worksheet:
    1. `Rekap Presensi`: seluruh mahasiswa beserta total sesi, hadir, terlambat, alpha, persentase, & status.
    2. `Warning (<75%)`: daftar khusus mahasiswa berisiko tidak memenuhi syarat presensi.
    3. `Log Presensi Detail`: riwayat scan per pertemuan.
    """
    wb = Workbook()

    header_fill = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    title_font = Font(name="Calibri", size=14, bold=True, color="1E3A5F")
    subtitle_font = Font(name="Calibri", size=10, italic=True, color="4B5563")
    warn_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    warn_font = Font(name="Calibri", size=11, bold=True, color="B91C1C")
    ok_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    ok_font = Font(name="Calibri", size=11, bold=True, color="166534")
    thin_side = Side(style="thin", color="CBD5E1")
    cell_border = Border(
        left=thin_side, right=thin_side, top=thin_side, bottom=thin_side
    )

    # --- Sheet 1: Rekap Presensi ---
    ws1 = wb.active
    ws1.title = "Rekap Presensi"
    ws1["A1"] = f"LAPORAN PRESENSI MATA KULIAH: {report['kode_mk']} - {report['nama_mk']}"
    ws1["A1"].font = title_font
    ws1["A2"] = (
        f"Semester: {report.get('semester') or '-'} | "
        f"Total Sesi: {report['total_sessions']} | "
        f"Rata-rata Kehadiran: {report['average_attendance_percentage']:.2f}% | "
        f"Warning (<75%): {report['warning_count']} mahasiswa | "
        f"Dicetak: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    ws1["A2"].font = subtitle_font

    headers = [
        "No",
        "NIM",
        "Nama Mahasiswa",
        "Kelas",
        "Total Sesi",
        "Hadir",
        "Terlambat",
        "Izin",
        "Alpha",
        "Persentase (%)",
        "Status",
    ]
    start_row = 4
    for col_idx, text in enumerate(headers, start=1):
        cell = ws1.cell(row=start_row, column=col_idx, value=text)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = cell_border

    for idx, student in enumerate(report["students"], start=1):
        row_num = start_row + idx
        status_label = "WARNING (<75%)" if student["warning"] else "AMAN"
        values = [
            idx,
            student["nim"],
            student["nama_mahasiswa"],
            student["nama_kelas"],
            student["total_sessions"],
            student["hadir"],
            student["terlambat"],
            student["izin"],
            student["alpha"],
            student["attendance_percentage"],
            status_label,
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws1.cell(row=row_num, column=col_idx, value=val)
            cell.border = cell_border
            if col_idx in (1, 4, 5, 6, 7, 8, 9, 10, 11):
                cell.alignment = Alignment(horizontal="center")
            if col_idx == 10:
                cell.number_format = "0.00"
            if col_idx == 11:
                if student["warning"]:
                    cell.fill = warn_fill
                    cell.font = warn_font
                else:
                    cell.fill = ok_fill
                    cell.font = ok_font

    _autofit_columns(ws1, min_width=10, max_width=36)

    # --- Sheet 2: Warning (<75%) ---
    ws2 = wb.create_sheet(title="Warning (<75%)")
    ws2["A1"] = f"DAFTAR MAHASISWA BERISIKO (<75%) - {report['kode_mk']}"
    ws2["A1"].font = title_font
    warn_headers = [
        "No",
        "NIM",
        "Nama Mahasiswa",
        "Kelas",
        "Hadir+Terlambat",
        "Total Sesi",
        "Persentase (%)",
        "Rekomendasi",
    ]
    for col_idx, text in enumerate(warn_headers, start=1):
        cell = ws2.cell(row=3, column=col_idx, value=text)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")
        cell.border = cell_border

    for idx, student in enumerate(report["warning_students"], start=1):
        row_num = 3 + idx
        values = [
            idx,
            student["nim"],
            student["nama_mahasiswa"],
            student["nama_kelas"],
            student["attended_count"],
            student["total_sessions"],
            student["attendance_percentage"],
            "Hubungi mahasiswa untuk konseling & peringatan syarat ujian (<75%)",
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws2.cell(row=row_num, column=col_idx, value=val)
            cell.border = cell_border
            if col_idx == 7:
                cell.number_format = "0.00"
                cell.fill = warn_fill
                cell.font = warn_font

    _autofit_columns(ws2, min_width=12, max_width=48)

    # --- Sheet 3: Log Presensi Detail ---
    ws3 = wb.create_sheet(title="Log Presensi Detail")
    ws3["A1"] = f"LOG SCAN PRESENSI - {report['kode_mk']}"
    ws3["A1"].font = title_font
    log_headers = [
        "No",
        "Tanggal",
        "Pertemuan",
        "Kelas",
        "NIM",
        "Nama Mahasiswa",
        "Status",
        "Waktu Scan",
        "Jarak Lokasi (m)",
    ]
    for col_idx, text in enumerate(log_headers, start=1):
        cell = ws3.cell(row=3, column=col_idx, value=text)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")
        cell.border = cell_border

    for idx, log in enumerate(report.get("detailed_logs", []), start=1):
        row_num = 3 + idx
        values = [
            idx,
            log["session_date"],
            log["pertemuan_ke"],
            log["nama_kelas"],
            log["nim"],
            log["nama_mahasiswa"],
            log["status"].upper(),
            log["scanned_at"],
            log["distance_meters"] if log["distance_meters"] is not None else "-",
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws3.cell(row=row_num, column=col_idx, value=val)
            cell.border = cell_border

    _autofit_columns(ws3, min_width=12, max_width=32)

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def _autofit_columns(ws: Any, *, min_width: int = 10, max_width: int = 40) -> None:
    for col_idx in range(1, ws.max_column + 1):
        col_letter = get_column_letter(col_idx)
        max_len = min_width
        for row_idx in range(3, ws.max_row + 1):
            val = ws.cell(row=row_idx, column=col_idx).value
            if val is not None:
                max_len = max(max_len, len(str(val)) + 3)
        ws.column_dimensions[col_letter].width = min(max_width, max_len)

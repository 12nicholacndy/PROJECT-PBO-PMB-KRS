from io import BytesIO
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.gpa_calculator import TranscriptData

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

NAVY = colors.HexColor("#1a2b4c")
GOLD = colors.HexColor("#b8943f")
CREAM = colors.HexColor("#faf7ee")
LIGHT_BG = colors.HexColor("#f2f4f8")
BORDER = colors.HexColor("#cbd2dc")
MUTED = colors.HexColor("#475467")
DANGER = colors.HexColor("#9f1239")
DANGER_BG = colors.HexColor("#fff5f5")


def render_transcript_html(transcript: TranscriptData) -> str:
    """Merender template HTML dinamis transkrip akademik dengan CSS profesional."""
    template = _jinja_env.get_template("transcript.html")
    return template.render(transcript=transcript)


def _draw_certificate_chrome(canvas: Canvas, doc) -> None:
    """Menggambar bingkai ganda bergaya ijazah resmi dan nomor halaman di setiap halaman PDF."""
    width, height = A4
    canvas.saveState()

    # Outer navy border & inner gold border
    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(2.4)
    canvas.rect(10 * mm, 10 * mm, width - 20 * mm, height - 20 * mm)

    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(0.9)
    canvas.rect(12.5 * mm, 12.5 * mm, width - 25 * mm, height - 25 * mm)

    # Subtle page footer line
    canvas.setFont("Times-Italic", 8)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(
        width - 16 * mm,
        14.5 * mm,
        f"Dokumen Resmi Universitas Teknologi Nusantara — Halaman {doc.page}",
    )
    canvas.restoreState()


def _draw_logo_on_first_page(canvas: Canvas, doc) -> None:
    """Menggambar bingkai + emblem logo universitas di header halaman pertama."""
    _draw_certificate_chrome(canvas, doc)
    _, height = A4
    canvas.saveState()

    cx = 28 * mm
    cy = height - 29 * mm
    r_outer = 10.5 * mm
    r_inner = 8.8 * mm

    canvas.setFillColor(NAVY)
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(1.8)
    canvas.circle(cx, cy, r_outer, stroke=1, fill=1)

    canvas.setStrokeColor(colors.HexColor("#e5c87b"))
    canvas.setLineWidth(0.8)
    canvas.circle(cx, cy, r_inner, stroke=1, fill=0)

    # Diamond emblem inside crest
    path = canvas.beginPath()
    path.moveTo(cx, cy + 6.5 * mm)
    path.lineTo(cx + 4.8 * mm, cy + 1.5 * mm)
    path.lineTo(cx, cy - 6.8 * mm)
    path.lineTo(cx - 4.8 * mm, cy + 1.5 * mm)
    path.close()
    canvas.setFillColor(GOLD)
    canvas.drawPath(path, stroke=0, fill=1)

    canvas.setFillColor(colors.white)
    canvas.setFont("Times-Bold", 7.5)
    canvas.drawCentredString(cx, cy - 1.2 * mm, "UTN")

    canvas.restoreState()


def generate_transcript_pdf(transcript: TranscriptData) -> bytes:
    """
    Menghasilkan file PDF Transkrip Akademik resmi menggunakan ReportLab.

    Layout sesuai spesifikasi tugas:
    - Header: Logo universitas, judul 'TRANSKRIP AKADEMIK'
    - Biodata: NIM, Nama, Program Studi, Angkatan
    - Per semester: tabel [Kode MK | Nama MK | SKS | Nilai | Mutu]
    - Footer: Total SKS, IPK, Predikat, TTD Dekan
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=20 * mm,
        title=f"Transkrip Akademik - {transcript.biodata.nim}",
        author="Universitas Teknologi Nusantara",
    )

    styles = getSampleStyleSheet()
    univ_style = ParagraphStyle(
        "UnivTitle",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=13,
        leading=16,
        alignment=TA_CENTER,
        textColor=NAVY,
    )
    faculty_style = ParagraphStyle(
        "FacultyTitle",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=9.5,
        leading=12,
        alignment=TA_CENTER,
        textColor=MUTED,
    )
    doc_title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=15,
        leading=19,
        alignment=TA_CENTER,
        textColor=GOLD,
        spaceBefore=3,
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Times-Italic",
        fontSize=8.5,
        leading=11,
        alignment=TA_CENTER,
        textColor=MUTED,
    )
    cell_style = ParagraphStyle(
        "Cell",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=9,
        leading=11.5,
        textColor=colors.HexColor("#182230"),
    )
    cell_center = ParagraphStyle("CellCenter", parent=cell_style, alignment=TA_CENTER)
    cell_right = ParagraphStyle("CellRight", parent=cell_style, alignment=TA_RIGHT)
    cell_bold = ParagraphStyle(
        "CellBold",
        parent=cell_style,
        fontName="Times-Bold",
        textColor=NAVY,
    )
    sem_header_style = ParagraphStyle(
        "SemHeader",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=9.5,
        leading=12,
        textColor=colors.white,
    )
    sem_header_right = ParagraphStyle(
        "SemHeaderRight",
        parent=sem_header_style,
        alignment=TA_RIGHT,
    )
    note_style = ParagraphStyle(
        "Note",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=8.2,
        leading=11,
        textColor=MUTED,
        alignment=TA_LEFT,
    )
    sign_style = ParagraphStyle(
        "Signature",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#182230"),
    )

    story: list = []

    # 1. Header (diberi ruang kiri agar seimbang dengan logo universitas di canvas)
    header_table = Table(
        [
            [
                "",
                [
                    Paragraph("UNIVERSITAS TEKNOLOGI NUSANTARA", univ_style),
                    Paragraph(transcript.biodata.fakultas, faculty_style),
                    Paragraph("TRANSKRIP AKADEMIK", doc_title_style),
                    Paragraph(
                        "Academic Transcript — Sistem Informasi Akademik",
                        subtitle_style,
                    ),
                ],
                "",
            ]
        ],
        colWidths=[22 * mm, 130 * mm, 22 * mm],
    )
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, 0), 1.6, NAVY),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 5 * mm))

    # 2. Biodata Mahasiswa
    biodata_rows = [
        [
            Paragraph("<b>NIM</b>", cell_bold),
            Paragraph(f": {transcript.biodata.nim}", cell_style),
            Paragraph("<b>Program Studi</b>", cell_bold),
            Paragraph(f": {transcript.biodata.program_studi}", cell_style),
        ],
        [
            Paragraph("<b>Nama Lengkap</b>", cell_bold),
            Paragraph(f": {transcript.biodata.nama}", cell_style),
            Paragraph("<b>Angkatan</b>", cell_bold),
            Paragraph(f": {transcript.biodata.angkatan}", cell_style),
        ],
    ]
    biodata_table = Table(
        biodata_rows,
        colWidths=[27 * mm, 60 * mm, 28 * mm, 59 * mm],
    )
    biodata_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CREAM),
                ("BOX", (0, 0), (-1, -1), 0.75, GOLD),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(biodata_table)
    story.append(Spacer(1, 5 * mm))

    # 3. Tabel Nilai Per Semester: [Kode MK | Nama MK | SKS | Nilai | Mutu]
    if transcript.semesters:
        for sem in transcript.semesters:
            banner = Table(
                [
                    [
                        Paragraph(
                            f"{sem.semester_label} ({sem.semester})",
                            sem_header_style,
                        ),
                        Paragraph(
                            f"SKS: {sem.total_sks}  |  IPS: {sem.ips:.2f}",
                            sem_header_right,
                        ),
                    ]
                ],
                colWidths=[114 * mm, 60 * mm],
            )
            banner.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            story.append(banner)

            table_data = [
                [
                    Paragraph("<b>Kode MK</b>", cell_center),
                    Paragraph("<b>Nama MK</b>", cell_center),
                    Paragraph("<b>SKS</b>", cell_center),
                    Paragraph("<b>Nilai</b>", cell_center),
                    Paragraph("<b>Mutu</b>", cell_center),
                ]
            ]
            row_styles = [
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]

            for idx, row in enumerate(sem.courses, start=1):
                suffix = ""
                if not row.lulus:
                    suffix = " [Tidak Lulus]"
                    row_styles.append(("BACKGROUND", (0, idx), (-1, idx), DANGER_BG))
                    row_styles.append(("TEXTCOLOR", (0, idx), (-1, idx), DANGER))
                elif row.is_retake and not row.counted_in_ipk:
                    suffix = " [Diulang]"

                table_data.append(
                    [
                        Paragraph(row.kode_mk, cell_center),
                        Paragraph(f"{row.nama_mk}{suffix}", cell_style),
                        Paragraph(str(row.sks), cell_center),
                        Paragraph(
                            f"<b>{row.nilai_huruf}</b> ({row.nilai_angka:.1f})",
                            cell_center,
                        ),
                        Paragraph(f"{row.mutu:.2f}", cell_right),
                    ]
                )

            sem_table = Table(
                table_data,
                colWidths=[26 * mm, 82 * mm, 16 * mm, 26 * mm, 24 * mm],
                repeatRows=1,
            )
            sem_table.setStyle(TableStyle(row_styles))
            story.append(sem_table)
            story.append(Spacer(1, 4 * mm))
    else:
        empty_table = Table(
            [
                [
                    Paragraph(
                        "Belum ada catatan nilai mata kuliah untuk mahasiswa ini.",
                        cell_center,
                    )
                ]
            ],
            colWidths=[174 * mm],
        )
        empty_table.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, GOLD),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        story.append(empty_table)
        story.append(Spacer(1, 4 * mm))

    # 4. Ringkasan: Total SKS, IPK, Predikat Kelulusan
    summary_table = Table(
        [
            [
                Paragraph("<b>TOTAL SKS LULUS</b>", cell_center),
                Paragraph("<b>INDEKS PRESTASI KUMULATIF (IPK)</b>", cell_center),
                Paragraph("<b>PREDIKAT KELULUSAN</b>", cell_center),
            ],
            [
                Paragraph(
                    f"<b>{transcript.total_sks_lulus} SKS</b>",
                    ParagraphStyle(
                        "SumVal1",
                        parent=cell_center,
                        fontName="Times-Bold",
                        fontSize=12,
                        textColor=NAVY,
                    ),
                ),
                Paragraph(
                    f"<b>{transcript.ipk:.2f}</b>",
                    ParagraphStyle(
                        "SumVal2",
                        parent=cell_center,
                        fontName="Times-Bold",
                        fontSize=12,
                        textColor=NAVY,
                    ),
                ),
                Paragraph(
                    f"<b>{transcript.predikat}</b>",
                    ParagraphStyle(
                        "SumVal3",
                        parent=cell_center,
                        fontName="Times-Bold",
                        fontSize=11.5,
                        textColor=NAVY,
                    ),
                ),
            ],
        ],
        colWidths=[54 * mm, 60 * mm, 60 * mm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CREAM),
                ("BOX", (0, 0), (-1, -1), 1.0, GOLD),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, GOLD),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(summary_table)
    story.append(Spacer(1, 8 * mm))

    # 5. Footer: Keterangan & TTD Dekan
    printed_str = transcript.generated_at.strftime("%d-%m-%Y %H:%M UTC")
    footer_table = Table(
        [
            [
                Paragraph(
                    "<b>Keterangan:</b><br/>"
                    "1. IPK dihitung dari mata kuliah yang lulus (nilai &gt;= D).<br/>"
                    "2. Mata kuliah yang diulang diambil nilai tertingginya.<br/>"
                    f"Dicetak pada: {printed_str}",
                    note_style,
                ),
                Paragraph(
                    f"Dekan {transcript.biodata.fakultas},<br/><br/><br/><br/>"
                    "<i>( TTD &amp; Stempel Dekan )</i><br/><br/>"
                    "<b><u>Prof. Dr. Hendra Wijaya, M.Kom.</u></b><br/>"
                    "NIP. 197408152001121003",
                    sign_style,
                ),
            ]
        ],
        colWidths=[102 * mm, 72 * mm],
    )
    footer_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEABOVE", (0, 0), (-1, 0), 0.6, BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(footer_table)

    doc.build(
        story,
        onFirstPage=_draw_logo_on_first_page,
        onLaterPages=_draw_certificate_chrome,
    )
    return buffer.getvalue()

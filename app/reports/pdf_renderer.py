"""Build professional PDF reports from validated inspection data."""

from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from PIL import Image, ImageDraw, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import (
    Image as PdfImage,
)

from app.ai.schemas import BBox, InspectionReportText, InspectionResult, ReportUserType

PAGE_MARGIN = 0.62 * inch
PHOTO_WIDTH = 3.05 * inch
PHOTO_HEIGHT = 2.3 * inch
IMAGE_JPEG_QUALITY = 90
ANNOTATION_COLOR = (190, 48, 37)
ANNOTATION_WIDTH = 5
INK = colors.HexColor("#203b2e")
MUTED = colors.HexColor("#68766b")
LINE = colors.HexColor("#dce4da")
PALE_GREEN = colors.HexColor("#eef3e7")
WARNING = colors.HexColor("#fff3d4")
USER_TYPE_LABELS = {
    ReportUserType.FARMER: "Farmer",
    ReportUserType.QC_OPERATOR: "QC operator",
}
FONT_DIRECTORY = Path(reportlab.__file__).parent / "fonts"
REGULAR_FONT_NAME = "FieldmarkVera"
BOLD_FONT_NAME = "FieldmarkVeraBold"
ITALIC_FONT_NAME = "FieldmarkVeraItalic"
pdfmetrics.registerFont(TTFont(REGULAR_FONT_NAME, str(FONT_DIRECTORY / "Vera.ttf")))
pdfmetrics.registerFont(TTFont(BOLD_FONT_NAME, str(FONT_DIRECTORY / "VeraBd.ttf")))
pdfmetrics.registerFont(TTFont(ITALIC_FONT_NAME, str(FONT_DIRECTORY / "VeraIt.ttf")))


def build_inspection_pdf(
    report_id: str,
    report_date: str,
    inspection_date: str,
    user_type: ReportUserType,
    result: InspectionResult,
    report_text: InspectionReportText,
    transcript: str,
    original_image: bytes,
) -> bytes:
    """Render a validated inspection narrative, evidence, and annotated images as a PDF."""
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=PAGE_MARGIN,
        leftMargin=PAGE_MARGIN,
        topMargin=0.58 * inch,
        bottomMargin=0.65 * inch,
        title=f"Inspection report {report_id}",
        author="Fieldmark",
    )
    styles = _styles()
    story = _story(
        styles,
        report_id,
        report_date,
        inspection_date,
        user_type,
        result,
        report_text,
        transcript,
        original_image,
    )
    document.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buffer.getvalue()


def annotate_photo(image_data: bytes, bbox: BBox | None) -> bytes:
    """Return an orientation-corrected JPEG with the model's normalized box."""
    with Image.open(BytesIO(image_data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    if bbox is not None:
        _draw_bbox(image, bbox)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=IMAGE_JPEG_QUALITY)
    return buffer.getvalue()


def _styles() -> dict[str, ParagraphStyle]:
    """Create reusable report typography styles."""
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle(
            "ReportBrand",
            parent=base["Normal"],
            fontName=BOLD_FONT_NAME,
            fontSize=9,
            textColor=MUTED,
            spaceAfter=5,
        ),
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName=BOLD_FONT_NAME,
            fontSize=22,
            leading=26,
            textColor=INK,
            alignment=TA_CENTER,
            spaceAfter=14,
        ),
        "section": ParagraphStyle(
            "ReportSection",
            parent=base["Heading2"],
            fontName=BOLD_FONT_NAME,
            fontSize=12,
            leading=15,
            textColor=INK,
            spaceBefore=13,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["BodyText"],
            fontName=REGULAR_FONT_NAME,
            fontSize=9.5,
            leading=14,
            textColor=INK,
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "ReportSmall",
            parent=base["BodyText"],
            fontName=REGULAR_FONT_NAME,
            fontSize=8,
            leading=11,
            textColor=MUTED,
        ),
        "table": ParagraphStyle(
            "ReportTable",
            parent=base["BodyText"],
            fontName=REGULAR_FONT_NAME,
            fontSize=8.5,
            leading=12,
            textColor=INK,
        ),
    }


def _story(
    styles: dict[str, ParagraphStyle],
    report_id: str,
    report_date: str,
    inspection_date: str,
    user_type: ReportUserType,
    result: InspectionResult,
    report_text: InspectionReportText,
    transcript: str,
    original_image: bytes,
) -> list[Flowable]:
    """Assemble all PDF sections in reading order."""
    story: list[Flowable] = [
        _paragraph("FIELDMARK  /  PRODUCE QUALITY INSPECTION", styles["brand"]),
        _paragraph("Inspection report", styles["title"]),
        _metadata_table(styles, report_id, report_date, inspection_date, user_type),
    ]
    if _needs_manual_review(result):
        story.append(_manual_review_panel(styles))
    story.extend(
        [
            _paragraph("Plain-language summary", styles["section"]),
            _paragraph(report_text.summary, styles["body"]),
            _paragraph("Photo evidence", styles["section"]),
            _photo_table(styles, original_image, result.bbox),
            _paragraph("Inspection findings", styles["section"]),
        ]
    )
    story.extend(_paragraph(f"- {finding}", styles["body"]) for finding in report_text.findings)
    story.append(_details_table(styles, result))
    _append_voice_note(story, styles, transcript, report_text)
    _append_recommendations(story, styles, result, report_text)
    story.append(Spacer(1, 8))
    story.append(_paragraph(result.disclaimer, styles["small"]))
    return story


def _metadata_table(
    styles: dict[str, ParagraphStyle],
    report_id: str,
    report_date: str,
    inspection_date: str,
    user_type: ReportUserType,
) -> Table:
    """Create a compact report and inspection metadata panel."""
    rows = [
        [_cell(styles, "Report ID", True), _cell(styles, report_id)],
        [_cell(styles, "Report date and time", True), _cell(styles, _format_date(report_date))],
        [
            _cell(styles, "Inspection date and time", True),
            _cell(styles, _format_date(inspection_date)),
        ],
        [_cell(styles, "User type", True), _cell(styles, USER_TYPE_LABELS[user_type])],
    ]
    table = Table(rows, colWidths=[1.55 * inch, 4.9 * inch], hAlign="LEFT")
    table.setStyle(_table_style(PALE_GREEN))
    return table


def _photo_table(
    styles: dict[str, ParagraphStyle],
    original_image: bytes,
    bbox: BBox | None,
) -> Table:
    """Place original and annotated photo previews side by side."""
    annotated_image = annotate_photo(original_image, bbox)
    original = _pdf_image(original_image)
    annotated = _pdf_image(annotated_image)
    caption = "Model-reported box" if bbox is not None else "No model location was provided"
    rows = [
        [original, annotated],
        [
            _paragraph("Original photo", styles["small"]),
            _paragraph(f"Annotated photo - {caption}", styles["small"]),
        ],
    ]
    table = Table(rows, colWidths=[3.2 * inch, 3.2 * inch], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _details_table(styles: dict[str, ParagraphStyle], result: InspectionResult) -> Table:
    """Render exact model fields without deriving new findings."""
    confidence = "Unavailable" if result.confidence is None else f"{result.confidence:.0%}"
    defect_types = ", ".join(item.value for item in result.defect_types) or "None reported"
    verdict = (
        "Needs manual review" if _needs_manual_review(result) else result.verdict.replace("_", " ")
    )
    rows = [
        [_cell(styles, "Verdict", True), _cell(styles, verdict)],
        [_cell(styles, "Confidence", True), _cell(styles, confidence)],
        [_cell(styles, "Defect type", True), _cell(styles, defect_types)],
        [_cell(styles, "Severity", True), _cell(styles, result.severity.value)],
        [_cell(styles, "Produce", True), _cell(styles, result.produce_type.value)],
    ]
    table = Table(rows, colWidths=[1.55 * inch, 4.9 * inch], hAlign="LEFT")
    table.setStyle(_table_style(colors.white))
    return table


def _manual_review_panel(styles: dict[str, ParagraphStyle]) -> Table:
    """Make uncertain results prominent without stating possible defects as fact."""
    content = _paragraph(
        "NEEDS MANUAL REVIEW\n"
        "This visual result is uncertain. Possible defects are not confirmed; inspect the produce "
        "manually before acting.",
        styles["body"],
    )
    table = Table([[content]], colWidths=[6.45 * inch], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), WARNING),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d5a84c")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _append_voice_note(
    story: list[Flowable],
    styles: dict[str, ParagraphStyle],
    transcript: str,
    report_text: InspectionReportText,
) -> None:
    """Include the exact transcript and any AI-identified contradiction."""
    story.append(_paragraph("Voice note", styles["section"]))
    transcript_text = transcript if transcript else "No voice note was provided."
    story.append(_paragraph(transcript_text, styles["body"]))
    if report_text.voice_note_conflict:
        story.append(
            _paragraph(f"Difference noted: {report_text.voice_note_conflict}", styles["body"])
        )


def _append_recommendations(
    story: list[Flowable],
    styles: dict[str, ParagraphStyle],
    result: InspectionResult,
    report_text: InspectionReportText,
) -> None:
    """Include the deterministic action and validated AI recommendations."""
    story.append(_paragraph("Recommended next steps", styles["section"]))
    recommendations = [result.recommended_action, *report_text.recommendations]
    unique_recommendations = list(dict.fromkeys(recommendations))
    for recommendation in unique_recommendations:
        story.append(_paragraph(f"- {recommendation}", styles["body"]))


def _pdf_image(image_data: bytes) -> PdfImage:
    """Create a bounded-size image flowable while preserving aspect ratio."""
    return PdfImage(
        BytesIO(image_data),
        width=PHOTO_WIDTH,
        height=PHOTO_HEIGHT,
        kind="proportional",
    )


def _draw_bbox(image: Image.Image, bbox: BBox) -> None:
    """Draw a model-reported normalized bounding box on the photo."""
    width, height = image.size
    x1, y1, x2, y2 = bbox
    coordinates = (round(x1 * width), round(y1 * height), round(x2 * width), round(y2 * height))
    draw = ImageDraw.Draw(image)
    draw.rectangle(coordinates, outline=ANNOTATION_COLOR, width=ANNOTATION_WIDTH)


def _table_style(background: colors.Color) -> TableStyle:
    """Return consistent styling for report metadata and finding tables."""
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, -1), background),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]
    )


def _cell(styles: dict[str, ParagraphStyle], text: str, bold: bool = False) -> Paragraph:
    """Build an escaped table cell."""
    content = f"<b>{escape(text)}</b>" if bold else escape(text)
    return Paragraph(content, styles["table"])


def _paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    """Build a paragraph with escaped model or transcript content."""
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _format_date(value: str) -> str:
    """Format an ISO timestamp for readers."""
    return datetime.fromisoformat(value).astimezone().strftime("%b %d, %Y | %I:%M %p %Z")


def _needs_manual_review(result: InspectionResult) -> bool:
    """Return whether the deterministic inspection requires human review."""
    return result.grade == "Needs manual review" or result.verdict == "needs_review"


def _draw_footer(canvas: Canvas, document: SimpleDocTemplate) -> None:
    """Draw a restrained page footer with page number."""
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(PAGE_MARGIN, 0.48 * inch, A4[0] - PAGE_MARGIN, 0.48 * inch)
    canvas.setFont(REGULAR_FONT_NAME, 8)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(A4[0] - PAGE_MARGIN, 0.3 * inch, f"Page {document.page}")
    canvas.restoreState()

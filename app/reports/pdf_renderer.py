"""Build professional PDF reports from validated inspection data."""

from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from PIL import Image, ImageDraw, ImageOps
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    KeepInFrame,
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

PAGE_MARGIN = 0.45 * inch
PAGE_TOP_MARGIN = 0.72 * inch
PAGE_BOTTOM_MARGIN = 0.57 * inch
CONTENT_WIDTH = A4[0] - 2 * PAGE_MARGIN
PHOTO_WIDTH = CONTENT_WIDTH / 2 - 0.18 * inch
PHOTO_HEIGHT = 1.45 * inch
FOOTER_RULE_Y = 0.5 * inch
FOOTER_TEXT_Y = 0.3 * inch
IMAGE_JPEG_QUALITY = 90
ANNOTATION_COLOR = (190, 48, 37)
ANNOTATION_WIDTH = 5
INK = colors.HexColor("#203b2e")
MUTED = colors.HexColor("#68766b")
LINE = colors.HexColor("#dce4da")
PALE_GREEN = colors.HexColor("#eef3e7")
WARNING = colors.HexColor("#fff3d4")
WARNING_BORDER = colors.HexColor("#d5a84c")
DEFECT_BACKGROUND = colors.HexColor("#fbe9e6")
DEFECT_BORDER = colors.HexColor("#bd4b3b")
USER_TYPE_LABELS = {
    ReportUserType.FARMER: "Farmer",
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
    document = _create_document(buffer, report_id)
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
    one_page_story = KeepInFrame(document.width, document.height, story, mode="shrink")
    document.build([one_page_story], onFirstPage=_draw_page_chrome)
    return buffer.getvalue()


def _create_document(buffer: BytesIO, report_id: str) -> SimpleDocTemplate:
    """Create an A4 document with room for repeating page chrome."""
    return SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=PAGE_MARGIN,
        leftMargin=PAGE_MARGIN,
        topMargin=PAGE_TOP_MARGIN,
        bottomMargin=PAGE_BOTTOM_MARGIN,
        title=f"Inspection Report {report_id}",
        author="Fieldmark",
    )


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
    return {
        "title": _text_style("ReportTitle", BOLD_FONT_NAME, 19, 22, INK, 0, 5),
        "section": _text_style("ReportSection", BOLD_FONT_NAME, 9, 11, INK, 6, 3),
        "body": _text_style("ReportBody", REGULAR_FONT_NAME, 8, 10, INK, 0, 3),
        "small": _text_style("ReportSmall", REGULAR_FONT_NAME, 7, 9, MUTED),
        "table": _text_style("ReportTable", REGULAR_FONT_NAME, 7.5, 9, INK),
        "panel_label": _text_style("ReportPanelLabel", BOLD_FONT_NAME, 6, 7, MUTED, 0, 1),
        "metric_label": _text_style("ReportMetricLabel", BOLD_FONT_NAME, 6, 7, MUTED, 0, 2),
        "metric_value": _text_style("ReportMetricValue", BOLD_FONT_NAME, 10, 12, INK),
        "grade_value": _text_style("ReportGradeValue", BOLD_FONT_NAME, 13, 15, INK),
        "bullet": _text_style("ReportBullet", REGULAR_FONT_NAME, 8, 10, INK, 0, 3, left_indent=12),
    }


def _text_style(
    name: str,
    font_name: str,
    font_size: float,
    leading: float,
    text_color: colors.Color,
    space_before: float = 0,
    space_after: float = 0,
    left_indent: float = 0,
) -> ParagraphStyle:
    """Create one consistent paragraph style for report sections."""
    return ParagraphStyle(
        name,
        fontName=font_name,
        fontSize=font_size,
        leading=leading,
        textColor=text_color,
        spaceBefore=space_before,
        spaceAfter=space_after,
        leftIndent=left_indent,
    )


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
    story = _opening_sections(styles, report_id, report_date, inspection_date, user_type, result)
    story.extend(_evidence_sections(styles, result, report_text, original_image))
    _append_recommendations(story, styles, result, report_text)
    story.extend((Spacer(1, 6), _paragraph(result.disclaimer, styles["small"])))
    return story


def _opening_sections(
    styles: dict[str, ParagraphStyle],
    report_id: str,
    report_date: str,
    inspection_date: str,
    user_type: ReportUserType,
    result: InspectionResult,
) -> list[Flowable]:
    """Build the report title, outcome card, metadata, and review notice."""
    story: list[Flowable] = [
        _group(
            [
                _paragraph("Inspection Report", styles["title"]),
                _outcome_panel(styles, result),
                _metadata_table(styles, report_id, report_date, inspection_date, user_type),
            ],
        )
    ]
    if _needs_manual_review(result):
        story.append(_manual_review_panel(styles))
    return story


def _evidence_sections(
    styles: dict[str, ParagraphStyle],
    result: InspectionResult,
    report_text: InspectionReportText,
    original_image: bytes,
) -> list[Flowable]:
    """Build the summary, image evidence, findings, and exact inspection labels."""
    story: list[Flowable] = [
        _message_panel(
            styles,
            "PLAIN-LANGUAGE SUMMARY",
            report_text.summary,
            PALE_GREEN,
            INK,
        )
    ]
    story.append(
        _group(
            [
                _paragraph("Photo evidence", styles["section"]),
                _photo_table(styles, original_image, result),
            ],
        )
    )
    story.extend(_finding_sections(styles, report_text.findings))
    story.append(_details_table(styles, result))
    return story


def _finding_sections(
    styles: dict[str, ParagraphStyle],
    findings: list[str],
) -> list[Flowable]:
    """Keep the findings heading with its first bullet."""
    bullets: list[Flowable] = [_bullet_paragraph(finding, styles["bullet"]) for finding in findings]
    first_section = _group([_paragraph("Inspection findings", styles["section"]), bullets[0]])
    return [first_section, *bullets[1:]]


def _outcome_panel(styles: dict[str, ParagraphStyle], result: InspectionResult) -> Table:
    """Present the grade, verdict, confidence, and severity as a quick-scan card."""
    verdict = "Needs manual review" if _needs_manual_review(result) else result.verdict.title()
    confidence = "Unavailable" if result.confidence is None else f"{result.confidence:.0%}"
    labels = ("GRADE", "VERDICT", "MODEL CONFIDENCE", "SEVERITY")
    values = (result.grade, verdict, confidence, result.severity.value.title())
    value_styles = (styles["grade_value"], styles["metric_value"], styles["metric_value"])
    row_labels = [Paragraph(label, styles["metric_label"]) for label in labels]
    row_values = [
        Paragraph(escape(value), value_styles[min(index, len(value_styles) - 1)])
        for index, value in enumerate(values)
    ]
    widths = [CONTENT_WIDTH / 4] * 4
    table = Table([row_labels, row_values], colWidths=widths, hAlign="LEFT", splitByRow=False)
    table.setStyle(_outcome_table_style(_outcome_background(result), _outcome_accent(result)))
    return table


def _outcome_accent(result: InspectionResult) -> colors.Color:
    """Choose the high-contrast accent for the outcome card."""
    if _needs_manual_review(result):
        return WARNING_BORDER
    if result.verdict == "defect" or result.grade == "Reject":
        return DEFECT_BORDER
    return INK


def _outcome_background(result: InspectionResult) -> colors.Color:
    """Choose a restrained status tint from the deterministic inspection verdict."""
    if _needs_manual_review(result):
        return WARNING
    if result.verdict == "defect" or result.grade == "Reject":
        return DEFECT_BACKGROUND
    return PALE_GREEN


def _outcome_table_style(background: colors.Color, accent: colors.Color) -> TableStyle:
    """Style the four-column inspection summary card."""
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, -1), background),
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("LINEBEFORE", (0, 0), (0, -1), 2.5, accent),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
    )


def _message_panel(
    styles: dict[str, ParagraphStyle],
    label: str,
    text: str,
    background: colors.Color,
    accent: colors.Color,
) -> Table:
    """Render a labeled callout with escaped, non-HTML model text."""
    rows = [
        [Paragraph(escape(label), styles["panel_label"])],
        [_paragraph(text, styles["body"])],
    ]
    table = Table(rows, colWidths=[CONTENT_WIDTH], hAlign="LEFT", splitByRow=False)
    table.setStyle(_message_panel_style(background, accent))
    return table


def _message_panel_style(background: colors.Color, accent: colors.Color) -> TableStyle:
    """Apply consistent fills, spacing, and an accent rule to a callout."""
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, -1), background),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("LINEBEFORE", (0, 0), (0, -1), 2.5, accent),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, 0), 4),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
            ("TOPPADDING", (0, 1), (-1, 1), 0),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 4),
        ]
    )


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
    table = Table(rows, colWidths=[1.55 * inch, CONTENT_WIDTH - 1.55 * inch], hAlign="LEFT")
    table.setStyle(_table_style(colors.white))
    return table


def _photo_table(
    styles: dict[str, ParagraphStyle],
    original_image: bytes,
    result: InspectionResult,
) -> Table:
    """Include an annotated preview only when a reported defect has a bounding box."""
    original = _pdf_image(original_image)
    if not result.defect_types or result.bbox is None:
        rows = [[original], [_paragraph("ORIGINAL PHOTO", styles["panel_label"])]]
        table = Table(rows, colWidths=[CONTENT_WIDTH], hAlign="LEFT", splitByRow=False)
        table.setStyle(_photo_table_style())
        return table

    annotated_image = annotate_photo(original_image, result.bbox)
    annotated = _pdf_image(annotated_image)
    rows = [
        [original, annotated],
        [
            _paragraph("ORIGINAL PHOTO", styles["panel_label"]),
            _paragraph("ANNOTATED PHOTO  /  MODEL-REPORTED BOX", styles["panel_label"]),
        ],
    ]
    table = Table(
        rows,
        colWidths=[CONTENT_WIDTH / 2] * 2,
        hAlign="LEFT",
        splitByRow=False,
    )
    table.setStyle(_photo_table_style())
    return table


def _photo_table_style() -> TableStyle:
    """Keep both photo previews and captions aligned in a single evidence panel."""
    return TableStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
    )


def _details_table(styles: dict[str, ParagraphStyle], result: InspectionResult) -> Table:
    """Render product and defect labels without deriving new findings."""
    defect_types = ", ".join(item.value for item in result.defect_types) or "None reported"
    product_label = result.detected_item.strip().title() or result.produce_type.title()
    rows = [
        [_cell(styles, "Product type", True), _cell(styles, product_label)],
        [_cell(styles, "Defect type", True), _cell(styles, defect_types.title())],
    ]
    table = Table(
        rows,
        colWidths=[1.55 * inch, CONTENT_WIDTH - 1.55 * inch],
        hAlign="LEFT",
        splitByRow=False,
    )
    table.setStyle(_table_style(colors.white))
    return table


def _manual_review_panel(styles: dict[str, ParagraphStyle]) -> Table:
    """Make uncertain results prominent without stating possible defects as fact."""
    return _message_panel(
        styles,
        "MANUAL REVIEW REQUIRED",
        "This visual result is uncertain. Possible defects are not confirmed; inspect the product "
        "manually before acting.",
        WARNING,
        WARNING_BORDER,
    )


def _append_voice_note(
    story: list[Flowable],
    styles: dict[str, ParagraphStyle],
    transcript: str,
    report_text: InspectionReportText,
) -> None:
    """Include the exact transcript and any AI-identified contradiction."""
    if not transcript:
        return
    story.append(_paragraph("Voice note", styles["section"]))
    story.append(_paragraph(transcript, styles["body"]))
    if report_text.voice_note_conflict:
        story.append(
            _message_panel(
                styles,
                "DIFFERENCE BETWEEN SOURCES",
                report_text.voice_note_conflict,
                WARNING,
                WARNING_BORDER,
            )
        )


def _append_recommendations(
    story: list[Flowable],
    styles: dict[str, ParagraphStyle],
    result: InspectionResult,
    report_text: InspectionReportText,
) -> None:
    """Include the deterministic action and validated AI recommendations."""
    action_panel = _message_panel(
        styles,
        "RECOMMENDED ACTION",
        result.recommended_action,
        PALE_GREEN,
        INK,
    )
    story.append(
        _group(
            [
                _paragraph("Recommended next steps", styles["section"]),
                action_panel,
            ],
        )
    )
    additional = _additional_recommendations(result.recommended_action, report_text)
    story.extend(_bullet_paragraph(item, styles["bullet"]) for item in additional)


def _additional_recommendations(
    recommended_action: str,
    report_text: InspectionReportText,
) -> list[str]:
    """Exclude the deterministic action from the supporting recommendation list."""
    return list(
        dict.fromkeys(item for item in report_text.recommendations if item != recommended_action)
    )


def _group(flowables: list[Flowable]) -> Table:
    """Keep related flowables together inside a single table cell."""
    table = Table([[flowables]], colWidths=[CONTENT_WIDTH], hAlign="LEFT", splitByRow=False)
    table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _bullet_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    """Render untrusted report text as an escaped bullet point."""
    return Paragraph(escape(text), style, bulletText="•")


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
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
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


def _draw_page_chrome(
    canvas: Canvas,
    document: SimpleDocTemplate,
) -> None:
    """Draw the report footer and page number."""
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setFont(REGULAR_FONT_NAME, 8)
    canvas.setFillColor(MUTED)
    canvas.line(PAGE_MARGIN, FOOTER_RULE_Y, A4[0] - PAGE_MARGIN, FOOTER_RULE_Y)
    canvas.drawString(PAGE_MARGIN, FOOTER_TEXT_Y, "Fieldmark  |  Indicative quality guidance")
    canvas.drawRightString(A4[0] - PAGE_MARGIN, FOOTER_TEXT_Y, f"Page {document.page}")
    canvas.restoreState()

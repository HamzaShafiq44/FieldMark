"""Build professional one-page PDF reports from validated inspection data."""

from collections.abc import Sequence
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
    HRFlowable,
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
PAGE_TOP_MARGIN = 0.98 * inch
PAGE_BOTTOM_MARGIN = 0.55 * inch
CONTENT_WIDTH = A4[0] - 2 * PAGE_MARGIN
PHOTO_WIDTH = CONTENT_WIDTH / 2 - 0.12 * inch
PHOTO_HEIGHT = 2.5 * inch
PHOTO_PAIR_HEIGHT = 2.15 * inch
OUTCOME_HEIGHT = 1.06 * inch
FOOTER_RULE_Y = 0.5 * inch
FOOTER_TEXT_Y = 0.3 * inch
IMAGE_JPEG_QUALITY = 90
ANNOTATION_COLOR = (190, 48, 37)
ANNOTATION_WIDTH = 5
INK = colors.HexColor("#203b2e")
MUTED = colors.HexColor("#68766b")
LINE = colors.HexColor("#dce4da")
PALE_GREEN = colors.HexColor("#eef3e7")
LIME = colors.HexColor("#d7ed88")
LEAF = colors.HexColor("#5d8154")
WARNING = colors.HexColor("#fff3d4")
WARNING_BORDER = colors.HexColor("#d5a84c")
WARNING_INK = colors.HexColor("#5e4513")
DEFECT_BACKGROUND = colors.HexColor("#fbe9e6")
DEFECT_BORDER = colors.HexColor("#bd4b3b")
SEVERITY_NONE = colors.HexColor("#c3d6c1")
SEVERITY_LOW = colors.HexColor("#e6e0a8")
SEVERITY_MED = colors.HexColor("#d9a066")
SEVERITY_HIGH = colors.HexColor("#bd4b3b")
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


class _OutcomeCard(Flowable):
    """A branded, grade-first summary card for the top of the report."""

    def __init__(self, result: InspectionResult) -> None:
        """Capture the inspection result shown in the card."""
        super().__init__()
        self.result = result

    def wrap(self, aW: float, aH: float) -> tuple[float, float]:
        """Occupy the full content width and a fixed card height."""
        self.width = CONTENT_WIDTH
        self.height = OUTCOME_HEIGHT
        return CONTENT_WIDTH, OUTCOME_HEIGHT

    def draw(self) -> None:
        """Draw the grade, verdict badge, product name, and secondary facts."""
        result = self.result
        background, accent, pill_text = _outcome_palette(result)
        canvas = self.canv
        width = self.width
        height = self.height
        manual_review = _needs_manual_review(result)
        verdict = (
            "Needs review" if manual_review else "Pass" if result.verdict == "pass" else "Defect"
        )
        product = result.detected_item.strip().title() or result.produce_type.title()
        confidence = "Unavailable" if result.confidence is None else f"{result.confidence:.0%}"
        grade_label = "—" if manual_review else result.grade
        severity_label = str(result.severity).title()

        canvas.saveState()
        canvas.setFillColor(background)
        canvas.roundRect(0, 0, width, height, 11, fill=1, stroke=0)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.roundRect(0, 0, width, height, 11, fill=0, stroke=1)

        canvas.setStrokeColor(accent)
        canvas.setLineWidth(2.6)
        canvas.setLineCap(1)
        canvas.line(0.2 * inch, 0.18 * inch, 0.2 * inch, height - 0.18 * inch)

        grade_box_width = 0.98 * inch
        grade_box_height = 0.72 * inch
        grade_box_x = 0.36 * inch
        grade_box_y = 0.17 * inch
        canvas.setFillColor(colors.white)
        canvas.setStrokeColor(accent)
        canvas.setLineWidth(0.8)
        canvas.roundRect(
            grade_box_x,
            grade_box_y,
            grade_box_width,
            grade_box_height,
            12,
            fill=1,
            stroke=1,
        )
        canvas.setFillColor(accent)
        grade_size = 34 if len(grade_label) <= 2 else 20
        while (
            grade_size > 12
            and pdfmetrics.stringWidth(grade_label, BOLD_FONT_NAME, grade_size)
            > grade_box_width - 0.14 * inch
        ):
            grade_size -= 1
        canvas.setFont(BOLD_FONT_NAME, grade_size)
        canvas.drawCentredString(
            grade_box_x + grade_box_width / 2,
            grade_box_y + (grade_box_height - grade_size * 0.72) / 2,
            grade_label,
        )

        # Grade gauge bar below the letter inside the grade box.
        gauge_width = grade_box_width - 0.14 * inch
        gauge_height = 0.2 * inch
        gauge_x = grade_box_x + 0.07 * inch
        gauge_y = grade_box_y - 0.075 * inch
        gauge_inner_h = gauge_height - 2
        gauge_inner_r = gauge_inner_h / 2
        canvas.setStrokeColor(colors.HexColor("#e5e9df"))
        canvas.setLineWidth(1)
        canvas.roundRect(
            gauge_x, gauge_y, gauge_width, gauge_height, gauge_height / 2, fill=0, stroke=1
        )
        if not manual_review and result.grade == "A":
            canvas.setFillColor(LEAF)
            canvas.roundRect(
                gauge_x + 1,
                gauge_y + 1,
                gauge_width - 2,
                gauge_inner_h,
                gauge_inner_r,
                fill=1,
                stroke=0,
            )
        elif not manual_review and result.grade == "B":
            canvas.setFillColor(LEAF)
            canvas.roundRect(
                gauge_x + 1,
                gauge_y + 1,
                (gauge_width - 2) * 0.62,
                gauge_inner_h,
                gauge_inner_r,
                fill=1,
                stroke=0,
            )
        elif not manual_review and result.grade == "Reject":
            canvas.setFillColor(DEFECT_BORDER)
            canvas.roundRect(
                gauge_x + 1,
                gauge_y + 1,
                1,
                gauge_inner_h,
                gauge_inner_r,
                fill=1,
                stroke=0,
            )

        content_x = grade_box_x + grade_box_width + 0.28 * inch
        content_width = width - content_x - 0.3 * inch
        pill_font_size = 6.2
        pill_text_width = pdfmetrics.stringWidth(verdict.upper(), BOLD_FONT_NAME, pill_font_size)
        pill_width = pill_text_width + 0.24 * inch
        pill_height = 0.21 * inch
        pill_y = height - 0.37 * inch
        canvas.setFillColor(accent)
        canvas.roundRect(
            content_x, pill_y, pill_width, pill_height, pill_height / 2, fill=1, stroke=0
        )
        canvas.setFillColor(pill_text)
        canvas.setFont(BOLD_FONT_NAME, pill_font_size)
        canvas.drawString(content_x + 0.12 * inch, pill_y + 0.066 * inch, verdict.upper())

        canvas.setFillColor(INK)
        _draw_fitted_text(
            canvas,
            product,
            content_x,
            height - 0.56 * inch,
            content_width,
            BOLD_FONT_NAME,
            16,
            min_size=9,
        )

        # Severity badge (independent color coding).
        severity_colors = {
            "None": SEVERITY_NONE,
            "Low": SEVERITY_LOW,
            "Medium": SEVERITY_MED,
            "High": SEVERITY_HIGH,
        }
        severity_fill = severity_colors.get(severity_label, SEVERITY_NONE)
        severity_text = pdfmetrics.stringWidth(severity_label.upper(), BOLD_FONT_NAME, 5.4)
        badge_width = severity_text + 0.22 * inch
        badge_height = 0.17 * inch
        badge_x = content_x
        badge_y = height - 0.78 * inch
        canvas.setFillColor(severity_fill)
        canvas.roundRect(
            badge_x, badge_y, badge_width, badge_height, badge_height / 2, fill=1, stroke=0
        )
        canvas.setFillColor(colors.white if severity_label in ("Medium", "High") else INK)
        canvas.setFont(BOLD_FONT_NAME, 5.4)
        canvas.drawString(badge_x + 0.11 * inch, badge_y + 0.05 * inch, severity_label.upper())

        canvas.setFillColor(MUTED)
        canvas.setFont(REGULAR_FONT_NAME, 7.2)
        canvas.drawString(content_x, 0.17 * inch, f"Confidence {confidence}")
        canvas.restoreState()


class _FactChips(Flowable):
    """Rounded tinted chips for product, severity, date, and prepared-for."""

    def __init__(
        self, result: InspectionResult, inspection_date: str, user_type: ReportUserType
    ) -> None:
        super().__init__()
        self.result = result
        self.inspection_date = inspection_date
        self.user_type = user_type

    def wrap(self, aW: float, aH: float) -> tuple[float, float]:
        self.width = CONTENT_WIDTH
        self.height = 0.42 * inch
        return self.width, self.height

    def draw(self) -> None:
        result = self.result
        product_label = result.detected_item.strip().title() or result.produce_type.title()
        defect_types = ", ".join(str(item) for item in result.defect_types) or "None reported"
        date_text = _format_short_date(self.inspection_date)
        items = [
            ("Product", product_label),
            ("Defects", defect_types.title()),
            ("Inspected", date_text),
            ("Prepared for", USER_TYPE_LABELS[self.user_type]),
        ]
        canvas = self.canv
        x = 0
        gap = 0.18 * inch
        chip_padding_x = 0.14 * inch
        font_size = 6.4
        label_size = 5.2
        for label, value in items:
            label_w = pdfmetrics.stringWidth(label.upper(), BOLD_FONT_NAME, label_size)
            value_w = pdfmetrics.stringWidth(str(value), REGULAR_FONT_NAME, font_size)
            chip_w = label_w + value_w + 2 * chip_padding_x + 0.12 * inch
            chip_h = 0.26 * inch
            canvas.setFillColor(PALE_GREEN)
            canvas.roundRect(x, 0.08 * inch, chip_w, chip_h, chip_h / 2, fill=1, stroke=0)
            canvas.setFillColor(MUTED)
            canvas.setFont(BOLD_FONT_NAME, label_size)
            canvas.drawString(x + chip_padding_x, 0.14 * inch, label.upper())
            canvas.setFillColor(INK)
            canvas.setFont(REGULAR_FONT_NAME, font_size)
            canvas.drawString(
                x + chip_padding_x + label_w + 0.08 * inch,
                0.14 * inch,
                str(value),
            )
            x += chip_w + gap


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
        inspection_date,
        user_type,
        result,
        report_text,
        transcript,
        original_image,
    )
    one_page_story = KeepInFrame(document.width, document.height, story, mode="shrink")

    def first_page(canvas: Canvas, page: SimpleDocTemplate) -> None:
        _draw_page_chrome(canvas, page, report_id, report_date, user_type, result)

    document.build([one_page_story], onFirstPage=first_page)
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
        "section": _text_style("ReportSection", BOLD_FONT_NAME, 8.5, 10, INK, 0, 0),
        "body": _text_style("ReportBody", REGULAR_FONT_NAME, 8.6, 10.6, INK, 0, 3),
        "small": _text_style("ReportSmall", REGULAR_FONT_NAME, 6.5, 8.5, MUTED),
        "table": _text_style("ReportTable", REGULAR_FONT_NAME, 7.4, 9.2, INK),
        "panel_label": _text_style("ReportPanelLabel", BOLD_FONT_NAME, 6.2, 7.5, MUTED, 0, 3),
        "bullet": _text_style(
            "ReportBullet", REGULAR_FONT_NAME, 8.0, 10.0, INK, 0, 3, left_indent=11
        ),
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
    inspection_date: str,
    user_type: ReportUserType,
    result: InspectionResult,
    report_text: InspectionReportText,
    transcript: str,
    original_image: bytes,
) -> list[Flowable]:
    """Assemble all PDF sections in reading order."""
    story: list[Flowable] = [
        _OutcomeCard(result),
        Spacer(1, 8),
        _FactChips(result, inspection_date, user_type),
    ]
    if _needs_manual_review(result):
        story.extend((Spacer(1, 8), _manual_review_panel(styles)))
    story.extend((Spacer(1, 12),))
    story.extend(
        (
            _section_band(styles, "WHAT WE SAW", report_text.summary, PALE_GREEN, LEAF),
            Spacer(1, 10),
            _section_band(styles, "NEXT STEP", result.recommended_action, PALE_GREEN, LEAF),
        )
    )
    story.extend((Spacer(1, 10), _section_heading(styles, "Photo evidence")))
    story.append(_photo_table(styles, original_image, result))
    story.extend((Spacer(1, 10), _section_heading(styles, "Inspection findings")))
    story.extend(_bullet_paragraph(finding, styles["bullet"]) for finding in report_text.findings)
    _append_voice_note(story, styles, transcript, report_text)
    story.extend((Spacer(1, 6), _paragraph(result.disclaimer, styles["small"])))
    return story


def _section_band(
    styles: dict[str, ParagraphStyle],
    label: str,
    text: str,
    background: colors.Color,
    accent: colors.Color,
) -> Table:
    """Full-width band combining a label and body text with a left accent rule."""
    rows = [
        [Paragraph(escape(label), styles["panel_label"])],
        [_paragraph(text, styles["body"])],
    ]
    table = Table(rows, colWidths=[CONTENT_WIDTH], hAlign="LEFT", splitByRow=False)
    table.setStyle(_section_band_style(background, accent))
    return table


def _section_band_style(background: colors.Color, accent: colors.Color) -> TableStyle:
    """Apply a light fill and left accent rule to a section band."""
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, -1), background),
            ("LINEBEFORE", (0, 0), (0, -1), 2.5, accent),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
            ("TOPPADDING", (0, 1), (-1, 1), 0),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
        ]
    )


def _section_heading(styles: dict[str, ParagraphStyle], title: str) -> Flowable:
    """Render an uppercase section label with a subtle divider."""
    return _group(
        [
            _paragraph(title.upper(), styles["section"]),
            HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=2, spaceAfter=0),
        ]
    )


def _photo_table(
    styles: dict[str, ParagraphStyle],
    original_image: bytes,
    result: InspectionResult,
) -> Table:
    """Include an annotated preview only when a reported defect has a bounding box."""
    if not result.defect_types or result.bbox is None:
        rows = [
            [_pdf_image(original_image, CONTENT_WIDTH)],
            [_paragraph("ORIGINAL PHOTO", styles["panel_label"])],
        ]
        table = Table(rows, colWidths=[CONTENT_WIDTH], hAlign="LEFT", splitByRow=False)
        table.setStyle(_photo_table_style())
        return table

    annotated_image = annotate_photo(original_image, result.bbox)
    rows = [
        [
            _pdf_image(original_image, PHOTO_WIDTH, PHOTO_PAIR_HEIGHT),
            _pdf_image(annotated_image, PHOTO_WIDTH, PHOTO_PAIR_HEIGHT),
        ],
        [
            _paragraph("ORIGINAL PHOTO", styles["panel_label"]),
            _paragraph("ANNOTATED / MODEL-REPORTED AREA", styles["panel_label"]),
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
    """Keep both photo previews and captions aligned with subtle borders."""
    return TableStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, 0), 0.5, LINE),
            ("LINEABOVE", (0, 1), (-1, 1), 0.35, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, 0), 0),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 1), (-1, 1), 0),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 0),
        ]
    )


def _manual_review_panel(styles: dict[str, ParagraphStyle]) -> Table:
    """Make uncertain results prominent without stating possible defects as fact."""
    return _section_band(
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
    """Include a compact voice-note chip and optional conflict note."""
    if not transcript:
        return
    max_chars = 120
    if len(transcript) <= max_chars:
        display = transcript
    else:
        display = transcript[: max_chars - 1].rstrip() + "…"
    story.extend((Spacer(1, 10), _section_heading(styles, "Voice note")))
    story.append(
        _section_band(
            styles,
            "TRANSCRIPT",
            escape(display),
            PALE_GREEN,
            LEAF,
        )
    )
    if report_text.voice_note_conflict:
        story.append(
            _section_band(
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
    """Kept for API compatibility; next step is now rendered inline in the story."""
    return None


def _additional_recommendations(
    recommended_action: str,
    report_text: InspectionReportText,
) -> list[str]:
    """Exclude the deterministic action from the supporting recommendation list."""
    return list(
        dict.fromkeys(item for item in report_text.recommendations if item != recommended_action)
    )


def _group(flowables: Sequence[Flowable], width: float = CONTENT_WIDTH) -> Table:
    """Keep related flowables together inside a single table cell."""
    table = Table([[list(flowables)]], colWidths=[width], hAlign="LEFT", splitByRow=False)
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


def _pdf_image(
    image_data: bytes,
    width: float,
    height: float = PHOTO_HEIGHT,
) -> PdfImage:
    """Create a bounded-size image flowable while preserving aspect ratio."""
    return PdfImage(
        BytesIO(image_data),
        width=width,
        height=height,
        kind="proportional",
    )


def _draw_fitted_text(
    canvas: Canvas,
    text: str,
    x: float,
    y: float,
    max_width: float,
    font_name: str,
    font_size: float,
    min_size: float = 8,
) -> None:
    """Draw text that shrinks or truncates to stay inside the outcome card."""
    size = font_size
    while size > min_size and pdfmetrics.stringWidth(text, font_name, size) > max_width:
        size -= 0.5
    if pdfmetrics.stringWidth(text, font_name, size) > max_width:
        while text and pdfmetrics.stringWidth(f"{text}...", font_name, size) > max_width:
            text = text[:-1]
        text = f"{text}..." if text else ""
    canvas.setFont(font_name, size)
    canvas.drawString(x, y, text)


def _draw_bbox(image: Image.Image, bbox: BBox) -> None:
    """Draw a model-reported normalized bounding box on the photo."""
    width, height = image.size
    x1, y1, x2, y2 = bbox
    coordinates = (round(x1 * width), round(y1 * height), round(x2 * width), round(y2 * height))
    draw = ImageDraw.Draw(image)
    draw.rectangle(coordinates, outline=ANNOTATION_COLOR, width=ANNOTATION_WIDTH)


def _paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    """Build a paragraph with escaped model or transcript content."""
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _format_short_date(value: str) -> str:
    """Format an ISO timestamp compactly for the header and facts panel."""
    return datetime.fromisoformat(value).astimezone().strftime("%d %b %Y")


def _needs_manual_review(result: InspectionResult) -> bool:
    """Return whether the deterministic inspection requires human review."""
    return result.grade == "Needs manual review" or result.verdict == "needs_review"


def _outcome_palette(result: InspectionResult) -> tuple[colors.Color, colors.Color, colors.Color]:
    """Return the background, accent, and badge text colors for the outcome card."""
    if _needs_manual_review(result):
        return WARNING, WARNING_BORDER, WARNING_INK
    if result.verdict == "defect" or result.grade == "Reject":
        return DEFECT_BACKGROUND, DEFECT_BORDER, colors.white
    return PALE_GREEN, INK, colors.white


def _draw_page_chrome(
    canvas: Canvas,
    document: SimpleDocTemplate,
    report_id: str,
    report_date: str,
    user_type: ReportUserType,
    result: InspectionResult,
) -> None:
    """Draw the branded header with status ribbon and the report footer."""
    page_width, page_height = A4
    canvas.saveState()

    brand_y = page_height - 0.55 * inch
    canvas.setFillColor(LIME)
    canvas.roundRect(PAGE_MARGIN, brand_y, 0.34 * inch, 0.34 * inch, 0.1 * inch, fill=1, stroke=0)
    canvas.setFillColor(INK)
    canvas.setFont(BOLD_FONT_NAME, 14)
    canvas.drawCentredString(PAGE_MARGIN + 0.17 * inch, brand_y + 0.1 * inch, "F")
    canvas.setFont(BOLD_FONT_NAME, 10)
    canvas.drawString(PAGE_MARGIN + 0.46 * inch, page_height - 0.4 * inch, "FIELDMARK")
    canvas.setFillColor(MUTED)
    canvas.setFont(BOLD_FONT_NAME, 5.5)
    canvas.drawString(
        PAGE_MARGIN + 0.46 * inch,
        page_height - 0.51 * inch,
        "PRODUCT QUALITY STUDIO",
    )

    canvas.setFillColor(INK)
    canvas.setFont(BOLD_FONT_NAME, 8)
    canvas.drawRightString(page_width - PAGE_MARGIN, page_height - 0.39 * inch, "INSPECTION REPORT")
    canvas.setFillColor(MUTED)
    canvas.setFont(REGULAR_FONT_NAME, 6.2)
    canvas.drawRightString(
        page_width - PAGE_MARGIN,
        page_height - 0.51 * inch,
        f"Report {report_id[:8].upper()}  ·  {_format_short_date(report_date)}  ·  "
        f"{USER_TYPE_LABELS[user_type]}",
    )

    header_rule_y = page_height - 0.72 * inch
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(PAGE_MARGIN, header_rule_y, page_width - PAGE_MARGIN, header_rule_y)
    canvas.setStrokeColor(LIME)
    canvas.setLineWidth(2)
    canvas.line(PAGE_MARGIN, header_rule_y, PAGE_MARGIN + 1.1 * inch, header_rule_y)

    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(PAGE_MARGIN, FOOTER_RULE_Y, page_width - PAGE_MARGIN, FOOTER_RULE_Y)
    canvas.setFillColor(MUTED)
    canvas.setFont(REGULAR_FONT_NAME, 6.5)
    canvas.drawString(
        PAGE_MARGIN,
        FOOTER_TEXT_Y,
        "Fieldmark  ·  Visual quality guidance, not a food safety assessment",
    )
    canvas.drawRightString(page_width - PAGE_MARGIN, FOOTER_TEXT_Y, f"Page {document.page}")
    canvas.restoreState()

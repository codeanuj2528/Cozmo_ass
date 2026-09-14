import re
import os
import sys
from pathlib import Path

from reportlab.lib.pagesizes import letter, A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.HexColor("#555555"))
        
        # Header (pages 2+)
        if self._pageNumber > 1:
            self.drawString(54, 842 - 36, "Cozmo AI — Floor-Plan Reconstruction Technical Report")
            self.setStrokeColor(colors.HexColor("#DDDDDD"))
            self.setLineWidth(0.5)
            self.line(54, 842 - 42, 595.27 - 54, 842 - 42)
            
        # Footer (all pages)
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(595.27 - 54, 36, page_str)
        self.drawString(54, 36, "CONFIDENTIAL — FOR TECHNICAL EVALUATION ONLY")
        self.setStrokeColor(colors.HexColor("#DDDDDD"))
        self.setLineWidth(0.5)
        self.line(54, 48, 595.27 - 54, 48)
        
        self.restoreState()

FONT_DIR = Path("/System/Library/Fonts/Supplemental")


def register_fonts():
    """Fonts that carry the report's minus signs, arrows, inequalities and box-drawing characters.

    The built-in Helvetica and Courier have none of them, and ReportLab draws each as a blank or a
    black box. Falls back to the built-ins where the macOS fonts are not installed.
    """
    faces = {
        "Body": FONT_DIR / "Arial Unicode.ttf",
        "Body-Bold": FONT_DIR / "Arial Bold.ttf",
        "Body-Italic": FONT_DIR / "Arial Italic.ttf",
        "Mono": FONT_DIR / "Courier New.ttf",
    }
    if not all(path.exists() for path in faces.values()):
        return "Helvetica", "Helvetica-Bold", "Courier"
    for name, path in faces.items():
        pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Italic", boldItalic="Body-Bold")
    return "Body", "Body-Bold", "Mono"


def _starts_block(line):
    """Whether a source line starts something other than a continuation of the paragraph above it."""
    return (
        line.startswith(("```", "|", "#", "- ", "* ", "**Technical Report**"))
        or line.strip() == "---"
        or re.match(r"^\d+\.\s", line) is not None
    )


def md_to_pdf(md_path, pdf_path):
    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        leftMargin=24,
        rightMargin=24,
        topMargin=52,
        bottomMargin=58
    )

    styles = getSampleStyleSheet()
    body_font, bold_font, mono_font = register_fonts()
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=14,
        leading=17,
        textColor=colors.HexColor("#1A202C"),
        spaceAfter=3
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=9,
        leading=11.5,
        textColor=colors.HexColor("#4A5568"),
        spaceAfter=4
    )

    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=10,
        leading=12.5,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=5,
        spaceAfter=2,
        keepWithNext=True
    )
    
    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#2D3748"),
        spaceBefore=4,
        spaceAfter=2,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['Normal'],
        fontName=body_font,
        fontSize=7.2,
        leading=9.0,
        textColor=colors.HexColor("#2D3748"),
        spaceAfter=1.5
    )

    bullet_style = ParagraphStyle(
        'Bullet_Custom',
        parent=body_style,
        leftIndent=7,
        firstLineIndent=-4,
        spaceAfter=1
    )

    code_style = ParagraphStyle(
        'Code_Custom',
        parent=styles['Normal'],
        fontName=mono_font,
        fontSize=6.8,
        leading=8.5,
        textColor=colors.HexColor("#1A202C"),
        backColor=colors.HexColor("#EDF2F7"),
        borderColor=colors.HexColor("#CBD5E0"),
        borderWidth=0.5,
        borderPadding=3,
        spaceBefore=2,
        spaceAfter=2
    )

    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName=body_font,
        fontSize=7,
        leading=8.5,
        textColor=colors.HexColor("#2D3748")
    )
    
    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=table_cell_style,
        fontName=bold_font
    )

    story = []
    lines = text.split('\n')
    i = 0
    in_code_block = False
    code_block_lines = []

    def format_inline(txt):
        # Convert markdown bold/italic/links to reportlab HTML tags
        txt = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'<u>\1</u>', txt)
        txt = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', txt)
        txt = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', txt)
        txt = re.sub(r'`([^`]+)`', r'<font face="' + mono_font + r'" color="#C53030">\1</font>', txt)
        return txt

    while i < len(lines):
        line = lines[i]
        
        # Code block handling
        if line.startswith('```'):
            if in_code_block:
                code_text = "<br/>".join(code_block_lines)
                story.append(Paragraph(code_text, code_style))
                code_block_lines = []
                in_code_block = False
            else:
                in_code_block = True
            i += 1
            continue

        if in_code_block:
            escaped_line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace(' ', '&nbsp;')
            code_block_lines.append(escaped_line)
            i += 1
            continue

        # Horizontal Rule
        if line.strip() == '---':
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E2E8F0"), spaceAfter=8, spaceBefore=4))
            i += 1
            continue

        # Markdown Table handling
        if line.startswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].startswith('|'):
                table_lines.append(lines[i])
                i += 1
            
            # Parse markdown table
            rows = []
            for tline in table_lines:
                # check if separator row |---|---|
                if re.match(r'^\|[\s\-:\t|]+\|$', tline.strip()):
                    continue
                cells = [c.strip() for c in tline.split('|')[1:-1]]
                rows.append(cells)
            
            if rows:
                table_data = []
                for r_idx, row in enumerate(rows):
                    row_data = []
                    for c_idx, cell in enumerate(row):
                        style = table_cell_bold if r_idx == 0 else table_cell_style
                        row_data.append(Paragraph(format_inline(cell), style))
                    table_data.append(row_data)
                
                # Render table
                t = Table(table_data, hAlign='LEFT')
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor("#1A202C")),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                    ('TOPPADDING', (0, 0), (-1, -1), 2),
                    ('LEFTPADDING', (0, 0), (-1, -1), 4),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ]))
                story.append(Spacer(1, 2))
                story.append(t)
                story.append(Spacer(1, 4))
            continue

        # Headers
        if line.startswith('# '):
            story.append(Paragraph(format_inline(line[2:]), title_style))
            i += 1
            continue
        elif line.startswith('## '):
            story.append(Paragraph(format_inline(line[3:]), h1_style))
            i += 1
            continue
        elif line.startswith('### '):
            story.append(Paragraph(format_inline(line[4:]), h2_style))
            i += 1
            continue
        elif line.startswith('**Technical Report**'):
            story.append(Paragraph(format_inline(line), subtitle_style))
            i += 1
            continue

        # Bullet points
        if line.startswith('- ') or line.startswith('* '):
            story.append(Paragraph(f"• {format_inline(line[2:])}", bullet_style))
            i += 1
            continue
        if re.match(r'^\d+\.\s', line):
            num = line.split('.')[0]
            rest = '.'.join(line.split('.')[1:]).strip()
            story.append(Paragraph(f"{num}. {format_inline(rest)}", bullet_style))
            i += 1
            continue

        # Normal text paragraph. Markdown wraps one paragraph over several source lines, and bold
        # and code spans often cross those breaks, so the lines are joined before formatting.
        if line.strip():
            paragraph = [line.strip()]
            i += 1
            while i < len(lines) and lines[i].strip() and not _starts_block(lines[i]):
                paragraph.append(lines[i].strip())
                i += 1
            story.append(Paragraph(format_inline(" ".join(paragraph)), body_style))
            continue
        # A heading keeps with the flowable after it, so a spacer there would let the heading end a
        # page on its own.
        if not (story and isinstance(story[-1], Paragraph) and story[-1].style.name in ("Heading1_Custom", "Heading2_Custom")):
            story.append(Spacer(1, 4))
        i += 1

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully compiled {md_path} -> {pdf_path}")

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    md_file = str(root / "technical_report.md")
    pdf_file = str(root / "technical_report.pdf")
    md_to_pdf(md_file, pdf_file)

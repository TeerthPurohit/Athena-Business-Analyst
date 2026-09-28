"""Reference-style PDF artifacts for business and functional requirements."""
from __future__ import annotations

import re
from html import escape
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, PageBreak, CondPageBreak, LongTable, TableStyle
from reportlab.platypus.tableofcontents import TableOfContents

PDF_DELIVERABLES = frozenset({"brd", "frd", "technical_docs"})
BLUE = colors.HexColor("#5277A5")
INK = colors.HexColor("#243447")


def _font_family():
    candidates = [
        (Path("C:/Windows/Fonts"), "arial.ttf", "arialbd.ttf"),
        (Path("/usr/share/fonts/truetype/dejavu"), "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ]
    for directory, regular, bold in candidates:
        if (directory / regular).exists() and (directory / bold).exists():
            if "AthenaText" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("AthenaText", str(directory / regular)))
                pdfmetrics.registerFont(TTFont("AthenaTextBold", str(directory / bold)))
                pdfmetrics.registerFontFamily("AthenaText", normal="AthenaText", bold="AthenaTextBold", italic="AthenaText", boldItalic="AthenaTextBold")
            return "AthenaText", "AthenaTextBold"
    return "Helvetica", "Helvetica-Bold"


def _inline_markup(text):
    text = str(text).replace("…", "...").replace("–", "-").replace("—", "-").replace("\u2011", "-")
    text = escape(text, quote=False)
    text = re.sub(r"&lt;br\s*/?&gt;", "<br/>", text, flags=re.I)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", text)
    return re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)


class RequirementsPDF(SimpleDocTemplate):
    def beforeDocument(self):
        self.bookmark_number = 0

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and hasattr(flowable, "outline_level"):
            text = flowable.getPlainText()
            if re.match(r"^\d|^Appendix", text):
                self.bookmark_number += 1
                key = f"section-{self.bookmark_number}"
                self.canv.bookmarkPage(key)
                self.notify("TOCEntry", (min(flowable.outline_level - 2, 2), text, self.page, key))


def markdown_to_pdf(markdown: str) -> bytes:
    """Render a cover, automatic contents, numbered sections, and wrapped blue tables."""
    regular, bold = _font_family()
    title_match = re.search(r"^# (.+)$", markdown, re.M)
    title = title_match.group(1) if title_match else "Requirements Document"
    project_match = re.search(r"^\*\*Project:\*\* (.+)$", markdown, re.M)
    project = project_match.group(1) if project_match else "Project"
    buffer = BytesIO()
    doc = RequirementsPDF(buffer, pagesize=A4, leftMargin=24*mm, rightMargin=24*mm,
        topMargin=25*mm, bottomMargin=23*mm, title=title, author="Athena")
    body = ParagraphStyle("body", fontName=regular, fontSize=9, leading=13, textColor=INK, spaceAfter=6)
    cell = ParagraphStyle("cell", parent=body, fontSize=8, leading=11, spaceAfter=0)
    header_cell = ParagraphStyle("headerCell", parent=cell, fontName=bold, textColor=colors.white)
    styles = {level: ParagraphStyle(f"h{level}", parent=body, fontName=bold,
        fontSize={2:15,3:11,4:10,5:9,6:9}[level], leading={2:20,3:15,4:14,5:13,6:13}[level],
        spaceBefore=12, spaceAfter=8, keepWithNext=True) for level in range(2,7)}
    cover_title = ParagraphStyle("cover", parent=body, fontName=bold, fontSize=26, leading=33, alignment=TA_CENTER)
    cover_project = ParagraphStyle("coverProject", parent=body, fontSize=17, leading=23, alignment=TA_CENTER, spaceBefore=24)
    story = [Spacer(1,65*mm), Paragraph(_inline_markup(title),cover_title),
        Paragraph(_inline_markup(project),cover_project), Spacer(1,18*mm),
        Paragraph("Draft - subject to review and approval", ParagraphStyle("draft",parent=body,alignment=TA_CENTER)), PageBreak()]
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("# ") or line.startswith("**Project:") or line.startswith("**Status:"):
            continue
        heading = re.match(r"^(#{2,6})\s+(.*)$",line)
        if heading:
            level, text = len(heading.group(1)), heading.group(2)
            if level == 2 and len(story) > 1 and not isinstance(story[-1], PageBreak):
                story.append(CondPageBreak(doc.height - 1))
            paragraph = Paragraph(_inline_markup(text),styles[level])
            paragraph.outline_level = level
            story.append(paragraph)
            if text == "Table of Contents":
                toc = TableOfContents()
                toc.levelStyles = [ParagraphStyle(f"toc{n}",parent=body,fontSize=9,leading=13,leftIndent=n*12,firstLineIndent=0,spaceBefore=4) for n in range(3)]
                story.append(toc)
                while i < len(lines) and not lines[i].startswith("##"):
                    i += 1
            continue
        if line.startswith("|"):
            rows = [line]
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip()); i += 1
            parsed = [[part.strip().replace("\\|", "|") for part in re.split(r"(?<!\\)\|", row.strip("|"))] for row in rows]
            parsed = [row for row in parsed if not all(re.fullmatch(r":?-+:?", c.replace(" ","")) for c in row)]
            if not parsed:
                continue
            count = len(parsed[0])
            parsed = [(row+[""]*count)[:count] for row in parsed]
            widths = [doc.width/count]*count
            if count == 2:
                widths = [doc.width*.29,doc.width*.71]
            elif count == 3 and parsed[0][0] in ("Ref","Field Number"):
                widths = [doc.width*.13,doc.width*.32,doc.width*.55]
            data = [[Paragraph(_inline_markup(value),header_cell if r == 0 else cell) for value in row] for r,row in enumerate(parsed)]
            table = LongTable(data,colWidths=widths,repeatRows=1,hAlign="LEFT",splitByRow=1,splitInRow=1)
            table.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),BLUE),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#EAF0F7"),colors.white]),
                ("GRID",(0,0),(-1,-1),.35,colors.HexColor("#A6B9D0")),("VALIGN",(0,0),(-1,-1),"TOP"),
                ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
                ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
            ]))
            story.extend([table,Spacer(1,10)])
            continue
        bullet = re.match(r"^[-*+]\s+(.*)$",line)
        story.append(Paragraph(_inline_markup(bullet.group(1) if bullet else line),body,bulletText="•" if bullet else None))

    def page(canvas, document):
        canvas.saveState()
        canvas.setFont(bold,9); canvas.setFillColor(BLUE)
        canvas.drawString(doc.leftMargin,A4[1]-15*mm,"ATHENA")
        canvas.setFont(regular,8); canvas.setFillColor(INK)
        header_title = title
        while pdfmetrics.stringWidth(header_title,regular,8) > doc.width-55:
            header_title = header_title[:-4]+"..."
        canvas.drawRightString(A4[0]-doc.rightMargin,A4[1]-15*mm,header_title)
        canvas.setStrokeColor(colors.HexColor("#A6B9D0")); canvas.line(doc.leftMargin,A4[1]-18*mm,A4[0]-doc.rightMargin,A4[1]-18*mm)
        canvas.setFont(regular,8)
        canvas.drawString(doc.leftMargin,12*mm,"Draft | Stakeholder review required")
        canvas.drawRightString(A4[0]-doc.rightMargin,12*mm,f"Page {document.page}")
        canvas.restoreState()
    doc.multiBuild(story,onFirstPage=page,onLaterPages=page)
    return buffer.getvalue()


def pdf_to_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()

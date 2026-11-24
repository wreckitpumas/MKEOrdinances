"""Build small synthetic PDFs for the tests. Placeholder content only."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = letter
LEFT, RIGHT = 72, 540
FONT, BOLD, SIZE, LEADING = "Helvetica", "Helvetica-Bold", 10, 12


class Doc:
    """A tiny layout helper. y is measured from the top of the page."""

    def __init__(self, path: Path):
        self.c = canvas.Canvas(str(path), pagesize=letter)
        self.page = 1
        self.y = 80

    def header_footer(self):
        self.c.setFont(FONT, 9)
        self.c.drawString(LEFT, PAGE_H - 40, f"PLACEHOLDER CODE {self.page}")
        self.c.drawCentredString(PAGE_W / 2, 30, f"- {self.page} -")

    def new_page(self):
        self.header_footer()
        self.c.showPage()
        self.page += 1
        self.y = 80

    def _draw(self, x, text, font, justify_to=None):
        self.c.setFont(font, SIZE)
        t = self.c.beginText(x, PAGE_H - self.y)
        t.setFont(font, SIZE)
        t.setWordSpace(0)  # word spacing is canvas state; always set it
        if justify_to is not None and " " in text:
            gaps = text.count(" ")
            extra = (justify_to - x - stringWidth(text, font, SIZE)) / gaps
            t.setWordSpace(max(0.0, extra))
        t.textOut(text)
        self.c.drawText(t)

    def line(self, text, *, bold=False, indent=0, justify=False):
        self._draw(LEFT + indent, text, BOLD if bold else FONT, RIGHT if justify else None)
        self.y += LEADING

    def heading_line(self, bold_part, rest=""):
        """A line that starts with a bold run followed by regular text."""
        self._draw(LEFT, bold_part, BOLD)
        if rest:
            self._draw(LEFT + stringWidth(bold_part + " ", BOLD, SIZE), rest, FONT)
        self.y += LEADING

    def paragraph(self, lines, indent_first=15):
        """Justified lines; the last line is left aligned and short."""
        for i, l in enumerate(lines):
            last = i == len(lines) - 1
            self._draw(LEFT + (indent_first if i == 0 else 0), l, FONT, None if last else RIGHT)
            self.y += LEADING

    def gap(self, n=1):
        self.y += LEADING * n

    def table(self, rows, col_widths, *, title=None, row_h=16):
        """A ruled table. An optional title row spans all columns."""
        x0 = LEFT
        x1 = LEFT + sum(col_widths)
        top = self.y
        c = self.c
        c.setLineWidth(0.8)
        y = top
        if title:
            c.line(x0, PAGE_H - y, x1, PAGE_H - y)
            c.line(x0, PAGE_H - y, x0, PAGE_H - (y + row_h))
            c.line(x1, PAGE_H - y, x1, PAGE_H - (y + row_h))
            t = c.beginText(x0 + 4, PAGE_H - (y + row_h - 5))
            t.setFont(BOLD, SIZE)
            t.setWordSpace(0)
            t.textOut(title)
            c.drawText(t)
            y += row_h
        for r in rows:
            c.line(x0, PAGE_H - y, x1, PAGE_H - y)
            x = x0
            for w, cell in zip(col_widths, r):
                c.line(x, PAGE_H - y, x, PAGE_H - (y + row_h))
                t = c.beginText(x + 4, PAGE_H - (y + row_h - 5))
                t.setFont(FONT, SIZE)
                t.setWordSpace(0)
                t.textOut(cell)
                c.drawText(t)
                x += w
            c.line(x1, PAGE_H - y, x1, PAGE_H - (y + row_h))
            y += row_h
        c.line(x0, PAGE_H - y, x1, PAGE_H - y)
        self.y = y + LEADING

    def save(self):
        self.header_footer()
        self.c.save()

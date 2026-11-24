"""Layout survey for legal PDFs.

Run this before writing or tuning a profile. It reports what the PDF actually
looks like (headers, footers, heading patterns, history notes, tables, columns)
so that every layout rule in pdf_cleaner/profiles/*.yaml is grounded in evidence.

Usage:
    python3 -m pdf_cleaner.survey <pdf or dir> [...] --out survey/
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

MAX_LINES = 300

# Candidate heading patterns. These are deliberately broad: the survey reports
# counts so a human can decide which ones belong in a profile.
HEADING_CANDIDATES = {
    "dashed_section (e.g. 99-9. Title.)": r"^\d+(?:-\d+(?:\.\d+)?)+\.\s+\S",
    "dashed_section_no_period (e.g. 99-9 Title)": r"^\d+(?:-\d+(?:\.\d+)?)+\s+[A-Z][a-z]",
    "chapter_word (CHAPTER 9)": r"^CHAPTER\s+\d+[A-Z]?\s*$",
    "subchapter_word (SUBCHAPTER 9)": r"^SUBCHAPTER\s+\d+",
    "section_word (Section 9)": r"^Section\s+\d+",
    "part_word (PART I)": r"^PART\s+[IVX]+\b",
    "numbered_caps_sub (9. CAPS)": r"^\d+\.\s+[A-Z][A-Z ,;'&-]{3,}\.",
    "pay_range (Pay Range ...)": r"^Pay Range\s+\S+",
    "all_caps_line": r"^[A-Z][A-Z0-9 ,.'&/()-]{6,}$",
    "dot_leader_entry (Title .... N)": r"\.{5,}\s*\d+\s*$",
}

HISTORY_TERMS = ["history", "ord.", "amended", "created", "renumbered", "eff.", "file"]


def run_pdftotext(pdf: Path, layout: bool = False) -> str:
    args = ["pdftotext", "-enc", "UTF-8"]
    if layout:
        args.append("-layout")
    args += [str(pdf), "-"]
    return subprocess.run(args, capture_output=True, check=True).stdout.decode("utf-8", "replace")


def detect_columns(page) -> bool:
    """Return True when the page has a clear vertical gutter near its center
    that body words do not cross, with substantial text on both sides."""
    words = page.extract_words()
    if len(words) < 40:
        return False
    width = float(page.width)
    mid_lo, mid_hi = width * 0.40, width * 0.60
    # Scan candidate gutter positions in the middle band.
    best = None
    for x in range(int(mid_lo), int(mid_hi), 2):
        crossing = sum(1 for w in words if w["x0"] < x < w["x1"])
        if best is None or crossing < best[1]:
            best = (x, crossing)
    x, crossing = best
    left = sum(1 for w in words if w["x1"] <= x)
    right = sum(1 for w in words if w["x0"] >= x)
    return crossing <= max(2, len(words) * 0.01) and left > 20 and right > 20


def page_lines(page) -> list[str]:
    text = page.extract_text() or ""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def survey_pdf(pdf_path: str) -> tuple[str, str]:
    pdf = Path(pdf_path)
    out: list[str] = []
    add = out.append

    reader = PdfReader(str(pdf))
    n_pages = len(reader.pages)
    sizes = Counter(
        (round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in reader.pages
    )

    add(f"SURVEY: {pdf.name}")
    add("=" * 72)
    add(f"Pages: {n_pages}")
    add("Page sizes (pts): " + ", ".join(f"{w}x{h} ({c} pages)" for (w, h), c in sizes.most_common()))

    first3: Counter = Counter()
    last3: Counter = Counter()
    no_text_pages: list[int] = []
    short_pages: list[int] = []
    multi_col_pages: list[int] = []
    table_pages: list[tuple[int, list]] = []

    def norm(line: str) -> str:
        # Collapse digits so "-72-" and "-73-" count as one footer candidate.
        return re.sub(r"\d+", "#", line)

    with pdfplumber.open(str(pdf)) as doc:
        for i, page in enumerate(doc.pages, start=1):
            chars = len(page.chars)
            if chars == 0:
                no_text_pages.append(i)
                page.close()
                continue
            lines = page_lines(page)
            if len(" ".join(lines)) < 200:
                short_pages.append(i)
            for ln in lines[:3]:
                first3[norm(ln)] += 1
            for ln in lines[-3:]:
                last3[norm(ln)] += 1
            if detect_columns(page):
                multi_col_pages.append(i)
            try:
                tables = page.find_tables()
            except Exception:  # pragma: no cover - pdfplumber edge cases
                tables = []
            if tables:
                rows = []
                for t in tables:
                    data = t.extract()
                    rows.append(data[:2])
                table_pages.append((i, rows))
            page.close()  # release pdfplumber's per-page caches

    add(f"Pages with a text layer: {n_pages - len(no_text_pages)} of {n_pages}")
    add(f"Pages with NO text layer: {compact_ranges(no_text_pages) or 'none'}")
    add(f"Very short pages (<200 chars): {len(short_pages)}: {compact_ranges(short_pages)[:300]}")
    add("")
    add("HEADER CANDIDATES (most frequent first-3 lines, digits shown as #)")
    for line, c in first3.most_common(12):
        add(f"  {c:5d}  {line[:100]}")
    add("")
    add("FOOTER CANDIDATES (most frequent last-3 lines, digits shown as #)")
    for line, c in last3.most_common(12):
        add(f"  {c:5d}  {line[:100]}")
    add("")
    add(f"MULTI-COLUMN PAGES: {len(multi_col_pages)} of {n_pages}")
    add(f"  {compact_ranges(multi_col_pages)[:600]}")
    add("")

    # Pattern statistics come from pdftotext reading-order output.
    text = run_pdftotext(pdf)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    add("SECTION HEADING CANDIDATES (pdftotext reading order)")
    for name, pat in HEADING_CANDIDATES.items():
        rx = re.compile(pat)
        hits = [ln for ln in lines if rx.search(ln)]
        add(f"  [{len(hits):6d}] {name}")
        for ex in hits[:: max(1, len(hits) // 4)][:4]:
            add(f"           e.g. {ex[:90]}")
    add("")
    add("HISTORY / AMENDMENT NOTE CANDIDATES")
    for term in HISTORY_TERMS:
        rx = re.compile(re.escape(term), re.I)
        hits = [ln for ln in lines if rx.search(ln)]
        add(f"  [{len(hits):6d}] '{term}'")
        step = max(1, len(hits) // 10)
        for ex in hits[::step][:10]:
            add(f"           {ex[:95]}")
    # Specific structures seen in Milwaukee documents during exploration.
    specific = {
        "(HISTORY: ...) opener": r"\(HISTORY:",
        "LEGISLATIVE HISTORY table title": r"^LEGISLATIVE HISTORY$",
        "'For (the) legislative history of chapter' pointer": r"For (the )?legislative history of (chapter|Chapter)",
        "statute source note (S. ... Stats ...)": r"^\(S\. .*Stats",
    }
    add("  Specific history structures:")
    for name, pat in specific.items():
        rx = re.compile(pat)
        hits = [ln for ln in lines if rx.search(ln)]
        add(f"  [{len(hits):6d}] {name}")
        for ex in hits[:3]:
            add(f"           {ex[:95]}")
    add("")
    add(f"TABLES DETECTED BY pdfplumber (ruled-line strategy): {len(table_pages)} pages")
    budget = MAX_LINES - len(out) - 5
    for pno, tables in table_pages:
        if budget <= 3:
            add(f"  ... ({len(table_pages)} pages total; list truncated to fit {MAX_LINES} lines)")
            add("  all table pages: " + compact_ranges([p for p, _ in table_pages])[:800])
            break
        for ti, rows in enumerate(tables):
            add(f"  p{pno} table {ti + 1}:")
            budget -= 1
            for r in rows:
                cells = " | ".join((c or "").replace("\n", " ")[:25] for c in r)
                add(f"      {cells[:110]}")
                budget -= 1
    return pdf.stem, "\n".join(out[:MAX_LINES]) + "\n"


def compact_ranges(nums: list[int]) -> str:
    if not nums:
        return ""
    parts = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = n
    parts.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(parts)


def collect_pdfs(inputs: list[str]) -> list[Path]:
    pdfs: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            pdfs.extend(sorted(x for x in p.iterdir() if x.suffix.lower() == ".pdf"))
        elif p.suffix.lower() == ".pdf" and p.exists():
            pdfs.append(p)
        else:
            raise SystemExit(f"Not a PDF or directory: {item}")
    return pdfs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Survey PDF layout for profile authoring.")
    ap.add_argument("inputs", nargs="*", help="PDF files or directories (default: $PDF_DIR)")
    ap.add_argument("--out", default="survey", help="output directory for reports")
    ap.add_argument("--jobs", type=int, default=2)
    args = ap.parse_args(argv)
    inputs = args.inputs or ([os.environ["PDF_DIR"]] if os.environ.get("PDF_DIR") else [])
    if not inputs:
        ap.error("give a PDF or directory, or set PDF_DIR")
    pdfs = collect_pdfs(inputs)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        for stem, report in ex.map(survey_pdf, [str(p) for p in pdfs]):
            (out_dir / f"{stem}.txt").write_text(report, encoding="utf-8")
            print(f"wrote {out_dir / (stem + '.txt')} ({report.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

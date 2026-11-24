"""Verification of the cleaned output against the PDF text layer.

- completeness: every alphanumeric character in the text layer must appear in
  the output, except characters in removed running headers, footers and page
  placeholders. Run per document as a multiset comparison.
- independent text layer check: pdfplumber characters vs pdftotext output.
- spot checks: random sections and tables compared to pdftotext page text.
- suspicious pages: short output, leftover page numbers, header fragments.
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .tables import fidelity_check

_ALNUM = re.compile(r"[^\W_]", re.UNICODE)
_MARKER_TABLE = re.compile(r"^\[TABLE [^\]:]*: ?(.*)\]$")
_MARKER_HISTORY = re.compile(r"^\[HISTORY [^\]]*\] ")
_HEADING = re.compile(r"^#{1,6} ")


def alnum_counter(text: str) -> Counter:
    return Counter(_ALNUM.findall(text))


def content_of_output(text: str) -> str:
    """Output text with tool-added markup removed (marker prefixes, table IDs,
    heading hashes). What remains should be text-layer content only."""
    out = []
    for line in text.splitlines():
        if line == "[/TABLE]":
            continue
        m = _MARKER_TABLE.match(line)
        if m:
            title = m.group(1)
            out.append("" if title == "(untitled)" else title)
            continue
        line = _MARKER_HISTORY.sub("", line)
        line = _HEADING.sub("", line)
        out.append(line)
    return "\n".join(out)


def completeness(char_text_by_page: dict[int, str], removed_texts: list[str], output_text: str) -> dict:
    src = Counter()
    for t in char_text_by_page.values():
        src.update(alnum_counter(t))
    removed = Counter()
    for t in removed_texts:
        removed.update(alnum_counter(t))
    expected = src - removed
    got = alnum_counter(content_of_output(output_text))
    lost = expected - got
    extra = got - expected
    return {
        "text_layer_alnum_chars": sum(src.values()),
        "removed_running_text_chars": sum(removed.values()),
        "expected_chars": sum(expected.values()),
        "output_chars": sum(got.values()),
        "lost_chars": sum(lost.values()),
        "extra_chars": sum(extra.values()),
        "lost_sample": dict(lost.most_common(10)),
        "extra_sample": dict(extra.most_common(10)),
    }


def pdftotext_pages(pdf: Path, first: int, last: int) -> str:
    res = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", "-f", str(first), "-l", str(last), "-layout", str(pdf), "-"],
        capture_output=True,
    )
    return res.stdout.decode("utf-8", "replace")


def have_pdftotext() -> bool:
    """pdftotext (poppler-utils) is optional. Without it the independent
    cross-check and spot checks are skipped; the completeness check still runs."""
    return shutil.which("pdftotext") is not None


def text_layer_crosscheck(pdf: Path, char_text_by_page: dict[int, str]) -> dict:
    if not have_pdftotext():
        return {"skipped": "pdftotext not installed", "pdfplumber_alnum_chars": None, "pdftotext_alnum_chars": None,
                "only_in_pdfplumber": None, "only_in_pdftotext": None}
    ours = Counter()
    for t in char_text_by_page.values():
        ours.update(alnum_counter(t))
    theirs = alnum_counter(pdftotext_pages(pdf, 1, max(char_text_by_page) if char_text_by_page else 1))
    diff_a = ours - theirs
    diff_b = theirs - ours
    return {
        "pdfplumber_alnum_chars": sum(ours.values()),
        "pdftotext_alnum_chars": sum(theirs.values()),
        "only_in_pdfplumber": sum(diff_a.values()),
        "only_in_pdftotext": sum(diff_b.values()),
    }


_WORD = re.compile(r"[^\W_]+(?:[.,'’/-][^\W_]+)*", re.UNICODE)


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def spot_check_sections(pdf: Path, doc, n: int = 5, seed: int = 0) -> list[dict]:
    if not have_pdftotext():
        return []
    rng = random.Random(seed)
    secs = [s for s in doc.sections if s.get("level") == 3] or doc.sections
    if not secs:
        return []
    picks = rng.sample(secs, min(n, len(secs)))
    offsets = sorted(h["offset"] for h in doc.headings)
    results = []
    for s in picks:
        nxt = next((o for o in offsets if o > s["offset"]), len(doc.text))
        body = doc.text[s["offset"] : nxt]
        end_page = page_at(doc.page_map, nxt - 1)
        page_text = pdftotext_pages(pdf, s["page"], max(end_page, s["page"]))
        src = Counter(_words(page_text))
        # Hyphen repair joins line-end fragments; accept a word if its pieces are there.
        src_joined = set(src)
        words = _words(content_of_output(body))
        missing = [w for w in words if w not in src_joined and not _pieces_present(w, src_joined)]
        results.append(
            {
                "section": s["id"],
                "heading": s["heading"][:80],
                "pages": f"{s['page']}-{end_page}" if end_page != s["page"] else str(s["page"]),
                "words": len(words),
                "words_not_found": len(missing),
                "examples_not_found": missing[:5],
                "result": "pass" if not missing else "check",
            }
        )
    return results


def _pieces_present(word: str, vocab: set[str]) -> bool:
    # A word joined across a line-end hyphen appears as two pieces in pdftotext.
    for i in range(2, len(word) - 1):
        if word[:i] in vocab and word[i:] in vocab:
            return True
        if word[:i] + "-" in vocab or (word[:i] in vocab and ("-" + word[i:]) in vocab):
            return True
    return False


def spot_check_tables(pdf: Path, tables, n: int = 3, seed: int = 0) -> list[dict]:
    if not have_pdftotext():
        return []
    rng = random.Random(seed)
    live = [t for t in tables if t.merged_into is None]
    picks = rng.sample(live, min(n, len(live)))
    out = []
    for t in picks:
        page_text = pdftotext_pages(pdf, min(t.pages), max(t.pages))
        cells = [t.title] + t.notes + t.header + [c for r in t.rows for c in r]
        ok, detail = fidelity_check(cells, page_text, require_coverage=False)
        out.append(
            {
                "table": t.id,
                "pages": ",".join(str(p) for p in t.pages),
                "rows": len(t.rows),
                "result": "pass" if ok else "fail",
                "detail": detail,
            }
        )
    return out


def page_at(page_map: list[dict], offset: int) -> int:
    for m in page_map:
        if m["start"] <= offset < m["end"]:
            return m["page"]
    return page_map[-1]["page"] if page_map else 1


_PAGE_NUM = re.compile(r"^-\s*[\divxlc]+[a-z]?\s*-$|^\d{1,2}/\d{1,2}/\d{2,4}$|^-\s*\d+[a-z]?\s*-\s+\d{1,2}/\d{1,2}/\d{2,4}$|^\d{1,2}/\d{1,2}/\d{2,4}\s+-\s*\d+[a-z]?\s*-$")


def suspicious_pages(doc, page_results, removed_headers: set[str]) -> list[dict]:
    """Pages whose output is much shorter than their text layer (letters and
    digits only, so shortened dot leaders do not count), leftover page numbers,
    and lines equal to a running header."""
    per_page = Counter()
    for m in doc.page_map:
        per_page[m["page"]] += len(_ALNUM.findall(doc.text[m["start"] : m["end"]]))
    out = []
    for pr in page_results:
        if not pr.has_text_layer:
            continue
        raw = len(_ALNUM.findall(pr.char_text))
        got = per_page.get(pr.page, 0)
        if raw > 300 and got < 0.25 * raw and not pr.tables:
            out.append(
                {
                    "page": pr.page,
                    "issue": f"short output ({got} letters/digits attributed, {raw} in text layer); "
                    "often a paragraph or table that started on the previous page",
                }
            )
    for i, line in enumerate(doc.text.splitlines()):
        s = line.strip()
        if _PAGE_NUM.match(s):
            out.append({"page": page_at(doc.page_map, doc.text.find(line)), "issue": f"leftover page number or date line: {s!r}"})
        elif s in removed_headers and len(s) > 3:
            out.append({"page": page_at(doc.page_map, doc.text.find(line)), "issue": f"line equals a running header: {s!r}"})
    return out

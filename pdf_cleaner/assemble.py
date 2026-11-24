"""Turn ordered page lines into the cleaned document.

Steps: chapter tables of contents become tables, lines are joined into
paragraphs (with hyphen repair), headings are detected, history notes are
attached to their section, and the output text is written with a page map.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .layout import Line, Word
from .profile import Profile
from .tables import Table, render_block

# Prefixes that usually start a real hyphenated compound ("non-residential").
COMPOUND_PREFIXES = {
    "non", "self", "co", "pre", "post", "anti", "multi", "semi", "cross", "well", "one",
    "two", "three", "four", "five", "six", "ten", "half", "mid", "sub", "ex", "re",
    "inter", "intra", "over", "under", "full", "part", "long", "short", "high", "low",
    "city", "state", "owner", "single", "double", "first", "second", "third",
}
LIST_MARKER = re.compile(r"^(?:\d+(?:\.\d+)*\.|[a-z](?:-\d+)*\.|\(\w{1,4}\))$")
SUBSECTION_WORD = re.compile(r"^(?:\d+(?:\.\d+)?|[a-z](?:-\d+)*)\.$")
WORD_CORE = re.compile(r"^[\W_]*(.*?)[\W_]*$")


def core(w: str) -> str:
    return WORD_CORE.match(w).group(1).lower()


@dataclass
class Stats:
    hyphen_joined: int = 0
    hyphen_kept: int = 0
    hyphen_uncertain: int = 0
    dot_leaders_collapsed: int = 0


@dataclass
class Para:
    words: list[tuple[str, bool]]  # (text, bold)
    page: int
    pages: list[int]
    table: Table | None = None

    @property
    def text(self) -> str:
        return " ".join(w for w, _ in self.words)


# ---------------------------------------------------------------------------
# Vocabulary and hyphen repair


def build_vocab(lines: list[Line]) -> Counter:
    vocab: Counter = Counter()
    for l in lines:
        ws = l.words
        for i, w in enumerate(ws):
            if i == len(ws) - 1 and w.text.endswith("-"):
                continue  # a line-end fragment is not evidence
            c = core(w.text)
            if c:
                vocab[c] += 1
    return vocab


def join_hyphen(left: str, right: str, vocab: Counter, stats: Stats) -> tuple[str, bool]:
    """Return (joined_text, merged). merged=False means keep as two words."""
    stem = left[:-1]
    if not stem or not right:
        return left + " " + right, False
    if not stem[-1].isalpha() or not right[0].isalpha():
        # "59-" + "11" or "one-" + "2" style: keep the hyphen, no space.
        return left + right, True
    if right[0].isupper():
        stats.hyphen_kept += 1
        return left + right, True
    rcore = core(right)
    joined, hyph = core(stem) + rcore, core(stem) + "-" + rcore
    cj, ch = vocab.get(joined, 0), vocab.get(hyph, 0)
    if cj > ch:
        stats.hyphen_joined += 1
        return stem + right, True
    if ch > cj:
        stats.hyphen_kept += 1
        return left + right, True
    stats.hyphen_uncertain += 1
    last = re.split(r"[^A-Za-z]", stem)[-1].lower()
    if last in COMPOUND_PREFIXES:
        stats.hyphen_kept += 1
        return left + right, True
    stats.hyphen_joined += 1
    return stem + right, True


# ---------------------------------------------------------------------------
# Paragraphs


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.:;)]$", text.strip()))


DOT_LEADER_END = re.compile(r"\.{4,}\s*[\dA-Za-z-]+$")


def starts_new(prev: Line | None, cur: Line, section_rx: re.Pattern | None, standalone: list[re.Pattern] = ()) -> bool:
    if prev is None or cur.table is not None or prev.table is not None:
        return True
    if cur.rotated or prev.rotated:
        return True
    size = cur.size
    first_w = cur.words[0]
    if first_w.bold and section_rx and section_rx.match(first_w.text):
        return True
    # Lines such as "CHAPTER 59" always stand alone.
    if any(rx.match(prev.text) or rx.match(cur.text) for rx in standalone):
        return True
    cur_bold = all(w.bold for w in cur.words)
    prev_bold = all(w.bold for w in prev.words)
    if cur_bold and not prev_bold and not prev.text.endswith("-"):
        return True
    if DOT_LEADER_END.search(prev.text):
        return True
    same_flow = prev.page == cur.page and prev.zone == cur.zone
    if same_flow and cur.top - prev.bottom > 0.75 * size:
        return True
    if prev.text.endswith("-") and not prev.text.endswith(" -"):
        return False
    indented = cur.x0 > cur.zone_left + 4
    if not same_flow:
        # Column or page break: the previous line's length says nothing, so
        # rely on indentation, list markers and sentence punctuation.
        if indented or _ends_sentence(prev.text):
            return True
        return bool(LIST_MARKER.match(first_w.text) and _ends_sentence(prev.text))
    fits = prev.x1 + 0.3 * size + (first_w.x1 - first_w.x0) < prev.zone_right - 1
    if fits:
        return True
    if indented and _ends_sentence(prev.text):
        return True
    if LIST_MARKER.match(first_w.text) and _ends_sentence(prev.text):
        return True
    return False


def build_paragraphs(lines: list[Line], profile: Profile, vocab: Counter, stats: Stats) -> list[Para]:
    sec_cfg = profile.section("headings").get("section") or {}
    section_rx = re.compile(sec_cfg["first_word"]) if sec_cfg.get("first_word") else None
    hcfg = profile.section("headings")
    standalone = [re.compile(hcfg[k]["line"]) for k in ("chapter", "subchapter") if hcfg.get(k)]
    dehyph = bool(profile.get("dehyphenate", True))
    paras: list[Para] = []
    prev: Line | None = None
    for l in lines:
        if l.table is not None:
            paras.append(Para([], l.page, [l.page], table=l.table))
            prev = l
            continue
        words = [(w.text, w.bold) for w in l.words]
        if starts_new(prev, l, section_rx, standalone) or not paras or paras[-1].table is not None:
            paras.append(Para(words, l.page, [l.page]))
        else:
            p = paras[-1]
            if l.page not in p.pages:
                p.pages.append(l.page)
            last, lb = p.words[-1]
            first, fb = words[0]
            if dehyph and (last.endswith("-") or last.endswith("­")) and len(last) > 1:
                if last.endswith("­"):
                    merged_text, merged = last[:-1] + first, True
                    stats.hyphen_joined += 1
                else:
                    merged_text, merged = join_hyphen(last, first, vocab, stats)
                if merged:
                    p.words[-1] = (merged_text, lb and fb)
                    p.words.extend(words[1:])
                else:
                    p.words.extend(words)
            else:
                p.words.extend(words)
        prev = l
    return paras


# ---------------------------------------------------------------------------
# Chapter tables of contents


def extract_tocs(lines: list[Line], profile: Profile, warnings: list[str]) -> list[Line]:
    cfg = profile.section("toc")
    if not cfg.get("start_line"):
        return lines
    start_rx = re.compile(cfg["start_line"])
    entry_rx = re.compile(cfg["entry"])
    sec_rx = re.compile((profile.section("headings").get("section") or {}).get("first_word", r"$^"))
    chap_rx = re.compile((profile.section("headings").get("chapter") or {}).get("line", r"$^"))
    out: list[Line] = []
    chapter = None
    since_chapter: int | None = None
    i = 0
    counts: Counter = Counter()
    while i < len(lines):
        l = lines[i]
        if l.table is None and chap_rx.match(l.text):
            m = re.search(r"(\d+[A-Z]?)$", l.text)
            chapter = m.group(1) if m else chapter
        labeled = l.table is None and start_rx.match(l.text)
        unlabeled = (
            not labeled
            and l.table is None
            and since_chapter is not None
            and since_chapter <= 4
            and _looks_like_toc(lines, i, entry_rx)
        )
        if l.table is None and chap_rx.match(l.text):
            since_chapter = 0
        elif since_chapter is not None:
            since_chapter += 1
        if labeled or unlabeled:
            rows: list[list[str]] = []
            pages = [l.page]
            j = i + 1 if labeled else i
            after_sub = False
            while j < len(lines):
                c = lines[j]
                if c.table is not None:
                    break
                t = c.text
                first = c.words[0]
                if (first.bold and sec_rx.match(first.text)) or (first.bold and chap_rx.match(t)):
                    break
                if entry_rx.match(t) and t.startswith("SUBCHAPTER") and _subchapter_starts_body(lines, j, sec_rx, entry_rx):
                    break
                if entry_rx.match(t):
                    m = re.match(r"^(SUBCHAPTER \d+[A-Z]?|\S+)\s*(.*)$", t)
                    rows.append([m.group(1), m.group(2)])
                    after_sub = t.startswith("SUBCHAPTER")
                elif rows and after_sub:
                    rows[-1][1] = (rows[-1][1] + " " + t).strip()
                    after_sub = False
                elif t.upper() == t and re.search(r"[A-Z]{3}", t) and len(t) < 60 and not first.bold:
                    # Group label inside a TOC, e.g. "CITY ATTORNEY".
                    rows.append(["", t])
                elif rows and (c.x0 > c.zone_left + 10 or (len(t) < 45 and not first.bold)):
                    rows[-1][1] = (rows[-1][1] + " " + t).strip()
                else:
                    break
                if c.page not in pages:
                    pages.append(c.page)
                j += 1
            if rows:
                counts[chapter] += 1
                suffix = "" if counts[chapter] == 1 else f"-{counts[chapter]}"
                tid = f"toc-ch{chapter}{suffix}" if chapter else f"toc-p{l.page}{suffix}"
                t = Table(
                    id=tid,
                    title=l.text if labeled else "",
                    header=["", ""],
                    rows=rows,
                    page=l.page,
                    pages=pages,
                    kind="toc",
                    chapter=chapter,
                    has_title=True,
                )
                t.region_text = "\n".join(x.text for x in lines[i:j])
                t.has_title = bool(labeled)
                tl = Line([], l.page, zone=l.zone, table=t)
                tl.zone_left, tl.zone_right = l.zone_left, l.zone_right
                out.append(tl)
                i = j
                continue
            warnings.append(f"p{l.page}: 'TABLE' line with no entries after it; kept as text")
        out.append(l)
        i += 1
    return out


def extract_row_tables(lines: list[Line], profile: Profile, warnings: list[str]) -> list[Line]:
    """Turn runs of consecutive lines that match a profile row pattern (for
    example "Hourly 36.95 40.41 ...") into tables. Cells are the line's words;
    the first cell is the row label. A run whose rows have different cell
    counts is left as text and reported."""
    specs = profile.get("row_tables") or []
    if not specs:
        return lines
    rxs = [re.compile(sp["row_line"]) for sp in specs]
    out: list[Line] = []
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.table is None and any(rx.match(l.text) for rx in rxs):
            j = i
            while j < len(lines) and lines[j].table is None and lines[j].page == l.page and any(rx.match(lines[j].text) for rx in rxs):
                j += 1
            run = lines[i:j]
            if len(run) >= 2:
                rows = [[w.text for w in r.words] for r in run]
                if len({len(r) for r in rows}) == 1:
                    t = Table(
                        id="",
                        title="",
                        header=[""] * len(rows[0]),
                        rows=rows,
                        page=l.page,
                        bbox=(min(r.x0 for r in run), run[0].top, max(r.x1 for r in run), run[-1].bottom),
                        kind="text_rows",
                    )
                    t.region_text = "\n".join(r.text for r in run)
                    tl = Line([], l.page, zone=l.zone, table=t)
                    tl.zone_left, tl.zone_right = l.zone_left, l.zone_right
                    out.append(tl)
                    i = j
                    continue
                warnings.append(f"p{l.page}: rows {run[0].text[:30]!r}... have different cell counts; kept as text")
            out.extend(run)
            i = j
            continue
        out.append(l)
        i += 1
    return out


def _looks_like_toc(lines: list[Line], i: int, entry_rx: re.Pattern) -> bool:
    """An unlabeled chapter TOC: at least 3 of the next 5 lines are short,
    non-bold section entries."""
    hits = 0
    for l in lines[i : i + 5]:
        if l.table is not None or not l.words:
            break
        if entry_rx.match(l.text) and not l.words[0].bold and len(l.text) < 70:
            hits += 1
    return hits >= 3 and entry_rx.match(lines[i].text) is not None and not lines[i].words[0].bold


def _subchapter_starts_body(lines: list[Line], j: int, sec_rx: re.Pattern, entry_rx: re.Pattern) -> bool:
    """A SUBCHAPTER line followed (after its title lines) by a bold section
    heading is the body heading, not a TOC row."""
    for l in lines[j + 1 : j + 4]:
        if l.table is not None or not l.words:
            return False
        if l.words[0].bold and sec_rx.match(l.words[0].text):
            return True
        if entry_rx.match(l.text):
            return False
    return False


# ---------------------------------------------------------------------------
# Output


@dataclass
class Element:
    kind: str  # heading | text | history | chapter_history | table
    text: str
    page: int
    pages: list[int]
    level: int = 0
    section: str | None = None
    table: Table | None = None


@dataclass
class Document:
    text: str
    page_map: list[dict]
    headings: list[dict]
    sections: list[dict]
    tables: list[Table]
    table_offsets: dict
    history_notes: list[dict]
    chapters: dict
    warnings: list[str]
    stats: Stats


def classify(paras: list[Para], profile: Profile, warnings: list[str], stats: Stats) -> list[Element]:
    hcfg = profile.section("headings")
    sec_cfg = hcfg.get("section") or {}
    sec_rx = re.compile(sec_cfg["first_word"]) if sec_cfg.get("first_word") else None
    chap_rx = re.compile(hcfg["chapter"]["line"]) if hcfg.get("chapter") else None
    sub_rx = re.compile(hcfg["subchapter"]["line"]) if hcfg.get("subchapter") else None
    chap_bold = bool((hcfg.get("chapter") or {}).get("require_bold", True))
    sub_bold = bool((hcfg.get("subchapter") or {}).get("require_bold", True))
    bold_lines = [(int(b["level"]), re.compile(b["line"])) for b in hcfg.get("bold_lines") or []]
    hist = profile.section("history")
    h_start = [re.compile(p) for p in hist.get("start_patterns") or []]
    h_inline = [re.compile(p) for p in hist.get("inline_patterns") or []]
    h_chapter = re.compile(hist["chapter_pointer"]) if hist.get("chapter_pointer") else None
    collapse = bool(profile.get("collapse_dot_leaders"))

    els: list[Element] = []
    section: str | None = None
    depth = 0
    hist_paras = 0
    i = 0

    def norm(text: str) -> str:
        if collapse:
            new, n = re.subn(r"\s*\.{4,}\s*", " .... ", text)
            stats.dot_leaders_collapsed += n
            text = new
        return " ".join(text.split())

    while i < len(paras):
        p = paras[i]
        if p.table is not None:
            els.append(Element("table", "", p.page, p.pages, table=p.table))
            i += 1
            continue
        text = norm(p.text)
        words = p.words
        all_bold = all(b for _, b in words)

        # Chapter and subchapter headings (bold "CHAPTER 59" + bold caps title lines).
        for rx, level, label, need_bold in ((chap_rx, 1, "chapter", chap_bold), (sub_rx, 2, "subchapter", sub_bold)):
            if rx and (all_bold or not need_bold) and rx.match(text):
                title_parts = []
                k = i + 1
                while k < len(paras) and len(title_parts) < 3:
                    q = paras[k]
                    if (
                        q.table is None
                        and q.words
                        and (all(b for _, b in q.words) or not need_bold)
                        and q.text.upper() == q.text
                        and len(q.text) < 90
                        and not (sec_rx and sec_rx.match(q.words[0][0]))
                        and not rx.match(q.text)
                        and not (chap_rx and chap_rx.match(q.text))
                        and not (sub_rx and sub_rx.match(q.text))
                    ):
                        title_parts.append(norm(q.text))
                        k += 1
                    else:
                        break
                heading = text + (": " + " ".join(title_parts) if title_parts else "")
                if depth > 0:
                    warnings.append(f"p{p.page}: history note before {heading!r} has unbalanced parentheses")
                    depth = 0
                els.append(Element("heading", heading, p.page, p.pages, level=level, section=None))
                if label == "chapter":
                    section = None
                i = k
                break
        else:
            # Section heading: bold "59-11." + bold title, cut before "1." style markers.
            if sec_rx and words and words[0][1] and sec_rx.match(words[0][0]):
                n = 1
                while n < len(words) and words[n][1] and not SUBSECTION_WORD.match(words[n][0]):
                    n += 1
                    if _closes_title(words[n - 1][0]):
                        break
                title = norm(" ".join(w for w, _ in words[:n]))
                body = norm(" ".join(w for w, _ in words[n:]))
                if depth > 0:
                    warnings.append(f"p{p.page}: history note before s. {words[0][0]} has unbalanced parentheses")
                    depth = 0
                section = words[0][0].rstrip(".")
                els.append(Element("heading", title, p.page, p.pages, level=3, section=section))
                if body:
                    els.extend(_split_history(body, p, section, h_start, h_inline))
                    depth = _depth_after(els)
                i += 1
                continue
            # Bold whole-line headings (positions, salary).
            matched = False
            if all_bold:
                for level, rx in bold_lines:
                    if rx.match(text):
                        els.append(Element("heading", text, p.page, p.pages, level=level, section=text))
                        section = text
                        matched = True
                        break
            if matched:
                i += 1
                continue
            # History continuation, chapter pointer, history start, or body text.
            if depth > 0:
                els.append(Element("history", text, p.page, p.pages, section=section))
                depth += text.count("(") - text.count(")")
                hist_paras += 1
                if hist_paras > 60:
                    warnings.append(f"p{p.page}: history note in s. {section} runs over 60 paragraphs; closed")
                    depth = 0
                i += 1
                continue
            if h_chapter and h_chapter.match(text):
                ch = h_chapter.match(text).group(1)
                els.append(Element("chapter_history", text, p.page, p.pages, section=ch))
                i += 1
                continue
            new = _split_history(text, p, section, h_start, h_inline)
            els.extend(new)
            depth = _depth_after(els) if any(e.kind == "history" for e in new) else 0
            hist_paras = 0
            i += 1
    return els


TITLE_ABBREV = {"wis", "stats", "st", "no", "nos", "co", "inc", "ch", "sec", "s", "ss", "adm", "dept", "vs", "mr", "mrs", "dr", "ave", "u.s", "e.g", "i.e", "etc", "sub", "par", "subd", "art", "ord"}


def _closes_title(word: str) -> bool:
    """True when a bold title word ends the section title ("Arrest.")."""
    if not word.endswith("."):
        return False
    stem = word[:-1].lower().strip("(\"'“”")
    return len(stem) > 1 and stem not in TITLE_ABBREV and not re.fullmatch(r"[a-z]", stem)


def _split_history(text: str, p: Para, section, h_start, h_inline) -> list[Element]:
    if any(rx.match(text) for rx in h_start):
        return [Element("history", text, p.page, p.pages, section=section)]
    pos = None
    for rx in h_inline:
        m = rx.search(text)
        if m and m.start() > 0 and (pos is None or m.start() < pos):
            pos = m.start()
    if pos is not None:
        body, note = text[:pos].rstrip(), text[pos:]
        return [
            Element("text", body, p.page, p.pages, section=section),
            Element("history", note, p.page, p.pages, section=section),
        ]
    return [Element("text", text, p.page, p.pages, section=section)]


def _depth_after(els: list[Element]) -> int:
    """Open-parenthesis depth of the history note that ends the element list."""
    depth = 0
    for e in reversed(els):
        if e.kind != "history":
            break
        depth += e.text.count("(") - e.text.count(")")
    return max(depth, 0)


_REF_SECTION = re.compile(r"\bSections?\s+(\d+[A-Za-z]?(?:-[\w.]+)+)")
_REF_LEAD = re.compile(r"^\(?(?:HISTORY:\s*)?(\d+[A-Za-z]?(?:-[\w.]+)+)\s+(?:am|cr|rc|rn|rp|ra|rec)\b", re.I)


def write_document(els: list[Element], tables: list[Table], warnings: list[str], stats: Stats) -> Document:
    out: list[str] = []
    pos = 0
    page_marks: list[tuple[int, int]] = []
    headings, sections, history_notes = [], [], []
    table_offsets: dict = {}
    chapters: dict = {}
    cur_section: dict | None = None
    cur_chapter: str | None = None

    def emit(s: str, page: int, blank_before: bool = False, blank_after: bool = False):
        nonlocal pos
        if blank_before and out and not out[-1].endswith("\n\n"):
            out.append("\n")
            pos += 1
        start = pos
        page_marks.append((start, page))
        out.append(s + "\n")
        pos += len(s) + 1
        if blank_after:
            out.append("\n")
            pos += 1
        return start

    for e in els:
        if e.kind == "heading":
            hashes = "#" * max(1, min(e.level, 6))
            off = emit(f"{hashes} {e.text}", e.page, blank_before=True)
            h = {"level": e.level, "text": e.text, "offset": off, "page": e.page}
            headings.append(h)
            m = re.match(r"^CHAPTER (\d+[A-Z]?)", e.text)
            if e.level == 1 and m:
                cur_chapter = m.group(1)
                chapters.setdefault(cur_chapter, {"heading": e.text, "pointer": [], "tables": []})
            if e.level == 3 or e.section is not None:
                cur_section = {
                    "id": e.section or e.text,
                    "heading": e.text,
                    "level": e.level,
                    "offset": off,
                    "page": e.page,
                    "chapter": cur_chapter,
                    "history_notes": 0,
                }
                sections.append(cur_section)
        elif e.kind == "text":
            emit(e.text, e.page)
        elif e.kind == "history":
            label = f"s. {cur_section['id']}" if cur_section else "no section"
            off = emit(f"[HISTORY {label}] {e.text}", e.page)
            starts_note = e.text.startswith("(")
            if cur_section is not None and starts_note:
                cur_section["history_notes"] += 1
            if starts_note:
                history_notes.append({"section": cur_section["id"] if cur_section else None, "offset": off, "page": e.page})
            if cur_section is not None:
                refs = _REF_SECTION.findall(e.text) + ([_REF_LEAD.match(e.text).group(1)] if _REF_LEAD.match(e.text) else [])
                sid = cur_section["id"]
                if refs and not any(r == sid or r.startswith(sid + "-") or r.startswith(sid + ".") for r in refs):
                    warnings.append(
                        f"p{e.page}: history line under s. {sid} names {', '.join(sorted(set(refs)))[:60]}"
                    )
        elif e.kind == "chapter_history":
            ch = e.section
            off = emit(f"[HISTORY ch. {ch}] {e.text}", e.page)
            chapters.setdefault(ch, {"heading": None, "pointer": [], "tables": []})["pointer"].append(off)
            history_notes.append({"chapter": ch, "offset": off, "page": e.page, "kind": "pointer"})
        elif e.kind == "table":
            t = e.table
            off = emit(render_block(t), e.page, blank_before=True, blank_after=True)
            table_offsets[t.id] = off
            if t.kind == "legislative_history":
                ch = t.chapter or cur_chapter
                chapters.setdefault(ch, {"heading": None, "pointer": [], "tables": []})["tables"].append(t.id)
                history_notes.append({"chapter": ch, "offset": off, "page": e.page, "kind": "table", "table": t.id})

    text = "".join(out)
    # Page map: contiguous runs of the same page.
    page_map = []
    for off, page in page_marks:
        if page_map and page_map[-1]["page"] == page:
            continue
        if page_map:
            page_map[-1]["end"] = off
        page_map.append({"page": page, "start": off, "end": None})
    if page_map:
        page_map[-1]["end"] = len(text)
    for s in sections:
        ch = chapters.get(s["chapter"]) if s.get("chapter") else None
        s["covered_by_chapter_history"] = bool(ch and (ch["pointer"] or ch["tables"]))
    return Document(text, page_map, headings, sections, tables, table_offsets, history_notes, chapters, warnings, stats)

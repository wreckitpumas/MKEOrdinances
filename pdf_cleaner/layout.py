"""Page layout: characters to lines, header and footer removal, column order,
and table regions.

All text comes from the PDF text layer (pdfplumber characters). Nothing here
generates or rewrites text. The steps are:

1. Characters are grouped into rows and words by position. Our own grouping is
   used (not pdfplumber's) because the survey found characters with slightly
   skewed matrices that pdfplumber treats as vertical text, and table headers
   that are truly rotated 90 degrees.
2. Rows in the header and footer zones that match the profile patterns are removed.
3. Legislative history tables (space-aligned text) and ruled tables are cut out
   as table objects and placed back into the flow at their position.
4. Column gutters are detected per page and lines are put in reading order.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from .profile import Profile
from .tables import Table, grid_to_table

BOLD_RX = re.compile(r"bold|black|heavy", re.I)


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    bold: bool


@dataclass
class Line:
    words: list[Word]
    page: int
    zone: int = -1  # -1 = full width, otherwise column index
    zone_left: float = 0.0
    zone_right: float = 0.0
    table: Table | None = None
    rotated: bool = False

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return self.table.bbox[0] if self.table and self.table.bbox else min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return self.table.bbox[2] if self.table and self.table.bbox else max(w.x1 for w in self.words)

    @property
    def top(self) -> float:
        if self.table and self.table.bbox:
            return self.table.bbox[1]
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        if self.table and self.table.bbox:
            return self.table.bbox[3]
        return max(w.bottom for w in self.words)

    @property
    def size(self) -> float:
        if not self.words:
            return 10.0
        return statistics.median(w.size for w in self.words)


@dataclass
class PageResult:
    page: int
    lines: list[Line]
    removed_header: list[str] = field(default_factory=list)
    removed_footer: list[str] = field(default_factory=list)
    dropped_lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    columns: int = 1
    char_text: str = ""  # all text-layer characters on the page, for checks
    has_text_layer: bool = True


# ---------------------------------------------------------------------------
# Character grouping


def orientation(ch: dict) -> str:
    a, b, c, d = ch["matrix"][:4]
    if abs(a) >= abs(b):
        return "h"
    return "up" if b > 0 else "down"


def is_bold(ch: dict) -> bool:
    return bool(BOLD_RX.search(ch.get("fontname") or ""))


def _words_from_run(chars: list[dict], axis: str) -> list[Word]:
    """Split an ordered run of characters on one line into words.

    A word boundary is an explicit whitespace character in the text layer, or a
    gap wider than 0.2 x font size where the text layer has no space."""
    words: list[Word] = []
    cur: list[dict] = []

    def flush():
        if not cur:
            return
        text = "".join(c["text"] for c in cur)
        bold = sum(1 for c in cur if is_bold(c)) * 2 > len(cur)
        words.append(
            Word(
                text=text,
                x0=min(c["x0"] for c in cur),
                x1=max(c["x1"] for c in cur),
                top=min(c["top"] for c in cur),
                bottom=max(c["bottom"] for c in cur),
                size=statistics.median(c["size"] for c in cur),
                bold=bold,
            )
        )
        cur.clear()

    prev = None
    for c in chars:
        if c["text"].isspace() or c["text"] == "":
            flush()
            prev = None
            continue
        if prev is not None:
            if axis == "h":
                gap = c["x0"] - prev["x1"]
            elif axis == "up":
                gap = prev["top"] - c["bottom"]
            else:
                gap = c["top"] - prev["bottom"]
            if gap > 0.2 * max(c["size"], 1.0):
                flush()
        cur.append(c)
        prev = c
    flush()
    return words


def group_rows(chars: list[dict]) -> list[list[dict]]:
    """Cluster horizontal characters into rows by vertical center."""
    if not chars:
        return []
    items = sorted(chars, key=lambda c: ((c["top"] + c["bottom"]) / 2, c["x0"]))
    rows: list[list[dict]] = []
    ref = None
    tol = None
    for c in items:
        yc = (c["top"] + c["bottom"]) / 2
        if rows and abs(yc - ref) <= tol:
            rows[-1].append(c)
        else:
            rows.append([c])
            ref = yc
            tol = 0.4 * max(c["size"], 4.0)
    for r in rows:
        r.sort(key=lambda c: c["x0"])
    return rows


def rotated_runs(chars: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group rotated characters into vertical runs (one run per text line)."""
    runs: list[tuple[str, list[dict]]] = []
    for direction in ("up", "down"):
        cs = [c for c in chars if orientation(c) == direction]
        cs.sort(key=lambda c: ((c["x0"] + c["x1"]) / 2))
        groups: list[list[dict]] = []
        ref = None
        for c in cs:
            xc = (c["x0"] + c["x1"]) / 2
            if groups and abs(xc - ref) <= 0.4 * max(c["size"], 4.0):
                groups[-1].append(c)
            else:
                groups.append([c])
                ref = xc
        for g in groups:
            g.sort(key=lambda c: -c["bottom"] if direction == "up" else c["top"])
            runs.append((direction, g))
    return runs


def text_of_chars(chars: list[dict]) -> str:
    """Reading-order text of an arbitrary set of characters (any orientation)."""
    parts = []
    horiz = [c for c in chars if orientation(c) == "h"]
    for row in group_rows(horiz):
        ws = _words_from_run(row, "h")
        if ws:
            parts.append(" ".join(w.text for w in ws))
    for direction, run in rotated_runs(chars):
        ws = _words_from_run(run, direction)
        if ws:
            parts.append(" ".join(w.text for w in ws))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tables


def _dark(color) -> bool:
    if color is None:
        return False
    if isinstance(color, (int, float)):
        color = (color,)
    color = tuple(color)
    if len(color) == 1:
        return color[0] < 0.85
    if len(color) == 3:
        return sum(color) / 3 < 0.85
    if len(color) == 4:  # CMYK
        return max(color) > 0.15
    return True


def _visible_graphic(obj: dict) -> bool:
    if obj.get("object_type") not in ("rect", "line", "curve"):
        return True
    if obj.get("stroke") and _dark(obj.get("stroking_color")):
        return True
    if obj.get("fill") and _dark(obj.get("non_stroking_color")):
        w = obj["x1"] - obj["x0"]
        h = obj["bottom"] - obj["top"]
        return min(w, h) < 3  # thin filled rectangles act as rules
    return False


def _inside(ch: dict, bbox, pad: float = 1.0) -> bool:
    x0, top, x1, bottom = bbox
    xc = (ch["x0"] + ch["x1"]) / 2
    yc = (ch["top"] + ch["bottom"]) / 2
    return x0 - pad <= xc <= x1 + pad and top - pad <= yc <= bottom + pad


def find_ruled_tables(page, chars: list[dict], profile: Profile, pno: int) -> list[Table]:
    cfg = profile.section("tables")
    if not cfg.get("ruled"):
        return []
    target = page.filter(_visible_graphic) if cfg.get("edges", "visible") == "visible" else page
    try:
        found = target.find_tables()
    except Exception:
        return []
    out: list[Table] = []
    min_rows = int(cfg.get("min_rows", 2))
    min_cols = int(cfg.get("min_cols", 2))
    for t in found:
        if len(t.rows) < min_rows or len(t.columns) < min_cols:
            continue
        # Assign every character in the table region to a cell: the cell that
        # contains it, else the nearest cell. No character is left out.
        cells = [(ri, ci, cell) for ri, row in enumerate(t.rows) for ci, cell in enumerate(row.cells) if cell is not None]
        region = [c for c in chars if _inside(c, t.bbox)]
        assigned: dict[tuple[int, int], list[dict]] = {}
        for ch in region:
            best = None
            for ri, ci, cb in cells:
                if _inside(ch, cb, pad=0.5):
                    best = (ri, ci)
                    break
            if best is None and cells:
                xc = (ch["x0"] + ch["x1"]) / 2
                yc = (ch["top"] + ch["bottom"]) / 2

                def dist(cb):
                    dx = max(cb[0] - xc, 0, xc - cb[2])
                    dy = max(cb[1] - yc, 0, yc - cb[3])
                    return dx * dx + dy * dy

                ri, ci, _cb = min(cells, key=lambda x: dist(x[2]))
                best = (ri, ci)
            if best is not None:
                assigned.setdefault(best, []).append(ch)
        grid = []
        for ri, row in enumerate(t.rows):
            grid.append(
                [None if cell is None else text_of_chars(assigned.get((ri, ci), [])).replace("\n", " ") for ci, cell in enumerate(row.cells)]
            )
        nonempty_cols = max((sum(1 for c in r if c) for r in grid), default=0)
        if nonempty_cols < min_cols:
            continue
        table = grid_to_table(grid, table_id="", page=pno, bbox=tuple(t.bbox))
        table.region_text = text_of_chars(region)
        m = re.match(r"^Table\s+([\w.\-]+)", table.title)
        table.id = m.group(1) if m else ""
        out.append(table)
    return out


# ---------------------------------------------------------------------------
# Rows, header/footer


@dataclass
class Row:
    words: list[Word]
    chars: list[dict]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)


def build_rows(chars: list[dict]) -> list[Row]:
    rows = []
    for rc in group_rows(chars):
        ws = _words_from_run(rc, "h")
        if ws:
            rows.append(Row(ws, rc))
    rows.sort(key=lambda r: r.top)
    return rows


def strip_running_text(rows: list[Row], profile: Profile, result: PageResult) -> list[Row]:
    hdr = profile.section("header")
    ftr = profile.section("footer")
    h_pats = profile.patterns("header")
    f_pats = profile.patterns("footer")
    remove: set[int] = set()

    zone = float(hdr.get("zone_top_pt", 0) or 0)
    max_rows = int(hdr.get("max_rows", 0) or 0)
    if h_pats and zone > 0 and max_rows > 0:
        cands = [i for i, r in enumerate(rows[:max_rows]) if r.top < zone]
        matched = [i for i in cands if any(p.match(rows[i].text) for p in h_pats)]
        if matched:
            remove.update(matched)
            # A two-line running header: an adjacent row in the zone just above
            # or below a matched row belongs to the same header block.
            for i in cands:
                if i in remove:
                    continue
                for j in matched:
                    gap = abs(rows[i].top - rows[j].top)
                    if gap <= 1.6 * max(w.size for w in rows[j].words):
                        remove.add(i)
        for i in cands:
            if i not in remove and len(rows[i].text) < 50:
                result.warnings.append(f"p{result.page}: text in header zone kept (no pattern match): {rows[i].text[:80]!r}")
    fzone = float(ftr.get("zone_top_pt", 0) or 0)
    if f_pats and ftr.get("last_rows_any_position"):
        # Short pages (for example zoning tables) end above the footer zone; the
        # page footer is then simply the last row. Only exact pattern matches count.
        n_last = int(ftr.get("last_rows_any_position"))
        for i in range(max(0, len(rows) - n_last), len(rows)):
            if any(p.match(rows[i].text) for p in f_pats):
                remove.add(i)
    if f_pats and fzone > 0:
        for i, r in enumerate(rows):
            if i in remove:
                continue
            if r.top >= fzone:
                if any(p.match(r.text) for p in f_pats):
                    remove.add(i)
                elif len(r.text) < 50:
                    result.warnings.append(f"p{result.page}: text in footer zone kept (no pattern match): {r.text[:80]!r}")
    kept = []
    for i, r in enumerate(rows):
        if i in remove:
            (result.removed_header if r.top < zone and i < max_rows else result.removed_footer).append(r.text)
        else:
            kept.append(r)
    return kept


# ---------------------------------------------------------------------------
# Legislative history (space-aligned) tables


_CH_FROM_HEADER = [
    re.compile(r"(\d+[A-Z]?)\s*-+\s*\((?:HISTORY|History)\)"),
    re.compile(r"\((?:HISTORY|History)\)\s+(\d+[A-Z]?)\s*-"),
]


def chapter_from_header(texts: list[str]) -> str | None:
    for t in texts:
        for rx in _CH_FROM_HEADER:
            m = rx.search(t)
            if m:
                return m.group(1)
    return None


def find_text_tables(rows: list[Row], profile: Profile, result: PageResult, prev_table: Table | None) -> tuple[list[Row], list[Table]]:
    specs = profile.get("text_tables") or []
    if not specs:
        return rows, []
    tables: list[Table] = []
    for spec in specs:
        title_rx = re.compile(spec["title_line"])
        header_rx = re.compile(spec["header_line"])
        cont_rx = re.compile(spec.get("continuation_header", r"$^"))
        start = next((i for i, r in enumerate(rows) if title_rx.match(r.text)), None)
        is_cont = False
        if start is None:
            # Continuation page: running header says (HISTORY) and the rows look tabular.
            if any(cont_rx.search(h) for h in result.removed_header) and prev_table is not None and prev_table.kind == spec["kind"]:
                start = 0
                is_cont = True
            else:
                continue
        region = rows[start:]
        # Stop at a following chapter title, if any.
        stop = next((i for i, r in enumerate(region) if i > 0 and re.match(r"^CHAPTER \d+", r.text) and not is_cont and i > 3), None)
        if stop is not None:
            region = region[:stop]
        hdr_i = next((i for i, r in enumerate(region) if header_rx.match(r.text)), None)
        title_lines: list[str] = []
        notes: list[str] = []
        if hdr_i is not None:
            pre = region[:hdr_i]
            col_starts = [w.x0 for w in region[hdr_i].words]
            header = [w.text for w in region[hdr_i].words]
            data = region[hdr_i + 1 :]
        elif is_cont and prev_table.column_starts:
            pre, data = [], region
            col_starts = prev_table.column_starts
            header = list(prev_table.header)
        else:
            result.warnings.append(f"p{result.page}: legislative history title found but no column header row; kept as text")
            continue
        for r in pre:
            if title_rx.match(r.text) or re.match(r"^CHAPTER \d+[A-Z]?$", r.text):
                title_lines.append(r.text)
            else:
                notes.append(r.text)
        body = []
        for r in data:
            cells = [""] * len(col_starts)
            for w in r.words:
                idx = 0
                for k, x in enumerate(col_starts):
                    if w.x0 >= x - 6:
                        idx = k
                cells[idx] = (cells[idx] + " " + w.text).strip()
            body.append(cells)
        chapter = None
        for tl in title_lines:
            m = re.match(r"^CHAPTER (\d+[A-Z]?)$", tl)
            if m:
                chapter = m.group(1)
        chapter = chapter or chapter_from_header(result.removed_header) or (prev_table.chapter if is_cont and prev_table else None)
        tid = spec.get("id_template", "texttable-p{page}").format(chapter=chapter or f"unknown-p{result.page}", page=result.page)
        t = Table(
            id=tid,
            title=" ".join(title_lines) if title_lines else (prev_table.title if is_cont and prev_table else ""),
            header=header,
            rows=body,
            page=result.page,
            bbox=(min(w.x0 for r in region for w in r.words), region[0].top, max(w.x1 for r in region for w in r.words), region[-1].bottom),
            kind=spec["kind"],
            notes=notes,
            chapter=chapter,
            has_title=bool(title_lines),
            column_starts=col_starts,
            header_copied=hdr_i is None,
        )
        t.region_text = "\n".join(r.text for r in region)
        tables.append(t)
        ids = {id(r) for r in region}
        rows = [r for r in rows if id(r) not in ids]
    return rows, tables


# ---------------------------------------------------------------------------
# Columns


def find_gutters(rows: list[list[Word]], lo: float, hi: float, width: float, depth: int = 0, min_side_chars: float = 25) -> list[float]:
    """Find vertical gutters between lo and hi.

    For each candidate x, count rows that have words on both sides with no word
    crossing x ("gapped"), and rows with a word crossing x. A gutter needs many
    gapped rows, few crossing rows, and real text (not short number columns) on
    both sides."""
    if depth > 2 or hi - lo < 0.36 * width:
        return []
    min_col = 0.18 * width
    sub_rows = [[w for w in r if w.x0 >= lo - 1 and w.x1 <= hi + 1] for r in rows]
    sub_rows = [r for r in sub_rows if r]
    if len(sub_rows) < 4:
        return []
    x_start, x_end = int(lo + min_col), int(hi - min_col)
    if x_end <= x_start:
        return []
    n = x_end - x_start
    gapped = [0] * n
    crossing = [0] * n
    for r in sub_rows:
        cover = bytearray(n)
        for w in r:
            a = max(0, int(w.x0 - 0.5) + 1 - x_start) if w.x0 - 0.5 >= x_start else 0
            b = min(n - 1, int(w.x1 - 0.5) - x_start) if w.x1 - 0.5 < x_end else n - 1
            # x = xi + 0.5 is crossed when x0 < x < x1
            for i in range(max(a, 0), b + 1):
                x = x_start + i + 0.5
                if w.x0 < x < w.x1:
                    cover[i] = 1
        min_x1 = min(w.x1 for w in r)
        max_x0 = max(w.x0 for w in r)
        for i in range(n):
            if cover[i]:
                crossing[i] += 1
            else:
                x = x_start + i + 0.5
                if min_x1 <= x <= max_x0:
                    gapped[i] += 1
    scores = [g - 3 * c for g, c in zip(gapped, crossing)]
    best = max(scores)
    xs = [x_start + i + 0.5 for i, s in enumerate(scores) if s == best]
    bi = scores.index(best)
    g_count, c_count = gapped[bi], crossing[bi]
    if g_count < 3 or g_count < 2.5 * c_count:
        return []
    runs, cur = [], [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] <= 1.01:
            cur.append(x)
        else:
            runs.append(cur)
            cur = [x]
    runs.append(cur)
    run = max(runs, key=len)
    g = (run[0] + run[-1]) / 2
    # A gutter is a wide band of white space, not a coincidence of word spaces
    # in justified text: the median gap at g on gapped rows must be >= 18pt.
    gaps = []
    for r in sub_rows:
        lw = [w.x1 for w in r if w.x1 <= g]
        rw = [w.x0 for w in r if w.x0 >= g]
        if lw and rw and not any(w.x0 < g < w.x1 for w in r):
            gaps.append(min(rw) - max(lw))
    if not gaps or statistics.median(gaps) < 18:
        return []
    # Real text on both sides, not a short column of numbers or labels:
    # average text length per row on each side, over rows with text on that side.
    left_len = right_len = left_rows_n = right_rows_n = 0
    for r in sub_rows:
        if any(w.x0 < g < w.x1 for w in r):
            continue
        lw = [w for w in r if w.x1 <= g]
        rw = [w for w in r if w.x0 >= g]
        if lw:
            left_rows_n += 1
            left_len += sum(len(w.text) + 1 for w in lw)
        if rw:
            right_rows_n += 1
            right_len += sum(len(w.text) + 1 for w in rw)
    if not left_rows_n or not right_rows_n or left_len / left_rows_n < min_side_chars or right_len / right_rows_n < min_side_chars:
        return []
    left_rows = [[w for w in r if w.x1 <= g] for r in rows]
    right_rows = [[w for w in r if w.x0 >= g] for r in rows]
    return (
        find_gutters(left_rows, lo, g, width, depth + 1, min_side_chars)
        + [g]
        + find_gutters(right_rows, g, hi, width, depth + 1, min_side_chars)
    )


def _spans_gutter(words: list[Word], gutters: list[float]) -> bool:
    """A row whose words sit on both sides of a gutter with only a normal word
    space between them (for example a centered chapter title) is full width."""
    for g in gutters:
        left = [w for w in words if w.x1 <= g]
        right = [w for w in words if w.x0 >= g]
        if left and right:
            gap = min(w.x0 for w in right) - max(w.x1 for w in left)
            size = statistics.median(w.size for w in words)
            if gap < 1.0 * size:
                return True
    return False


def zone_lines(rows: list[Row], gutters: list[float], pno: int) -> list[Line]:
    bounds = [float("-inf")] + gutters + [float("inf")]
    lines: list[Line] = []
    for r in rows:
        if gutters and any(any(w.x0 < g < w.x1 for w in r.words) for g in gutters):
            lines.append(Line(r.words, pno, zone=-1))
            continue
        if gutters and _spans_gutter(r.words, gutters):
            lines.append(Line(r.words, pno, zone=-1))
            continue
        if not gutters:
            lines.append(Line(r.words, pno, zone=-1))
            continue
        for zi in range(len(bounds) - 1):
            ws = [w for w in r.words if bounds[zi] <= (w.x0 + w.x1) / 2 < bounds[zi + 1]]
            if ws:
                lines.append(Line(ws, pno, zone=zi))
    return lines


def reading_order(lines: list[Line], ncols: int) -> list[Line]:
    if ncols <= 1:
        return sorted(lines, key=lambda l: (l.top, l.x0))
    full = sorted([l for l in lines if l.zone == -1], key=lambda l: l.top)
    cols = {z: sorted([l for l in lines if l.zone == z], key=lambda l: l.top) for z in range(ncols)}
    out: list[Line] = []
    prev = float("-inf")
    for f in full + [None]:
        limit = f.top if f else float("inf")
        for z in range(ncols):
            out.extend(l for l in cols[z] if prev <= l.top < limit)
        if f:
            out.append(f)
            prev = f.top
    return out


def set_zone_edges(lines: list[Line]) -> None:
    by_zone: dict[int, list[Line]] = {}
    for l in lines:
        if l.table is None:
            by_zone.setdefault(l.zone, []).append(l)
    for z, ls in by_zone.items():
        x0s = [round(l.x0) for l in ls]
        left = statistics.mode(x0s) if x0s else 0
        left = min(left, min(x0s))  if len(ls) < 3 else left
        x1s = sorted(l.x1 for l in ls)
        right = x1s[int(0.9 * (len(x1s) - 1))] if x1s else 0
        for l in ls:
            l.zone_left, l.zone_right = float(left), float(right)
    for l in lines:
        if l.table is not None:
            l.zone_left, l.zone_right = l.x0, l.x1


# ---------------------------------------------------------------------------
# Page entry point


def dedupe_chars(chars: list[dict], tol: float = 1.0) -> list[dict]:
    """Drop duplicate glyphs (same text, font and size within tol points), as
    produced by overprinted "fake bold". Same rule as pdfplumber's
    dedupe_chars, implemented with a position bucket for speed."""
    seen: dict[tuple, list[dict]] = {}
    out = []
    for c in chars:
        key = (c["text"], c.get("fontname"), round(c.get("size", 0), 1))
        bx, by = int(c["x0"] // tol), int(c["top"] // tol)
        dup = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for o in seen.get(key + (bx + dx, by + dy), ()):
                    if abs(o["x0"] - c["x0"]) <= tol and abs(o["top"] - c["top"]) <= tol:
                        dup = True
                        break
                if dup:
                    break
            if dup:
                break
        if dup:
            continue
        seen.setdefault(key + (bx, by), []).append(c)
        out.append(c)
    return out


def extract_page(page, pno: int, profile: Profile, prev_text_table: Table | None) -> PageResult:
    res = PageResult(page=pno, lines=[])
    chars = dedupe_chars([c for c in page.chars if c.get("text") is not None])
    res.char_text = "".join(c["text"] for c in chars)
    if not chars:
        res.has_text_layer = False
        return res

    # Tables first, so their characters leave the text flow.
    ruled = find_ruled_tables(page, chars, profile, pno)
    if ruled:
        chars = [c for c in chars if not any(_inside(c, t.bbox) for t in ruled)]

    horiz = [c for c in chars if orientation(c) == "h"]
    rotated = [c for c in chars if orientation(c) != "h"]
    rows = build_rows(horiz)
    rows = strip_running_text(rows, profile, res)
    rows, text_tables = find_text_tables(rows, profile, res, prev_text_table)
    res.tables = ruled + text_tables

    # Rotated text outside tables (rare) becomes its own line at its position.
    rot_lines = []
    for direction, run in rotated_runs(rotated):
        ws = _words_from_run(run, direction)
        if ws:
            rot_lines.append(Line(ws, pno, zone=-1, rotated=True))
    if rot_lines:
        res.warnings.append(f"p{pno}: {len(rot_lines)} rotated text line(s) outside tables kept in place")

    gutters: list[float] = []
    if profile.section("columns").get("detect", True):
        min_side = float(profile.section("columns").get("min_side_chars", 25))
        gutters = find_gutters([r.words for r in rows], 0.0, float(page.width), float(page.width), min_side_chars=min_side)
    res.columns = len(gutters) + 1
    lines = zone_lines(rows, gutters, pno) + rot_lines
    for t in res.tables:
        tl = Line([], pno, zone=-1, table=t)
        if gutters and t.bbox:
            bounds = [float("-inf")] + gutters + [float("inf")]
            for zi in range(len(bounds) - 1):
                if bounds[zi] <= t.bbox[0] and t.bbox[2] <= bounds[zi + 1]:
                    tl.zone = zi
        lines.append(tl)

    # Editorial placeholders such as "[This page is blank.]".
    drops = [re.compile(p) for p in (profile.get("drop_line_patterns") or [])]
    kept = []
    for l in lines:
        if l.table is None and any(p.match(l.text) for p in drops):
            res.dropped_lines.append(l.text)
        else:
            kept.append(l)
    ordered = reading_order(kept, res.columns)
    set_zone_edges(ordered)
    res.lines = ordered
    return res

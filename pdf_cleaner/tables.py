"""Table model, deterministic cleanup, markdown rendering, and the fidelity check."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A cell that is one number split by a stray space in the text layer,
# e.g. "7 8,798.02" for 78,798.02. Only joined when the result is a
# well-formed number with a comma or decimal point.
_SPLIT_NUMBER = re.compile(r"^\$?[\d ,.]+$")
_WELL_FORMED_NUMBER = re.compile(r"^\$?\d{1,3}(?:,\d{3})*(?:\.\d+)?$|^\$?\d+\.\d+$")
_GARBLED = re.compile(r"(?:\b\w\b ){4,}")


@dataclass
class Table:
    id: str
    title: str
    header: list[str]
    rows: list[list[str]]
    page: int
    bbox: tuple[float, float, float, float] | None = None
    kind: str = "ruled"  # ruled | legislative_history | toc
    notes: list[str] = field(default_factory=list)  # caption lines kept inside the block
    pages: list[int] = field(default_factory=list)
    method: str = "deterministic"  # deterministic | llm
    fidelity: str = "not_applicable"  # pass | fail | not_applicable
    fidelity_detail: str = ""
    llm_status: str | None = None  # None (not sent) | accepted | rejected: <reason>
    quality_issues: list[str] = field(default_factory=list)
    number_repairs: int = 0
    merged_into: str | None = None
    region_text: str = ""  # text layer of the table region (for fidelity / LLM)
    chapter: str | None = None
    has_title: bool = False
    header_copied: bool = False  # header row copied from the previous page's table
    column_starts: list[float] | None = None  # legislative history column x positions

    def __post_init__(self):
        if not self.pages:
            self.pages = [self.page]

    @property
    def ncols(self) -> int:
        widths = [len(self.header)] + [len(r) for r in self.rows]
        return max(widths) if widths else 0

    def all_cell_text(self) -> str:
        parts = [self.title] + self.notes + self.header + [c for r in self.rows for c in r]
        return " ".join(p for p in parts if p)


def clean_cell(text: str | None) -> tuple[str, int]:
    """Normalize whitespace in a cell and repair split numbers. Returns (text, repairs)."""
    if not text:
        return "", 0
    t = " ".join(text.split())
    if " " in t and _SPLIT_NUMBER.match(t):
        joined = t.replace(" ", "")
        if ("," in joined or "." in joined) and _WELL_FORMED_NUMBER.match(joined):
            return joined, 1
    return t, 0


def grid_to_table(grid: list[list[str | None]], *, table_id: str, page: int, bbox) -> Table:
    """Turn a cell grid into title, notes, header and rows.

    Leading rows with at most one non-empty cell are title or caption rows.
    The first row with two or more non-empty cells is the header unless it
    looks like data (contains a number), in which case the header is blank.
    """
    repairs = 0
    cleaned: list[list[str]] = []
    for row in grid:
        out = []
        for cell in row:
            c, n = clean_cell(cell)
            repairs += n
            out.append(c)
        cleaned.append(out)
    cleaned = [r for r in cleaned if any(r)]
    ncols = max((len(r) for r in cleaned), default=0)
    cleaned = [r + [""] * (ncols - len(r)) for r in cleaned]

    lead: list[str] = []
    i = 0
    while i < len(cleaned) and sum(1 for c in cleaned[i] if c) <= 1:
        lead.append(next((c for c in cleaned[i] if c), ""))
        i += 1
    body = cleaned[i:]
    title, notes = "", []
    if lead:
        title = lead[0]
        rest = lead[1:]
        if re.match(r"^Table\s+\S+", title) and rest:
            title = f"{title} {rest[0]}"
            rest = rest[1:]
        notes = rest
    header: list[str] = [""] * ncols
    if body:
        first = body[0]
        looks_like_data = any(re.search(r"\d", c) and _SPLIT_NUMBER.match(c) for c in first if c)
        if not looks_like_data:
            header, body = first, body[1:]

    t = Table(
        id=table_id,
        title=title,
        header=header,
        rows=body,
        page=page,
        bbox=bbox,
        notes=notes,
        number_repairs=repairs,
        has_title=bool(title),
    )
    t.quality_issues = quality_issues(t)
    return t


def quality_issues(t: Table) -> list[str]:
    """Reasons a deterministic table may be wrong. Any issue makes the table
    eligible for the optional model-assisted pass."""
    issues = []
    cells = t.header + [c for r in t.rows for c in r]
    if any(_GARBLED.search(c) for c in cells if c):
        issues.append("garbled_spacing")
    if t.rows and t.ncols >= 3:
        full = [r for r in t.rows if sum(1 for c in r if c) >= 0.8 * t.ncols]
        # Several rows before the first full data row means a multi-level header.
        first_full = next((i for i, r in enumerate(t.rows) if r in full), None)
        if first_full is not None and first_full >= 2:
            issues.append("multi_row_header")
    if t.header and any(t.header) and sum(1 for c in t.header if not c) >= max(2, t.ncols // 2):
        issues.append("sparse_header")
    if not t.rows:
        issues.append("no_body_rows")
    return issues


def _md_escape(cell: str) -> str:
    return cell.replace("|", "\\|").replace("\n", " ")


def to_markdown(t: Table) -> str:
    n = t.ncols or 1
    header = (t.header + [""] * n)[:n]
    lines = ["| " + " | ".join(_md_escape(c) for c in header) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(n)) + " |")
    for r in t.rows:
        r = (r + [""] * n)[:n]
        lines.append("| " + " | ".join(_md_escape(c) for c in r) + " |")
    return "\n".join(lines)


def render_block(t: Table) -> str:
    """The inline table format. See README: [TABLE id: title] ... [/TABLE]."""
    title = t.title or "(untitled)"
    out = [f"[TABLE {t.id}: {title}]"]
    out.extend(n for n in t.notes if n)
    out.append(to_markdown(t))
    out.append("[/TABLE]")
    return "\n".join(out)


def parse_markdown_table(text: str) -> tuple[list[str], list[list[str]]] | None:
    """Parse the first markdown pipe table in text into (header, rows)."""
    rows = []
    in_table = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and len(s) >= 2:
            in_table = True
            inner = s[1:-1]
            cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]
            rows.append(cells)
        elif in_table:
            break
    if len(rows) < 2:
        return None
    header, sep, body = rows[0], rows[1], rows[2:]
    if not all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in sep if c.strip()):
        return None
    return header, body


_TOKEN = re.compile(r"\S+")
_EDGE_PUNCT = "|"


def tokens(text: str) -> list[str]:
    return [t.strip(_EDGE_PUNCT) for t in _TOKEN.findall(text) if t.strip(_EDGE_PUNCT)]


def fidelity_check(candidate_cells: list[str], source_text: str, require_coverage: bool = True) -> tuple[bool, str]:
    """Every token in the candidate table must appear in the source text layer.

    A token may also match the concatenation of up to 3 consecutive source tokens
    (the text layer sometimes splits one number or word with a stray space).
    With require_coverage, every source token must also appear in the candidate
    (as a token, or as part of a concatenated token), so nothing is dropped.
    """
    src = tokens(source_text)
    allowed: set[str] = set(src)
    concat_parts: dict[str, tuple[str, ...]] = {}
    for i in range(len(src)):
        for k in (2, 3):
            if i + k <= len(src):
                joined = "".join(src[i : i + k])
                allowed.add(joined)
                concat_parts.setdefault(joined, tuple(src[i : i + k]))
    cand = [t for c in candidate_cells for t in tokens(c)]
    foreign = [t for t in cand if t not in allowed]
    if foreign:
        return False, "tokens not in text layer: " + ", ".join(sorted(set(foreign))[:10])
    if require_coverage:
        from collections import Counter

        have = Counter()
        for t in cand:
            if t in concat_parts and t not in src:
                have.update(concat_parts[t])
            else:
                have[t] += 1
        need = Counter(src)
        missing = need - have
        if missing:
            ex = ", ".join(sorted(missing)[:10])
            return False, f"{sum(missing.values())} text-layer tokens missing from table: {ex}"
    return True, "all tokens match the text layer"

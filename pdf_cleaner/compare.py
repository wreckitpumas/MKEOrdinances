"""Compare two cleaned .txt files of the same document.

Reports section headings present in only one file, tables present in only one
file, the word count difference per section, and every section whose text
changed beyond whitespace. Output is a short markdown summary.

Usage:
    python3 -m pdf_cleaner.compare OLD.txt NEW.txt [--out summary.md]

Sections are found by markdown heading lines ("# ", "## ", "### ") as written
by this tool. For an older extraction without them, lines that start with a
section number such as "999-999-9. Title." are also treated as headings.
A different rule can be given with --heading-regex.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_HEADING = r"^(?:#{1,6} .+|\d+[A-Za-z]?(?:-\d+[A-Za-z]?(?:\.\d+)?[a-z]?)+\.\s+[A-Z].*)$"
TABLE_OPEN = re.compile(r"^\[TABLE ([^:\]]+):.*\]$")
TABLE_CLOSE = "[/TABLE]"
SECTION_ID = re.compile(r"^(?:#{1,6} )?((?:CHAPTER|SUBCHAPTER|PART|SECTION) [\w.]+|\d+[A-Za-z]?(?:-\d+[A-Za-z]?(?:\.\d+)?[a-z]?)+\.?)")


def section_key(heading: str) -> str:
    m = SECTION_ID.match(heading)
    if m:
        return m.group(1).rstrip(".").upper() if m.group(1)[0].isalpha() else m.group(1).rstrip(".")
    return re.sub(r"^#{1,6} ", "", heading).strip()


def parse(text: str, heading_rx: re.Pattern) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Return (sections key -> body text, tables id -> block text, ordered keys)."""
    sections: dict[str, list[str]] = {}
    tables: dict[str, list[str]] = {}
    order: list[str] = []
    cur = "(before first heading)"
    sections[cur] = []
    order.append(cur)
    in_table: str | None = None
    for line in text.splitlines():
        if in_table is not None:
            tables[in_table].append(line)
            sections[cur].append(line)
            if line.strip() == TABLE_CLOSE:
                in_table = None
            continue
        m = TABLE_OPEN.match(line.strip())
        if m:
            tid = m.group(1).strip()
            k, n = tid, 2
            while k in tables:
                k = f"{tid}#{n}"
                n += 1
            in_table = k
            tables[k] = [line]
            sections[cur].append(line)
            continue
        if heading_rx.match(line.strip()):
            key = section_key(line.strip())
            k, n = key, 2
            while k in sections:
                k = f"{key}#{n}"
                n += 1
            cur = k
            sections[cur] = [line]
            order.append(cur)
            continue
        sections[cur].append(line)
    return {k: "\n".join(v) for k, v in sections.items()}, {k: "\n".join(v) for k, v in tables.items()}, order


def norm_ws(s: str) -> str:
    return " ".join(s.split())


def word_count(s: str) -> int:
    return len(re.findall(r"\w+", s))


def compare(old_text: str, new_text: str, heading_regex: str = DEFAULT_HEADING, old_name: str = "A", new_name: str = "B") -> str:
    rx = re.compile(heading_regex)
    a_secs, a_tabs, a_order = parse(old_text, rx)
    b_secs, b_tabs, b_order = parse(new_text, rx)
    only_a = [k for k in a_order if k not in b_secs]
    only_b = [k for k in b_order if k not in a_secs]
    common = [k for k in b_order if k in a_secs]
    changed, wc_rows = [], []
    for k in common:
        wa, wb = word_count(a_secs[k]), word_count(b_secs[k])
        if wa != wb:
            wc_rows.append((k, wa, wb))
        # Compare body text without the heading line and without markup prefixes.
        if norm_ws(_body(a_secs[k])) != norm_ws(_body(b_secs[k])):
            changed.append(k)
    tabs_only_a = [k for k in a_tabs if k not in b_tabs]
    tabs_only_b = [k for k in b_tabs if k not in a_tabs]

    L = [f"# Comparison: {old_name} vs {new_name}", ""]
    L.append(f"- Sections: {len(a_secs)} in {old_name}, {len(b_secs)} in {new_name}, {len(common)} in both.")
    L.append(f"- Tables: {len(a_tabs)} in {old_name}, {len(b_tabs)} in {new_name}.")
    L.append(f"- Sections whose text changed beyond whitespace: {len(changed)}.")
    L.append("")
    L.append(f"## Section headings only in {old_name} ({len(only_a)})")
    L.extend(f"- {k}" for k in only_a[:200])
    L.append("")
    L.append(f"## Section headings only in {new_name} ({len(only_b)})")
    L.extend(f"- {k}" for k in only_b[:200])
    L.append("")
    L.append(f"## Tables only in {old_name} ({len(tabs_only_a)})")
    L.extend(f"- {k}" for k in tabs_only_a[:200])
    L.append("")
    L.append(f"## Tables only in {new_name} ({len(tabs_only_b)})")
    L.extend(f"- {k}" for k in tabs_only_b[:200])
    L.append("")
    L.append(f"## Word count difference per section ({len(wc_rows)} sections differ)")
    if wc_rows:
        L.append("")
        L.append(f"| Section | {old_name} | {new_name} | Difference |")
        L.append("| --- | --- | --- | --- |")
        for k, wa, wb in sorted(wc_rows, key=lambda r: -abs(r[2] - r[1]))[:200]:
            L.append(f"| {k} | {wa} | {wb} | {wb - wa:+d} |")
    L.append("")
    L.append(f"## Sections changed beyond whitespace ({len(changed)})")
    L.extend(f"- {k}" for k in changed[:500])
    if len(changed) > 500:
        L.append(f"- ... and {len(changed) - 500} more")
    return "\n".join(L) + "\n"


_PREFIX = re.compile(r"^\[HISTORY [^\]]*\] ")


def _body(section_text: str) -> str:
    lines = section_text.splitlines()[1:] if section_text else []
    return "\n".join(_PREFIX.sub("", l) for l in lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m pdf_cleaner.compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("old", help="first cleaned .txt (for example an older extraction)")
    ap.add_argument("new", help="second cleaned .txt (for example output of this tool)")
    ap.add_argument("--out", help="write the markdown summary here (default: print)")
    ap.add_argument("--heading-regex", default=DEFAULT_HEADING, help="regex for a section heading line")
    args = ap.parse_args(argv)
    a = Path(args.old).read_text(encoding="utf-8")
    b = Path(args.new).read_text(encoding="utf-8")
    md = compare(a, b, args.heading_regex, Path(args.old).name, Path(args.new).name)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

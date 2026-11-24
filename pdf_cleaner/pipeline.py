"""Clean one PDF end to end and write <name>_cleaned.txt and <name>_manifest.json."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from . import checks
from .assemble import Stats, build_paragraphs, build_vocab, classify, extract_row_tables, extract_tocs, write_document
from .layout import Line, PageResult, extract_page
from .llm import TableStructurer
from .profile import Profile
from .tables import Table, fidelity_check


@dataclass
class Result:
    pdf: Path
    name: str
    profile: str
    pages: int
    doc: object
    manifest: dict
    usage: dict


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def output_name(pdf: Path) -> str:
    """Output base name: the PDF stem with a trailing browser download suffix
    like " (1)" removed. Spaces are kept."""
    import re

    return re.sub(r"\s*\(\d+\)$", "", pdf.stem)


def assign_ids(tables: list[Table], used: set[str] | None = None) -> None:
    """Untitled tables get unlabeled-p<page>; when that ID is taken, -2, -3 ..."""
    used = set() if used is None else used
    for t in tables:
        if t.id:
            used.add(t.id)
    for t in tables:
        if not t.id:
            base = f"unlabeled-p{t.page}"
            cand, k = base, 1
            while cand in used:
                k += 1
                cand = f"{base}-{k}"
            t.id = cand
            used.add(cand)


def merge_tables(lines: list[Line], warnings: list[str], repeated_text: list[str]) -> list[Line]:
    """Merge tables that continue across pages.

    A table continues the previous one when it is the next element in reading
    order on the following page, has the same number of columns and kind, and
    has no title of its own (or the same table ID)."""
    out: list[Line] = []
    last_table_line: Line | None = None
    by_id: dict[str, Table] = {}
    for l in lines:
        t = l.table
        if t is None:
            out.append(l)
            last_table_line = None
            continue
        target: Table | None = None
        prev = last_table_line.table if last_table_line is not None else None
        if prev is not None and prev.kind == t.kind and t.page == prev.pages[-1] + 1 and prev.ncols == t.ncols and (not t.has_title or t.id == prev.id):
            target = prev
        elif t.id and not t.id.startswith("unlabeled") and t.id in by_id:
            cand = by_id[t.id]
            if cand.kind == t.kind and t.page - cand.pages[-1] in (0, 1) and cand.ncols == t.ncols:
                target = cand
            else:
                if cand.ncols != t.ncols:
                    t.quality_issues = sorted(set(t.quality_issues) | {"continuation_column_mismatch"})
                    warnings.append(f"p{t.page}: table {t.id} continues with {t.ncols} columns instead of {cand.ncols}; kept as a separate block")
                t.id = f"{t.id}-p{t.page}"
        if target is not None:
            # Repeated titles, captions and header rows on continuation pages are
            # dropped like running headers, and recorded for the completeness check.
            if t.has_title and t.title:
                repeated_text.append(t.title)
            repeated_text.extend(n for n in t.notes if n in target.notes)
            target.notes.extend(n for n in t.notes if n not in target.notes)
            if t.header == target.header:
                if not t.header_copied:
                    repeated_text.extend(t.header)
                target.rows.extend(t.rows)
            elif not any(t.header):
                target.rows.extend(t.rows)
            else:
                target.rows.append(t.header)
                target.rows.extend(t.rows)
            for p in t.pages:
                if p not in target.pages:
                    target.pages.append(p)
            target.region_text += "\n" + t.region_text
            target.quality_issues = sorted(set(target.quality_issues) | set(t.quality_issues))
            target.number_repairs += t.number_repairs
            if t.method == "llm" or target.method == "llm":
                target.method = "llm" if t.method == target.method else "mixed"
            t.merged_into = target.id
            continue
        by_id.setdefault(t.id, t)
        out.append(l)
        last_table_line = l
    return out


def normalize_zone_edges(page_results: list[PageResult]) -> None:
    """Pages with few lines give unreliable column edges. Use the typical edges
    of the same column index from well-filled pages instead."""
    import statistics

    samples: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for pr in page_results:
        by_zone: dict[int, list[Line]] = {}
        for l in pr.lines:
            if l.table is None:
                by_zone.setdefault(l.zone, []).append(l)
        for z, ls in by_zone.items():
            if len(ls) >= 10:
                samples.setdefault((pr.columns, z), []).append((ls[0].zone_left, ls[0].zone_right))
    typical = {
        k: (statistics.median(a for a, _ in v), statistics.median(b for _, b in v)) for k, v in samples.items() if len(v) >= 3
    }
    for pr in page_results:
        by_zone: dict[int, list[Line]] = {}
        for l in pr.lines:
            if l.table is None:
                by_zone.setdefault(l.zone, []).append(l)
        for z, ls in by_zone.items():
            if len(ls) < 10 and (pr.columns, z) in typical:
                left, right = typical[(pr.columns, z)]
                for l in ls:
                    l.zone_left, l.zone_right = min(left, l.zone_left), max(right, l.zone_right)


def process_pdf(pdf: Path, profile: Profile, structurer: TableStructurer, max_pages: int | None = None, page_range: tuple[int, int] | None = None) -> Result:
    warnings: list[str] = []
    page_results: list[PageResult] = []
    prev_text_table: Table | None = None
    with pdfplumber.open(str(pdf)) as doc:
        n = len(doc.pages)
        limit = min(n, max_pages) if max_pages else n
        first = 0
        if page_range:
            first, limit = page_range[0] - 1, min(n, page_range[1])
        for i in range(first, limit):
            page = doc.pages[i]
            pr = extract_page(page, i + 1, profile, prev_text_table)
            page.close()
            # A text table (legislative history) only continues onto the very next page.
            text_tables = [t for t in pr.tables if t.kind != "ruled"]
            prev_text_table = text_tables[-1] if text_tables else None
            page_results.append(pr)
            warnings.extend(pr.warnings)

    normalize_zone_edges(page_results)
    all_lines: list[Line] = [l for pr in page_results for l in pr.lines]
    tables = [t for pr in page_results for t in pr.tables]
    assign_ids(tables)

    # Deterministic fidelity: our own table assembly must match the text layer.
    for t in tables:
        ok, detail = fidelity_check([t.title] + t.notes + t.header + [c for r in t.rows for c in r], t.region_text)
        t.fidelity = "pass" if ok else "fail"
        t.fidelity_detail = detail
        if not ok and t.kind == "ruled":
            t.quality_issues = sorted(set(t.quality_issues) | {"fidelity_fail"})

    structurer.process(pdf, tables)

    repeated_text: list[str] = []
    all_lines = merge_tables(all_lines, warnings, repeated_text)
    all_lines = extract_tocs(all_lines, profile, warnings)
    all_lines = extract_row_tables(all_lines, profile, warnings)
    tables = [l.table for l in all_lines if l.table is not None] + [t for t in tables if t.merged_into]
    assign_ids(tables)
    for t in tables:
        if t.kind in ("toc", "text_rows"):
            ok, detail = fidelity_check([t.title] + [c for r in t.rows for c in r], t.region_text)
            t.fidelity = "pass" if ok else "fail"
            t.fidelity_detail = detail

    stats = Stats()
    vocab = build_vocab([l for l in all_lines if l.table is None])
    paras = build_paragraphs(all_lines, profile, vocab, stats)
    els = classify(paras, profile, warnings, stats)
    document = write_document(els, tables, warnings, stats)

    char_text = {pr.page: pr.char_text for pr in page_results}
    removed = [t for pr in page_results for t in pr.removed_header + pr.removed_footer + pr.dropped_lines] + repeated_text
    completeness = checks.completeness(char_text, removed, document.text)
    if completeness["lost_chars"]:
        warnings.append(
            f"completeness: {completeness['lost_chars']} text-layer characters not found in output "
            f"(sample {completeness['lost_sample']})"
        )
    crosscheck = checks.text_layer_crosscheck(pdf, char_text)
    removed_headers = {t for pr in page_results for t in pr.removed_header}
    suspicious = checks.suspicious_pages(document, page_results, removed_headers)
    spot_sections = checks.spot_check_sections(pdf, document)
    spot_tables = checks.spot_check_tables(pdf, [t for t in tables if t.merged_into is None])

    removed_counts = Counter()
    for pr in page_results:
        for t in pr.removed_header:
            removed_counts["header:" + _template(t)] += 1
        for t in pr.removed_footer:
            removed_counts["footer:" + _template(t)] += 1
        for t in pr.dropped_lines:
            removed_counts["placeholder:" + t] += 1

    has_history_rules = bool(profile.section("history"))
    sections_out = []
    for s in document.sections:
        sections_out.append(
            {
                "id": s["id"],
                "heading": s["heading"],
                "level": s["level"],
                "offset": s["offset"],
                "page": s["page"],
                "chapter": s.get("chapter"),
                "history_notes": s["history_notes"],
                "covered_by_chapter_history": s.get("covered_by_chapter_history", False),
            }
        )
    manifest = {
        "source_pdf": pdf.name,
        "pdf_sha256": sha256_file(pdf),
        "profile": profile.name,
        "pages": len(page_results),
        "pages_without_text_layer": [pr.page for pr in page_results if not pr.has_text_layer],
        "multi_column_pages": sum(1 for pr in page_results if pr.columns > 1),
        "page_map": document.page_map,
        "headings": document.headings,
        "sections": sections_out,
        "history_rules_in_profile": has_history_rules,
        "history_notes": document.history_notes,
        "chapter_history": document.chapters,
        "tables": [
            {
                "id": t.id,
                "kind": t.kind,
                "title": t.title,
                "column_headers": t.header,
                "rows": len(t.rows),
                "pages": t.pages,
                "offset": document.table_offsets.get(t.id),
                "method": t.method,
                "fidelity": t.fidelity,
                "fidelity_detail": t.fidelity_detail,
                "llm_status": t.llm_status,
                "quality_issues": t.quality_issues,
                "number_repairs": t.number_repairs,
                "merged_into": t.merged_into,
            }
            for t in tables
        ],
        "stats": {
            "hyphen_joined": stats.hyphen_joined,
            "hyphen_kept": stats.hyphen_kept,
            "hyphen_uncertain": stats.hyphen_uncertain,
            "dot_leaders_collapsed": stats.dot_leaders_collapsed,
            "output_chars": len(document.text),
        },
        "removed_running_text": dict(removed_counts.most_common()),
        "repeated_table_text_dropped": len(repeated_text),
        "checks": {
            "completeness": completeness,
            "text_layer_crosscheck": crosscheck,
            "spot_check_sections": spot_sections,
            "spot_check_tables": spot_tables,
            "suspicious_pages": suspicious,
        },
        "warnings": warnings,
    }
    return Result(pdf, output_name(pdf), profile.name, len(page_results), document, manifest, structurer.usage.to_dict())


def _template(text: str) -> str:
    import re

    return re.sub(r"\d+", "#", text)


def write_outputs(res: Result, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    txt = out_dir / f"{res.name}_cleaned.txt"
    man = out_dir / f"{res.name}_manifest.json"
    txt.write_text(res.doc.text, encoding="utf-8")
    man.write_text(json.dumps(res.manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return txt, man

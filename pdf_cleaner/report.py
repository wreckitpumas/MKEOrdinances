"""Write output/extraction_report.md covering every PDF in the run."""

from __future__ import annotations

from pathlib import Path


def _sections_without_history(m: dict) -> tuple[list[str], int]:
    if not m.get("history_rules_in_profile"):
        return [], 0
    secs = [s for s in m["sections"] if s["level"] == 3]
    missing = [s for s in secs if s["history_notes"] == 0 and not s["covered_by_chapter_history"]]
    covered = sum(1 for s in secs if s["history_notes"] == 0 and s["covered_by_chapter_history"])
    return [s["id"] for s in missing], covered


def write_report(results: list, out_dir: Path, *, llm_mode: str, model: str, dry_run_plan: list[dict] | None = None) -> Path:
    L: list[str] = []
    a = L.append
    a("# Extraction report")
    a("")
    a(f"Table model step: {llm_mode}. Model setting: `{model}`.")
    a("")
    a("## Per-source counts")
    a("")
    a("| Source | Pages | Sections | Tables | History notes (section / chapter) | Warnings | Lost chars |")
    a("| --- | --- | --- | --- | --- | --- | --- |")
    for r in results:
        m = r.manifest
        secs = [s for s in m["sections"] if s["level"] == 3] or m["sections"]
        tables = [t for t in m["tables"] if not t["merged_into"]]
        sec_notes = sum(1 for h in m["history_notes"] if h.get("section") is not None or ("chapter" not in h))
        ch_notes = sum(1 for h in m["history_notes"] if "chapter" in h)
        lost = m["checks"]["completeness"]["lost_chars"]
        a(f"| {r.name} | {m['pages']} | {len(secs)} | {len(tables)} | {sec_notes} / {ch_notes} | {len(m['warnings'])} | {lost} |")
    a("")
    a("Lost chars: alphanumeric characters in the PDF text layer (after removing running headers, footers and blank-page notes) that do not appear in the output. 0 means nothing was dropped.")
    a("")

    a("## History notes")
    a("")
    for r in results:
        m = r.manifest
        if not m.get("history_rules_in_profile"):
            a(f"- **{r.name}**: the survey found no history notes in this document, so none are expected.")
            continue
        missing, covered = _sections_without_history(m)
        secs = [s for s in m["sections"] if s["level"] == 3]
        with_notes = sum(1 for s in secs if s["history_notes"])
        a(
            f"- **{r.name}**: {with_notes} sections have their own history note; "
            f"{covered} rely on chapter-level history (legislative history table or Municipal Research Library pointer); "
            f"{len(missing)} have neither."
        )
        if missing:
            shown = ", ".join(missing[:40])
            more = f" (and {len(missing) - 40} more; full list in the manifest)" if len(missing) > 40 else ""
            a(f"  - Sections with no history note: {shown}{more}")
    a("")

    a("## Tables")
    a("")
    for r in results:
        m = r.manifest
        tables = [t for t in m["tables"] if not t["merged_into"]]
        if not tables:
            continue
        kinds = {}
        for t in tables:
            kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
        multi = sum(1 for t in tables if len(t["pages"]) > 1)
        flagged = [t for t in tables if t["quality_issues"]]
        failed = [t for t in tables if t["fidelity"] == "fail"]
        rejected = [t for t in tables if (t.get("llm_status") or "").startswith("rejected")]
        kind_s = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
        a(f"- **{r.name}**: {len(tables)} tables ({kind_s}); {multi} span pages; {len(flagged)} flagged by the quality check.")
        for t in failed:
            a(f"  - Fidelity FAIL: `{t['id']}` p{t['pages']}: {t['fidelity_detail']}")
        for t in rejected:
            a(f"  - Model table rejected: `{t['id']}` p{t['pages']}: {t['llm_status']}")
    a("")

    a("## Suspicious pages")
    a("")
    any_sus = False
    for r in results:
        sus = r.manifest["checks"]["suspicious_pages"]
        if sus:
            any_sus = True
            a(f"- **{r.name}** ({len(sus)}):")
            for s in sus[:8]:
                a(f"  - p{s['page']}: {s['issue']}")
            if len(sus) > 8:
                a(f"  - ... {len(sus) - 8} more in the manifest")
    if not any_sus:
        a("None found.")
    a("")

    a("## Model (API) usage")
    a("")
    tot_in = sum(r.usage.get("input_tokens", 0) for r in results)
    tot_out = sum(r.usage.get("output_tokens", 0) for r in results)
    sent = sum(r.usage.get("tables_sent", 0) for r in results)
    pages = sum(r.usage.get("pages_sent", 0) for r in results)
    est_in = sum(r.usage.get("est_input_tokens", 0) for r in results)
    est_out = sum(r.usage.get("est_output_tokens", 0) for r in results)
    est_cost = sum(r.usage.get("est_cost_usd", 0.0) for r in results)
    planned = sum(len(r.usage.get("planned", [])) for r in results)
    a(f"- Pages sent: {pages}. Tables sent: {sent}. Tokens used: {tot_in} input, {tot_out} output.")
    a(f"- Tables eligible for the model step (failed the deterministic quality check): {planned}.")
    a(f"- Estimated cost of a full model run for those tables: {est_in} input and {est_out} output tokens, about ${est_cost:.2f} with `{model}`.")
    a("")

    a("## Checks")
    a("")
    a("| Source | Text layer chars (pdfplumber / pdftotext) | Removed running text | Output chars | Lost | Extra |")
    a("| --- | --- | --- | --- | --- | --- |")
    for r in results:
        c = r.manifest["checks"]["completeness"]
        x = r.manifest["checks"]["text_layer_crosscheck"]
        layer = "skipped (pdftotext not installed)" if x.get("skipped") else f"{x['pdfplumber_alnum_chars']} / {x['pdftotext_alnum_chars']}"
        a(
            f"| {r.name} | {layer} | {c['removed_running_text_chars']} | "
            f"{c['output_chars']} | {c['lost_chars']} | {c['extra_chars']} |"
        )
    a("")
    a("### Spot checks (5 random sections and 3 random tables per PDF, compared with pdftotext page text)")
    a("")
    for r in results:
        ss = r.manifest["checks"]["spot_check_sections"]
        ts = r.manifest["checks"]["spot_check_tables"]
        a(f"**{r.name}**")
        a("")
        for s in ss:
            note = "" if s["result"] == "pass" else f" Not found: {s['examples_not_found']}"
            a(f"- s. {s['section']} (p{s['pages']}): {s['words']} words, {s['words_not_found']} not found in page text: {s['result']}.{note}")
        for t in ts:
            a(f"- table `{t['table']}` (p{t['pages']}, {t['rows']} rows): {t['result']}. {t['detail']}")
        if not ss and not ts:
            a("- skipped (pdftotext not installed)" if r.manifest["checks"]["text_layer_crosscheck"].get("skipped") else "- nothing to sample")
        a("")
    path = out_dir / "extraction_report.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path

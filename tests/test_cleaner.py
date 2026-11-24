"""End-to-end tests on synthetic PDFs. All content is placeholder text."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf_cleaner.cli import main as cli_main
from pdf_cleaner.compare import compare
from pdf_cleaner.llm import TableStructurer
from pdf_cleaner.pipeline import process_pdf
from pdf_cleaner.profile import load_profile
from pdf_cleaner.tables import Table, fidelity_check
from pdfgen import Doc

PROFILE_PATH = Path(__file__).parent / "synthetic_profile.yaml"
BODY = [
    "Placeholder text for testing only and it has no legal meaning at all in any",
    "place. The placeholder requirement applies to every placeholder item that is",
    "listed in s. 999-999-9 and to every other placeholder item named in this",
    "section of the placeholder code.",
]


def run(pdf: Path, tmp_path: Path, llm: TableStructurer | None = None):
    prof = load_profile(PROFILE_PATH)
    structurer = llm or TableStructurer("claude-haiku-5-5", tmp_path / "cache", enabled=False, dry_run=False, batch=False)
    return process_pdf(pdf, prof, structurer)


def build_two_section_pdf(path: Path):
    d = Doc(path)
    d.line("CHAPTER 999", bold=True)
    d.gap()
    d.heading_line("999-1. Placeholder Alpha.")
    d.paragraph(BODY)
    d.line("(HISTORY: Section 999-1 cr. File #000000, Jan. 1, 2000;", indent=15)
    d.line("999-1 am. File #000001, Feb. 2, 2001.)")
    d.gap()
    d.heading_line("999-2. Placeholder Beta.", "Placeholder text for the second section.")
    d.paragraph(BODY)
    d.line("(HISTORY: Section 999-2 cr. File #000002, Mar. 3, 2002.)", indent=15)
    d.new_page()
    d.heading_line("999-3. Placeholder Gamma.")
    d.paragraph(BODY)
    d.save()


def test_header_and_footer_removed(tmp_path):
    pdf = tmp_path / "doc.pdf"
    build_two_section_pdf(pdf)
    res = run(pdf, tmp_path)
    text = res.doc.text
    assert "PLACEHOLDER CODE" not in text
    assert not re.search(r"^- \d+ -$", text, re.M)
    assert res.manifest["checks"]["completeness"]["lost_chars"] == 0
    assert any(k.startswith("header:") for k in res.manifest["removed_running_text"])
    assert any(k.startswith("footer:") for k in res.manifest["removed_running_text"])


def test_hyphen_repair(tmp_path):
    pdf = tmp_path / "hyphen.pdf"
    d = Doc(pdf)
    d.heading_line("999-1. Placeholder Hyphens.")
    # "requirement" appears unbroken elsewhere, so "require-" + "ment" is joined.
    # "non-conforming" appears hyphenated elsewhere, so its hyphen is kept.
    d.paragraph(
        [
            "Each placeholder requirement and each non-conforming placeholder use is listed",
            "here. The placeholder text continues with a line that ends in a require-",
            "ment and then another placeholder line that ends with a word like non-",
            "conforming before this placeholder paragraph ends.",
        ]
    )
    d.save()
    text = run(pdf, tmp_path).doc.text
    assert "ends in a requirement and then" in text
    assert "word like non-conforming before" in text
    assert "require-\n" not in text and "require- ment" not in text


def test_table_spanning_two_pages(tmp_path):
    pdf = tmp_path / "table.pdf"
    d = Doc(pdf)
    d.heading_line("999-1. Placeholder Rates.")
    d.paragraph(BODY)
    d.y = 560
    d.table(
        [["Item", "Rate", "Unit"], ["Placeholder A", "1.00", "each"], ["Placeholder B", "2.00", "each"], ["Placeholder C", "3.00", "each"]],
        [200, 100, 100],
        title="Table 999-1 PLACEHOLDER RATES",
    )
    d.new_page()
    d.table([["Placeholder D", "4.00", "each"], ["Placeholder E", "5.00", "each"]], [200, 100, 100])
    d.heading_line("999-2. Placeholder After.", "Placeholder text after the table.")
    d.save()
    res = run(pdf, tmp_path)
    text = res.doc.text
    blocks = re.findall(r"^\[TABLE ([^:]+): (.*)\]\n(.*?)^\[/TABLE\]", text, re.M | re.S)
    assert len(blocks) == 1, text
    tid, title, body = blocks[0]
    assert tid == "999-1"
    assert title == "Table 999-1 PLACEHOLDER RATES"
    assert "| Item | Rate | Unit |" in body
    for name in "ABCDE":
        assert f"| Placeholder {name} |" in body
    t = next(t for t in res.manifest["tables"] if t["id"] == "999-1")
    assert t["pages"] == [1, 2]
    assert t["column_headers"] == ["Item", "Rate", "Unit"]
    assert t["fidelity"] == "pass"
    # The table sits between the section text and the next heading.
    assert text.index("[TABLE 999-1") < text.index("### 999-2.")
    assert res.manifest["checks"]["completeness"]["lost_chars"] == 0


def test_fidelity_check_rejects_invented_token():
    source = "Item Rate Unit Placeholder A 1.00 each"
    ok, _ = fidelity_check(["Item", "Rate", "Unit", "Placeholder A", "1.00", "each"], source)
    assert ok
    ok, detail = fidelity_check(["Item", "Rate", "Unit", "Placeholder A", "1.50", "each"], source)
    assert not ok and "1.50" in detail
    # Dropping a token also fails.
    ok, detail = fidelity_check(["Item", "Rate", "Unit", "Placeholder A", "each"], source)
    assert not ok and "missing" in detail


class FakeClient:
    """Stands in for anthropic.Anthropic(); returns a fixed markdown table."""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params):
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.reply)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


def _flagged_table(page_bbox):
    t = Table(
        id="999-9",
        title="",
        header=["Item", "Rate"],
        rows=[["Placeholder A", "1.00"]],
        page=1,
        bbox=page_bbox,
    )
    t.region_text = "Item Rate\nPlaceholder A 1.00"
    t.quality_issues = ["garbled_spacing"]
    return t


def test_model_table_with_invented_token_falls_back(tmp_path):
    pdf = tmp_path / "doc.pdf"
    build_two_section_pdf(pdf)
    invented = "| Item | Rate |\n| --- | --- |\n| Placeholder A | 1.00 |\n| Placeholder Z | 9.99 |\n"
    client = FakeClient(invented)
    s = TableStructurer("claude-haiku-5-5", tmp_path / "cache", enabled=True, dry_run=False, batch=False, client=client)
    t = _flagged_table((72, 80, 400, 120))
    s.process(pdf, [t])
    assert client.calls == 1
    assert t.method == "deterministic"
    assert t.rows == [["Placeholder A", "1.00"]]
    assert t.llm_status.startswith("rejected by fidelity check")
    assert s.usage.failures

    # A faithful arrangement is accepted, and a rerun is served from the cache.
    faithful = "| Item | Rate |\n| --- | --- |\n| Placeholder A | 1.00 |\n"
    client2 = FakeClient(faithful)
    s2 = TableStructurer("claude-haiku-5-5", tmp_path / "cache2", enabled=True, dry_run=False, batch=False, client=client2)
    t2 = _flagged_table((72, 80, 400, 120))
    s2.process(pdf, [t2])
    assert t2.method == "llm" and t2.llm_status == "accepted"
    s3 = TableStructurer("claude-haiku-5-5", tmp_path / "cache2", enabled=True, dry_run=False, batch=False, client=FakeClient(faithful))
    t3 = _flagged_table((72, 80, 400, 120))
    s3.process(pdf, [t3])
    assert s3.client.calls == 0 and s3.usage.cache_hits == 1 and t3.method == "llm"


def test_history_notes_attach_to_their_section(tmp_path):
    pdf = tmp_path / "history.pdf"
    build_two_section_pdf(pdf)
    res = run(pdf, tmp_path)
    lines = res.doc.text.splitlines()
    i1 = lines.index("### 999-1. Placeholder Alpha.")
    i2 = lines.index("### 999-2. Placeholder Beta.")
    i3 = lines.index("### 999-3. Placeholder Gamma.")
    sec1 = lines[i1:i2]
    sec2 = lines[i2:i3]
    assert sec1[-3:-1] == [
        "[HISTORY s. 999-1] (HISTORY: Section 999-1 cr. File #000000, Jan. 1, 2000;",
        "[HISTORY s. 999-1] 999-1 am. File #000001, Feb. 2, 2001.)",
    ] or sec1[-2:] == [
        "[HISTORY s. 999-1] (HISTORY: Section 999-1 cr. File #000000, Jan. 1, 2000;",
        "[HISTORY s. 999-1] 999-1 am. File #000001, Feb. 2, 2001.)",
    ]
    assert any(l.startswith("[HISTORY s. 999-2] (HISTORY: Section 999-2 cr.") for l in sec2)
    assert not any("HISTORY" in l for l in lines[i3:])
    by_id = {s["id"]: s for s in res.manifest["sections"]}
    assert by_id["999-1"]["history_notes"] == 1
    assert by_id["999-2"]["history_notes"] == 1
    assert by_id["999-3"]["history_notes"] == 0
    assert "## " not in "\n".join(lines[:i1]) or "# CHAPTER 999" in lines


def test_paths_with_spaces(tmp_path):
    src = tmp_path / "my pdf files"
    src.mkdir()
    pdf = src / "placeholder code volume one.pdf"
    build_two_section_pdf(pdf)
    out = tmp_path / "output dir"
    rc = cli_main([str(src), "--out", str(out), "--no-llm", "--profile", str(PROFILE_PATH)])
    assert rc == 0
    txt = out / "placeholder code volume one_cleaned.txt"
    man = out / "placeholder code volume one_manifest.json"
    assert txt.exists() and man.exists()
    assert (out / "extraction_report.md").exists()
    m = json.loads(man.read_text(encoding="utf-8"))
    assert m["source_pdf"] == "placeholder code volume one.pdf"
    # The page map covers the whole output.
    assert m["page_map"][0]["start"] == 0 and m["page_map"][-1]["end"] == len(txt.read_text(encoding="utf-8"))


def test_dry_run_makes_no_calls(tmp_path, capsys):
    pdf = tmp_path / "doc one.pdf"
    build_two_section_pdf(pdf)
    rc = cli_main([str(pdf), "--out", str(tmp_path / "o"), "--dry-run", "--profile", str(PROFILE_PATH)])
    assert rc == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert not (tmp_path / "o").exists()


def test_compare_reports_differences():
    a = "### 999-1. Placeholder A.\nOne two three.\n\n### 999-2. Placeholder B.\nFour five.\n[TABLE t-1: Placeholder]\n| x |\n| --- |\n[/TABLE]\n"
    b = "### 999-1. Placeholder A.\nOne two   three.\n\n### 999-3. Placeholder C.\nSix.\n"
    md = compare(a, b, old_name="old", new_name="new")
    assert "Section headings only in old (1)" in md and "- 999-2" in md
    assert "Section headings only in new (1)" in md and "- 999-3" in md
    assert "Tables only in old (1)" in md and "- t-1" in md
    assert "Sections whose text changed beyond whitespace: 0" in md


def test_web_endpoint_returns_txt(tmp_path):
    import threading
    import urllib.parse
    import urllib.request

    from pdf_cleaner.web import make_server

    pdf = tmp_path / "web upload one.pdf"
    build_two_section_pdf(pdf)
    srv = make_server("127.0.0.1", 0, tmp_path / "cache", "claude-haiku-5-5")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        q = urllib.parse.urlencode({"filename": pdf.name, "profile": str(PROFILE_PATH), "llm": "0"})
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/clean?{q}", data=pdf.read_bytes(), method="POST")
        data = json.loads(urllib.request.urlopen(req).read())
        assert data["name"] == "web upload one"
        assert "### 999-1. Placeholder Alpha." in data["text"]
        assert data["summary"]["lost_chars"] == 0
        # A non-PDF upload is refused.
        bad = urllib.request.Request(f"http://127.0.0.1:{port}/api/clean?{q}", data=b"not a pdf", method="POST")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad)
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert "<title>PDF Cleaner</title>" in page
    finally:
        srv.shutdown()

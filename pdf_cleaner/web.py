"""A small local browser front end: load a PDF, get the cleaned .txt back.

Usage:
    python3 -m pdf_cleaner.web            # opens http://127.0.0.1:8765
    python3 -m pdf_cleaner.web --port 9000 --no-browser

It runs the same pipeline as the command line, on this machine only (it
listens on 127.0.0.1). Uploaded PDFs go to a temporary folder that is deleted
after each request. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .llm import DEFAULT_MODEL, TableStructurer, load_dotenv
from .pipeline import output_name, process_pdf
from .profile import all_profiles, resolve_profile

MAX_UPLOAD = 200 * 1024 * 1024
_LOCK = threading.Lock()  # one PDF at a time keeps memory use predictable


def summarize(res) -> dict:
    m = res.manifest
    secs = [s for s in m["sections"] if s["level"] == 3] or m["sections"]
    tables = [t for t in m["tables"] if not t["merged_into"]]
    return {
        "pages": m["pages"],
        "sections": len(secs),
        "tables": len(tables),
        "tables_fidelity_fail": sum(1 for t in tables if t["fidelity"] == "fail"),
        "tables_via_model": sum(1 for t in tables if t["method"] == "llm"),
        "history_notes": len(m["history_notes"]),
        "lost_chars": m["checks"]["completeness"]["lost_chars"],
        "warnings": len(m["warnings"]),
        "profile": m["profile"],
        "api_tables_sent": res.usage.get("tables_sent", 0),
    }


def clean_bytes(data: bytes, filename: str, profile: str | None, use_llm: bool, cache_dir: Path, model: str) -> dict:
    """Run the pipeline on uploaded PDF bytes and return text, manifest and summary."""
    safe = re.sub(r"[^\w .()-]", "_", Path(filename).name) or "upload.pdf"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    with tempfile.TemporaryDirectory(prefix="pdf_cleaner_") as tmp:
        pdf = Path(tmp) / safe
        pdf.write_bytes(data)
        prof = resolve_profile(pdf, profile or None)
        structurer = TableStructurer(model, cache_dir, enabled=use_llm, dry_run=False, batch=False)
        res = process_pdf(pdf, prof, structurer)
    return {
        "name": output_name(Path(safe)),
        "text": res.doc.text,
        "manifest": res.manifest,
        "summary": summarize(res),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "pdf_cleaner"

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("[web] " + (fmt % args) + "\n")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/info":
            self._json(
                200,
                {
                    "profiles": [p.name for p in all_profiles()],
                    "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                    "model": self.server.model,
                },
            )
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/api/clean":
            self._json(404, {"error": "not found"})
            return
        q = parse_qs(url.query)
        filename = q.get("filename", ["upload.pdf"])[0]
        profile = q.get("profile", ["auto"])[0]
        use_llm = q.get("llm", ["0"])[0] == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD:
            self._json(400, {"error": "Upload is empty or larger than 200 MB."})
            return
        data = self.rfile.read(length)
        if not data.startswith(b"%PDF"):
            self._json(400, {"error": "That file is not a PDF."})
            return
        started = time.time()
        try:
            with _LOCK:
                out = clean_bytes(data, filename, None if profile == "auto" else profile, use_llm, self.server.cache_dir, self.server.model)
        except SystemExit as e:  # profile resolution errors
            self._json(400, {"error": str(e)})
            return
        except Exception as e:  # pragma: no cover - surfaced to the page
            self._json(500, {"error": f"{type(e).__name__}: {e}"})
            return
        out["seconds"] = round(time.time() - started, 1)
        self._json(200, out)


def make_server(host: str, port: int, cache_dir: Path, model: str) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.cache_dir = cache_dir
    srv.model = model
    return srv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m pdf_cleaner.web", description="Local browser front end for pdf_cleaner.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    ap.add_argument("--model", default=os.environ.get("PDF_CLEANER_MODEL", DEFAULT_MODEL))
    ap.add_argument("--cache-dir", default=os.environ.get("PDF_CLEANER_CACHE", ".api_cache"))
    args = ap.parse_args(argv)
    load_dotenv(Path(".env"))
    srv = make_server("127.0.0.1", args.port, Path(args.cache_dir), args.model)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"pdf_cleaner web: {url}  (Ctrl+C to stop)", file=sys.stderr)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDF Cleaner</title>
<style>
:root {
  --bg: #f6f7f9; --card: #ffffff; --ink: #1d2330; --muted: #5d6677; --line: #dfe3ea;
  --accent: #2f5fd0; --accent-ink: #ffffff; --ok: #1f7a4a; --bad: #b3261e; --drop: #eef3ff;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #15181e; --card: #1d2129; --ink: #e8ebf1; --muted: #9aa3b2; --line: #313744;
          --accent: #7aa2ff; --accent-ink: #0d1320; --ok: #5fd39a; --bad: #ff8a80; --drop: #222a3a; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
       font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 32px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 24px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 20px; margin-bottom: 16px; }
#drop { border: 2px dashed var(--line); border-radius: 10px; padding: 32px 16px; text-align: center;
        cursor: pointer; transition: background .15s, border-color .15s; }
#drop.over { background: var(--drop); border-color: var(--accent); }
#drop strong { display: block; font-size: 16px; }
#drop span { color: var(--muted); font-size: 14px; }
#file { display: none; }
.row { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; margin-top: 16px; }
label { color: var(--muted); font-size: 14px; }
select { font: inherit; padding: 6px 8px; border-radius: 8px; border: 1px solid var(--line); background: var(--card); color: var(--ink); }
button { font: inherit; font-weight: 600; padding: 9px 16px; border-radius: 8px; border: 1px solid var(--accent);
         background: var(--accent); color: var(--accent-ink); cursor: pointer; }
button.secondary { background: transparent; color: var(--accent); }
button:disabled { opacity: .45; cursor: not-allowed; }
#status { margin-top: 14px; color: var(--muted); min-height: 1.5em; }
#status.err { color: var(--bad); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin: 4px 0 16px; }
.stat { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }
.stat b { display: block; font-size: 20px; }
.stat small { color: var(--muted); }
.good b { color: var(--ok); } .warn b { color: var(--bad); }
pre { margin: 12px 0 0; max-height: 420px; overflow: auto; white-space: pre-wrap; word-break: break-word;
      background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 12px; font: 12.5px/1.5 ui-monospace, Menlo, Consolas, monospace; }
#result { display: none; }
.note { color: var(--muted); font-size: 13px; margin-top: 8px; }
</style>
</head>
<body>
<main>
  <h1>PDF Cleaner</h1>
  <p class="sub">Load a Milwaukee Charter, Code, Positions or Salary PDF and get a cleaned .txt file back. Runs on this computer.</p>

  <div class="card">
    <div id="drop" tabindex="0" role="button" aria-label="Choose a PDF">
      <strong id="dropTitle">Drop a PDF here</strong>
      <span>or click to choose a file</span>
    </div>
    <input id="file" type="file" accept="application/pdf,.pdf">
    <div class="row">
      <label for="profile">Document type</label>
      <select id="profile"><option value="auto">Detect from file name</option></select>
      <label><input type="checkbox" id="llm"> Use Claude for flagged tables</label>
      <button id="go" disabled>Clean PDF</button>
    </div>
    <div id="status"></div>
  </div>

  <div class="card" id="result">
    <div class="stats" id="stats"></div>
    <div class="row" style="margin-top:0">
      <button id="dlTxt">Download .txt</button>
      <button id="dlJson" class="secondary">Download manifest .json</button>
    </div>
    <p class="note" id="lostNote"></p>
    <pre id="preview"></pre>
  </div>
</main>
<script>
const $ = (id) => document.getElementById(id);
let chosen = null, result = null, timer = null;

fetch("/api/info").then(r => r.json()).then(info => {
  for (const p of info.profiles) {
    const o = document.createElement("option"); o.value = p; o.textContent = p; $("profile").appendChild(o);
  }
  if (!info.api_key) {
    $("llm").disabled = true;
    $("llm").parentElement.title = "Set ANTHROPIC_API_KEY to enable";
    $("llm").parentElement.style.opacity = .5;
  }
});

function choose(f) {
  if (!f) return;
  if (!/\.pdf$/i.test(f.name)) { setStatus("Please choose a .pdf file.", true); return; }
  chosen = f;
  $("dropTitle").textContent = f.name;
  $("go").disabled = false;
  setStatus(`${(f.size / 1048576).toFixed(1)} MB ready.`);
}
function setStatus(msg, err) { $("status").textContent = msg; $("status").className = err ? "err" : ""; }

$("drop").onclick = () => $("file").click();
$("drop").onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") $("file").click(); };
$("file").onchange = (e) => choose(e.target.files[0]);
["dragenter", "dragover"].forEach(ev => $("drop").addEventListener(ev, e => { e.preventDefault(); $("drop").classList.add("over"); }));
["dragleave", "drop"].forEach(ev => $("drop").addEventListener(ev, e => { e.preventDefault(); $("drop").classList.remove("over"); }));
$("drop").addEventListener("drop", e => choose(e.dataTransfer.files[0]));

$("go").onclick = async () => {
  if (!chosen) return;
  $("go").disabled = true; $("result").style.display = "none";
  const t0 = Date.now();
  timer = setInterval(() => setStatus(`Cleaning ${chosen.name} ... ${Math.round((Date.now() - t0) / 1000)} s (large volumes take a few minutes)`), 500);
  try {
    const q = new URLSearchParams({ filename: chosen.name, profile: $("profile").value, llm: $("llm").checked ? "1" : "0" });
    const r = await fetch("/api/clean?" + q, { method: "POST", headers: { "Content-Type": "application/pdf" }, body: chosen });
    const data = await r.json();
    clearInterval(timer);
    if (!r.ok) { setStatus(data.error || "Something went wrong.", true); return; }
    result = data; show(data);
    setStatus(`Done in ${data.seconds} s.`);
  } catch (e) {
    clearInterval(timer); setStatus("Could not reach the cleaner: " + e, true);
  } finally { $("go").disabled = false; }
};

function show(d) {
  const s = d.summary;
  const items = [
    ["Pages", s.pages], ["Sections", s.sections], ["Tables", s.tables], ["History notes", s.history_notes],
    ["Lost characters", s.lost_chars, s.lost_chars === 0 ? "good" : "warn"], ["Warnings", s.warnings],
  ];
  $("stats").innerHTML = items.map(([k, v, c]) => `<div class="stat ${c || ""}"><b>${v}</b><small>${k}</small></div>`).join("");
  $("lostNote").textContent = (s.lost_chars === 0
    ? "Every letter and digit in the PDF text layer is in the output (running headers and footers removed on purpose)."
    : `${s.lost_chars} text-layer characters were not found in the output. See warnings in the manifest.`)
    + ` Profile: ${s.profile}.` + (s.tables_fidelity_fail ? ` ${s.tables_fidelity_fail} table(s) failed the fidelity check.` : "");
  const lines = d.text.split("\n");
  $("preview").textContent = lines.slice(0, 300).join("\n") + (lines.length > 300 ? `\n\n... ${lines.length - 300} more lines in the download` : "");
  $("result").style.display = "block";
}
function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
$("dlTxt").onclick = () => result && download(result.name + "_cleaned.txt", result.text, "text/plain;charset=utf-8");
$("dlJson").onclick = () => result && download(result.name + "_manifest.json", JSON.stringify(result.manifest, null, 1), "application/json");
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())

"""Optional model-assisted table structuring.

Only tables whose deterministic extraction fails a quality check are sent.
The model only arranges cells: every token it returns must appear in the text
layer of that table region (fidelity check), and every text-layer token must
appear in its output. A table that fails is not used; the deterministic table
is kept and the failure is recorded.

The anthropic SDK is imported only when a real API call is made, so --no-llm
runs, --dry-run and the tests work without it.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .tables import Table, fidelity_check, parse_markdown_table

PROMPT_VERSION = "table-v1"
DEFAULT_MODEL = "claude-haiku-5-5"
# USD per million tokens (input, output), from the Claude models overview page.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
MAX_EDGE_PX = 1568
RENDER_DPI = 150

PROMPT = """You are given an image of one table from an official legal document, and the exact text layer of that table region.

Rewrite the table as a single markdown pipe table.

Rules:
- Use only words, numbers and symbols that appear in the text layer below, exactly as written there.
- Never add, correct, complete, reword, translate or summarize anything.
- Do not invent column names. If the table has no header row, use an empty header row.
- Arrange the cells into the rows and columns shown in the image. Leave a cell empty if it is empty in the image.
- Every piece of text in the text layer must appear in your table exactly once.
- Output only the markdown table. No commentary.

Text layer of the table region:
<text_layer>
{text}
</text_layer>"""


@dataclass
class Usage:
    pages_sent: int = 0
    tables_sent: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    est_input_tokens: int = 0
    est_output_tokens: int = 0
    cost_usd: float = 0.0
    est_cost_usd: float = 0.0
    planned: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def candidates(tables: list[Table]) -> list[Table]:
    return [t for t in tables if t.kind == "ruled" and t.quality_issues and t.bbox]


def cache_key(model: str, t: Table) -> str:
    """Keyed on the content of the table region, not the whole PDF, so a new
    edition only re-sends tables whose text changed."""
    bbox = [round(v) for v in t.bbox] if t.bbox else None
    blob = json.dumps({"m": model, "v": PROMPT_VERSION, "text": t.region_text, "bbox": bbox}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def render_png(pdf_path: Path, t: Table) -> tuple[bytes, int, int]:
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as doc:
        page = doc.pages[t.page - 1]
        x0, top, x1, bottom = t.bbox
        crop = page.crop((max(0, x0 - 6), max(0, top - 6), min(page.width, x1 + 6), min(page.height, bottom + 6)))
        img = crop.to_image(resolution=RENDER_DPI).original
        w, h = img.size
        scale = min(1.0, MAX_EDGE_PX / max(w, h))
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), img.size[0], img.size[1]


def estimate_tokens(t: Table) -> tuple[int, int]:
    """Rough token estimate without calling the API: image tokens are about
    width x height / 750 at the size sent; text is about 3.5 characters a token."""
    x0, top, x1, bottom = t.bbox
    w = (x1 - x0 + 12) * RENDER_DPI / 72
    h = (bottom - top + 12) * RENDER_DPI / 72
    scale = min(1.0, MAX_EDGE_PX / max(w, h))
    image_tokens = math.ceil((w * scale) * (h * scale) / 750)
    text_tokens = math.ceil((len(PROMPT) + len(t.region_text)) / 3.5)
    out_tokens = math.ceil(len(t.region_text) / 3.0 * 1.4) + 50
    return image_tokens + text_tokens, out_tokens


def price(model: str, inp: int, out: int, batch: bool) -> float:
    pi, po = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    cost = inp / 1e6 * pi + out / 1e6 * po
    return cost / 2 if batch else cost


class TableStructurer:
    def __init__(self, model: str, cache_dir: Path, *, enabled: bool, dry_run: bool, batch: bool, client=None):
        self.model = model
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled
        self.dry_run = dry_run
        self.batch = batch
        self.client = client
        self.usage = Usage()

    # -- cache ---------------------------------------------------------------
    def _cache_get(self, key: str) -> dict | None:
        p = self.cache_dir / f"{key}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def _cache_put(self, key: str, value: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / f"{key}.json").write_text(json.dumps(value), encoding="utf-8")

    # -- API -----------------------------------------------------------------
    def _client(self):
        if self.client is None:
            import anthropic  # optional dependency, imported only here

            self.client = anthropic.Anthropic()
        return self.client

    def _params(self, png: bytes, t: Table) -> dict:
        return {
            "model": self.model,
            "max_tokens": 16000,
            "output_config": {"effort": "low"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(png).decode()},
                        },
                        {"type": "text", "text": PROMPT.format(text=t.region_text)},
                    ],
                }
            ],
        }

    @staticmethod
    def _message_to_record(msg) -> dict:
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        usage = getattr(msg, "usage", None)
        return {
            "stop_reason": getattr(msg, "stop_reason", None),
            "text": text,
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        }

    def _call_direct(self, jobs: list[tuple[str, Table, dict]]) -> dict[str, dict]:
        client = self._client()
        out = {}
        for key, _t, params in jobs:
            msg = client.messages.create(**params)
            out[key] = self._message_to_record(msg)
        return out

    def _call_batch(self, jobs: list[tuple[str, Table, dict]]) -> dict[str, dict]:
        client = self._client()
        reqs = [{"custom_id": key[:64], "params": params} for key, _t, params in jobs]
        batch = client.messages.batches.create(requests=reqs)
        while True:
            b = client.messages.batches.retrieve(batch.id)
            if b.processing_status == "ended":
                break
            time.sleep(30)
        by_short = {key[:64]: key for key, _t, _p in jobs}
        out = {}
        for r in client.messages.batches.results(batch.id):
            key = by_short.get(r.custom_id)
            if key and r.result.type == "succeeded":
                out[key] = self._message_to_record(r.result.message)
            elif key:
                out[key] = {"stop_reason": f"batch_{r.result.type}", "text": "", "input_tokens": 0, "output_tokens": 0}
        return out

    # -- main ----------------------------------------------------------------
    def process(self, pdf_path: Path, tables: list[Table]) -> None:
        todo = candidates(tables)
        pending: list[tuple[str, Table, dict | None]] = []
        for t in todo:
            key = cache_key(self.model, t)
            inp, out = estimate_tokens(t)
            self.usage.est_input_tokens += inp
            self.usage.est_output_tokens += out
            self.usage.planned.append(
                {"table": t.id, "page": t.page, "issues": t.quality_issues, "est_input_tokens": inp, "est_output_tokens": out, "cached": bool(self._cache_get(key))}
            )
            cached = self._cache_get(key)
            if cached is not None:
                self.usage.cache_hits += 1
                if self.enabled and not self.dry_run:
                    self._apply(t, cached)
                continue
            if self.enabled and not self.dry_run:
                pending.append((key, t, None))
        self.usage.est_cost_usd = price(self.model, self.usage.est_input_tokens, self.usage.est_output_tokens, self.batch)
        if not pending:
            return
        jobs = []
        for key, t, _ in pending:
            png, _w, _h = render_png(pdf_path, t)
            jobs.append((key, t, self._params(png, t)))
        results = self._call_batch(jobs) if self.batch else self._call_direct(jobs)
        pages = set()
        for key, t, _p in jobs:
            rec = results.get(key)
            if rec is None:
                self.usage.failures.append(f"{t.id} p{t.page}: no API result")
                continue
            self._cache_put(key, rec)
            self.usage.tables_sent += 1
            pages.add((str(pdf_path), t.page))
            self.usage.input_tokens += rec["input_tokens"]
            self.usage.output_tokens += rec["output_tokens"]
            self._apply(t, rec)
        self.usage.pages_sent += len(pages)
        self.usage.cost_usd = price(self.model, self.usage.input_tokens, self.usage.output_tokens, self.batch)

    def _apply(self, t: Table, rec: dict) -> None:
        if rec.get("stop_reason") not in ("end_turn", "stop_sequence"):
            t.llm_status = f"rejected: model response not usable (stop_reason={rec.get('stop_reason')}); deterministic table kept"
            self.usage.failures.append(f"{t.id} p{t.page}: {t.llm_status}")
            return
        parsed = parse_markdown_table(rec.get("text", ""))
        if parsed is None:
            t.llm_status = "rejected: model output had no markdown table; deterministic table kept"
            self.usage.failures.append(f"{t.id} p{t.page}: {t.llm_status}")
            return
        header, rows = parsed
        cells = header + [c for r in rows for c in r]
        # Title and caption lines stay deterministic; the model only arranges cells,
        # so its cells plus our title lines must cover the region text exactly.
        ok, detail = fidelity_check(cells + [t.title] + t.notes, t.region_text)
        if not ok:
            t.llm_status = f"rejected by fidelity check: {detail}; deterministic table kept"
            self.usage.failures.append(f"{t.id} p{t.page}: {t.llm_status}")
            return
        t.header, t.rows = header, rows
        t.method = "llm"
        t.llm_status = "accepted"
        t.fidelity = "pass"
        t.fidelity_detail = detail


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env reader (KEY=VALUE lines). Existing environment wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)

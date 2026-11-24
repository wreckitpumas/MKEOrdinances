"""Command line entry point: python3 -m pdf_cleaner <pdf or dir> [...] --out <dir>"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .llm import DEFAULT_MODEL, TableStructurer, load_dotenv
from .pipeline import process_pdf, write_outputs
from .profile import resolve_profile
from .report import write_report


def collect_pdfs(inputs: list[str]) -> list[Path]:
    pdfs: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            pdfs.extend(sorted(x for x in p.iterdir() if x.suffix.lower() == ".pdf"))
        elif p.is_file() and p.suffix.lower() == ".pdf":
            pdfs.append(p)
        else:
            raise SystemExit(f"Not a PDF file or directory: {item!r}")
    if not pdfs:
        raise SystemExit("No PDFs found.")
    return pdfs


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python3 -m pdf_cleaner",
        description="Convert legal PDFs into clean, structured .txt files with a manifest.",
    )
    ap.add_argument("inputs", nargs="*", help="PDF files or directories (default: $PDF_DIR)")
    ap.add_argument("--out", default="output", help="output directory (default: output)")
    ap.add_argument("--profile", help="profile name or path to a .yaml file (default: match by filename)")
    ap.add_argument("--no-llm", action="store_true", help="fully deterministic run; never call the API")
    ap.add_argument("--dry-run", action="store_true", help="list tables that would go to the API with token and cost estimates; do not call it")
    ap.add_argument("--model", default=os.environ.get("PDF_CLEANER_MODEL", DEFAULT_MODEL), help=f"model for table structuring (env PDF_CLEANER_MODEL, default {DEFAULT_MODEL})")
    ap.add_argument("--batch", action="store_true", help="use the Message Batches API (half price, results can take up to a day)")
    ap.add_argument("--cache-dir", default=os.environ.get("PDF_CLEANER_CACHE", ".api_cache"), help="API response cache directory (default .api_cache)")
    ap.add_argument("--max-pages", type=int, help="only process the first N pages (for testing)")
    ap.add_argument("--pages", help="only process a page range, e.g. 380-470 (for testing)")
    ap.add_argument("--jobs", type=int, default=1, help="PDFs to process in parallel (default 1)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(Path(".env"))
    inputs = args.inputs or ([os.environ["PDF_DIR"]] if os.environ.get("PDF_DIR") else [])
    if not inputs:
        print("error: give a PDF or directory, or set PDF_DIR", file=sys.stderr)
        return 2
    pdfs = collect_pdfs(inputs)
    out_dir = Path(args.out)

    use_llm = not args.no_llm and not args.dry_run
    if use_llm and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; running deterministically (same as --no-llm).", file=sys.stderr)
        use_llm = False
    mode = "dry run (no API calls)" if args.dry_run else ("enabled" if use_llm else "off (--no-llm or no API key)")

    page_range = tuple(int(x) for x in args.pages.split("-")) if args.pages else None
    jobs = [(pdf, resolve_profile(pdf, args.profile)) for pdf in pdfs]

    def make_structurer():
        return TableStructurer(args.model, Path(args.cache_dir), enabled=use_llm, dry_run=args.dry_run, batch=args.batch)

    results = []
    if args.jobs > 1 and len(jobs) > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futs = [ex.submit(process_pdf, pdf, prof, make_structurer(), args.max_pages, page_range) for pdf, prof in jobs]
            for (pdf, prof), fut in zip(jobs, futs):
                print(f"[{prof.name}] {pdf.name} ...", file=sys.stderr, flush=True)
                results.append(fut.result())
    else:
        for pdf, prof in jobs:
            print(f"[{prof.name}] {pdf.name} ...", file=sys.stderr, flush=True)
            results.append(process_pdf(pdf, prof, make_structurer(), max_pages=args.max_pages, page_range=page_range))
    if not args.dry_run:
        for res in results:
            txt, man = write_outputs(res, out_dir)
            print(f"  wrote {txt} and {man.name}", file=sys.stderr)

    if args.dry_run:
        total_in = total_out = 0
        total_cost = 0.0
        print(f"DRY RUN: tables that would be sent to {args.model}{' (batch, half price)' if args.batch else ''}")
        for r in results:
            u = r.usage
            print(f"\n{r.pdf.name}: {len(u['planned'])} tables, est {u['est_input_tokens']} input + {u['est_output_tokens']} output tokens, ${u['est_cost_usd']:.4f}")
            for p in u["planned"]:
                cached = " (cached, free)" if p["cached"] else ""
                print(f"  p{p['page']:<5} {p['table']:<24} issues={','.join(p['issues'])} in~{p['est_input_tokens']} out~{p['est_output_tokens']}{cached}")
            total_in += u["est_input_tokens"]
            total_out += u["est_output_tokens"]
            total_cost += u["est_cost_usd"]
        print(f"\nTOTAL: est {total_in} input + {total_out} output tokens, about ${total_cost:.4f}")
        return 0

    report = write_report(results, out_dir, llm_mode=mode, model=args.model)
    print(f"wrote {report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

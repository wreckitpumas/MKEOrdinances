# pdf_cleaner

Converts official legal PDFs (the Milwaukee City Charter, Code of Ordinances volumes 1 to 3, the Positions Ordinance and the Salary Ordinance) into clean, structured `.txt` files for a retrieval system that splits by section and cites sources.

Accuracy comes first. Body text comes from the PDF text layer by deterministic extraction and is never generated or rewritten by a model. Every run checks that no text-layer character was lost (see "Checks").

## Setup

```bash
python3 -m pip install -r requirements.txt
```

```bash
sudo apt-get install -y poppler-utils   # provides pdftotext, used by the survey and the checks
```

The source PDFs are read from a path you give on the command line, or from `PDF_DIR`. Paths with spaces work; quote them.

## Commands

```bash
# 1. Survey: layout facts that every profile rule is based on (writes survey/<pdf stem>.txt)
python3 -m pdf_cleaner.survey "$PDF_DIR" --out survey

# 2. Deterministic run (no API calls)
python3 -m pdf_cleaner "$PDF_DIR" --out output --no-llm

# 3. Dry run: which tables would go to the API, with token and cost estimates
python3 -m pdf_cleaner "$PDF_DIR" --dry-run

# 4. Full run with the optional table model step (needs ANTHROPIC_API_KEY in the environment or .env)
python3 -m pdf_cleaner "$PDF_DIR" --out output

# 5. Compare two cleaned files of the same document
python3 -m pdf_cleaner.compare old_cleaned.txt output/Master-V1_cleaned.txt --out compare.md
```

Options: `--model` (or env `PDF_CLEANER_MODEL`, default `claude-haiku-5-5`), `--batch` (Message Batches API, half price, results can take up to a day), `--cache-dir` (default `.api_cache`), `--profile <name or path>` (default: chosen by file name), `--pages 380-470` and `--max-pages N` for quick tests.

## Output

For each PDF, `output/<name>_cleaned.txt` and `output/<name>_manifest.json`, plus one `output/extraction_report.md` for the whole run. `<name>` is the PDF file name without `.pdf` and without a trailing download suffix such as ` (1)`.

### Cleaned text format

| Element | Format |
| --- | --- |
| Chapter | `# CHAPTER 59: HEALTH DEPARTMENT` (the tool adds `: ` between the number and the title lines) |
| Subchapter | `## SUBCHAPTER 1: GENERAL PROVISIONS` |
| Section | `### 999-999-9. Section Title.` (the bold section title from the PDF, verbatim) |
| Positions and Salary headings | `#`, `##`, `###` by profile rule (for example `## DEPARTMENT OF ...`, `### Pay Range ...`) |
| Body text | One paragraph per line. Line breaks inside a paragraph are joined and line-end hyphens repaired. |
| Section history note | Each paragraph of the note on its own line, right after the section text: `[HISTORY s. 999-999-9] (HISTORY: ...)` |
| Chapter history pointer | `[HISTORY ch. 999] For the legislative history of chapter 999, contact ...` |
| Table | A block, see below |

Tables stay inline at the position where they appear in the PDF:

```
[TABLE <id>: <title>]
<optional caption lines, for example a legend or abbreviations>
| header | header |
| --- | --- |
| cell | cell |
[/TABLE]
```

`<id>` is the table's own number when it has one (`Table 999-1` becomes `999-1`), `history-ch<N>` for a chapter's legislative history table, `toc-ch<N>` for a chapter's table of sections, and otherwise `unlabeled-p<page>` (with `-2`, `-3` when a page has several). `<title>` is `(untitled)` when the table has none. A chunker can cut each block out with:

```python
re.findall(r"^\[TABLE ([^:\]]+): (.*)\]\n(.*?)^\[/TABLE\]$", text, re.M | re.S)
```

A table with no header row in the PDF gets an empty markdown header row (`|  |  |`). Tables that continue across pages are merged into one block. Repeated titles and header rows on continuation pages are dropped like running headers, and the manifest lists every page the table covers.

### Manifest

`<name>_manifest.json` holds:

- `page_map`: character offset ranges in the cleaned text and the PDF page each came from.
- `headings` and `sections`: every heading with its offset and page. Sections also carry their history note count and whether chapter-level history covers them.
- `tables`: ID, kind, title, column headers, page range, extraction method (`deterministic` or `llm`), fidelity result, quality issues and model status.
- `history_notes`, `chapter_history`, `removed_running_text`, `warnings`, and the results of all checks.

## How it works

1. **Survey** (`pdf_cleaner/survey.py`) reports page sizes, text layer, header and footer candidates, heading and history patterns, table pages and multi-column pages. Every rule in a profile comes from these reports.
2. **Profiles** (`pdf_cleaner/profiles/<source>.yaml`) hold all layout rules: header and footer patterns and zones, heading patterns, history note patterns, legislative history table layout, table detection settings, and column settings. To tune, edit the YAML; no code changes are needed.
3. **Layout** (`layout.py`) groups text-layer characters into words and lines by position. It handles slightly skewed character matrices (Salary tables) and text rotated 90 degrees (zoning table headers). It then removes headers and footers that match the profile, cuts out tables, finds column gutters per page, and orders lines (left column, then right, with full-width lines in between).
4. **Tables**:
   - Ruled tables are found with pdfplumber. In the Code and Charter only visible rules count, because the survey found invisible boxes around ordinary paragraphs.
   - Legislative history tables are parsed by column position under their header row.
   - Chapter tables of sections become `toc-ch<N>` tables.
   - Salary wage rows printed without borders become tables through a profile row pattern.
   - Every character in a table region is assigned to a cell.
   - A number split by a stray space in the text layer (`7 8,798.02`) is rejoined only when the result is a well-formed number. Each such repair is counted in the manifest.
5. **Assembly** (`assemble.py`) joins lines into paragraphs. It uses indentation, vertical gaps, whether the next word would have fit on the previous line, list markers and sentence punctuation. At column and page breaks it relies only on indentation, list markers and punctuation.
6. **Hyphen repair** uses the document's own vocabulary:
   - `require-` + `ment` becomes `requirement` when that word occurs elsewhere in the document.
   - `non-` + `conforming` keeps its hyphen when `non-conforming` occurs elsewhere.
   - Undecided cases join, except after common compound prefixes such as `non`, `self` and `co`. They are counted as `hyphen_uncertain` in the manifest.
7. **Headings** use bold text: a section heading is a bold section number followed by the bold title, ending at the title's period. Subchapter headings in the Code are regular weight, so their profile does not require bold.
8. **History notes** start with a profile pattern (`(HISTORY:`, statute source notes, and the forms the survey found) and run until their parentheses balance. Each line of a note is prefixed with the section it belongs to. Each line that names a different section triggers a warning.

### Optional model step for tables

Only tables that fail a deterministic quality check go to the API:

- `garbled_spacing`
- `multi_row_header`
- `sparse_header`
- `no_body_rows`
- `continuation_column_mismatch`
- `fidelity_fail`

What gets sent: an image of the table region, plus the text layer of that region.

The model only arranges cells. Its output then has to pass a **fidelity check**:

- Every token in the model's table must appear in the region's text layer. A token can also match two or three consecutive text-layer tokens joined together.
- Every text-layer token must appear in the model's table.

A table that fails is not used. The deterministic table is kept, and the failure is recorded in the manifest and the report.

Responses are cached in `.api_cache/`. The cache key is the content of the table region (its text layer, position and model), not the whole PDF, so a new edition only re-sends tables whose text changed.

Without `ANTHROPIC_API_KEY` the tool runs deterministically and says so.

## Checks (in every manifest and in the report)

- **Completeness**: compares the multiset of alphanumeric characters in the text layer (minus removed running headers, footers, blank-page notes and repeated table titles) with the output (minus tool markup). `lost_chars` must be 0.
- **Text layer cross-check**: pdfplumber characters vs `pdftotext` output for the same PDF.
- **Table fidelity**: every deterministic table is checked against its region's text layer, the same way as model tables.
- **Spot checks**: 5 random sections and 3 random tables per PDF compared with `pdftotext` page text.
- **Suspicious pages**: pages with much less output than text layer, leftover page numbers, lines equal to a running header.

## Tests

```bash
python3 -m pytest -q tests
```

The tests build synthetic PDFs with ReportLab, using placeholder content only. They cover:

- header and footer removal
- hyphen repair
- a table spanning two pages
- the fidelity check rejecting an invented token, with fallback to the deterministic table and caching
- history note attachment
- paths with spaces
- dry run
- compare

## Known limits

- The text layer is the source of truth. Where it has defects, the output keeps them, for example:
  - stray spaces inside words: `DISTR ICTS`
  - overlapping duplicate text: `nonneo ne` in one zoning table, which fails the fidelity check and is reported
- Complex zoning tables (multi-level headers, legend rows) come out with correct cell text, but deterministic column structure can be imperfect. These tables are flagged as candidates for the optional model step.
- Index pages at the end of the Code volumes are kept as text with shortened dot leaders.

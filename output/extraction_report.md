# Extraction report

Table model step: off (--no-llm or no API key). Model setting: `claude-haiku-5-5`.

## Per-source counts

| Source | Pages | Sections | Tables | History notes (section / chapter) | Warnings | Lost chars |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-Positions-Ordinance-11.04.26 | 138 | 99 | 0 | 0 / 0 | 0 | 0 |
| 2026-Salary-Ordinance-11.04.26 | 161 | 162 | 795 | 0 / 0 | 0 | 0 |
| Master-Charter | 342 | 394 | 26 | 403 / 3 | 9 | 0 |
| Master-V1 | 675 | 1046 | 56 | 24 / 44 | 20 | 0 |
| Master-V2 | 624 | 450 | 87 | 0 / 28 | 33 | 0 |
| Master-V3 | 364 | 395 | 33 | 0 / 30 | 10 | 0 |

Lost chars: alphanumeric characters in the PDF text layer (after removing running headers, footers and blank-page notes) that do not appear in the output. 0 means nothing was dropped.

## History notes

- **2026-Positions-Ordinance-11.04.26**: the survey found no history notes in this document, so none are expected.
- **2026-Salary-Ordinance-11.04.26**: the survey found no history notes in this document, so none are expected.
- **Master-Charter**: 281 sections have their own history note; 48 rely on chapter-level history (legislative history table or Municipal Research Library pointer); 65 have neither.
  - Sections with no history note: 1-01, 3-02, 3-03, 3-04, 3-27, 3-30, 4-10, 4-12, 4-14, 4-15, 4-18, 4-19, 4-24, 4-27, 4-32, 5-09, 7-15, 7-31, 10-01, 11-06, 11-15, 11-19, 11-26, 11-27, 11-38, 12-01, 12-07, 12-09, 12-10, 12-12, 12-13, 12-14, 12-16, 12-19, 12-20, 12-22, 12-27, 13-09, 13-11, 14-14 (and 25 more; full list in the manifest)
- **Master-V1**: 18 sections have their own history note; 1010 rely on chapter-level history (legislative history table or Municipal Research Library pointer); 18 have neither.
  - Sections with no history note: 73-1, 73-12, 82-1, 82-2, 82-3, 82-4, 82-5, 82-6, 82-7, 82-8, 82-9, 82-10, 82-11, 82-12, 82-13, 82-16, 82-19, 82-22
- **Master-V2**: 0 sections have their own history note; 437 rely on chapter-level history (legislative history table or Municipal Research Library pointer); 13 have neither.
  - Sections with no history note: 239-1, 239-2, 239-3, 239-4, 239-5, 239-6, 239-7, 239-8, 239-9, 239-10, 239-11, 239-12, 239-13
- **Master-V3**: 0 sections have their own history note; 395 rely on chapter-level history (legislative history table or Municipal Research Library pointer); 0 have neither.

## Tables

- **2026-Salary-Ordinance-11.04.26**: 795 tables (157 ruled, 638 text_rows); 0 span pages; 0 flagged by the quality check.
- **Master-Charter**: 26 tables (3 legislative_history, 23 toc); 3 span pages; 0 flagged by the quality check.
- **Master-V1**: 56 tables (11 legislative_history, 45 toc); 4 span pages; 0 flagged by the quality check.
- **Master-V2**: 87 tables (13 legislative_history, 50 ruled, 24 toc); 14 span pages; 17 flagged by the quality check.
- **Master-V3**: 33 tables (6 legislative_history, 27 toc); 4 span pages; 0 flagged by the quality check.

## Suspicious pages

- **Master-Charter** (2):
  - p267: short output (0 letters/digits attributed, 4159 in text layer); often a paragraph or table that started on the previous page
  - p283: short output (0 letters/digits attributed, 4148 in text layer); often a paragraph or table that started on the previous page
- **Master-V2** (3):
  - p10: short output (0 letters/digits attributed, 699 in text layer); often a paragraph or table that started on the previous page
  - p322: short output (0 letters/digits attributed, 1533 in text layer); often a paragraph or table that started on the previous page
  - p5: line equals a running header: 'Manufactured Home Communities'
- **Master-V3** (2):
  - p206: short output (0 letters/digits attributed, 1972 in text layer); often a paragraph or table that started on the previous page
  - p5: line equals a running header: 'Index'

## Model (API) usage

- Pages sent: 0. Tables sent: 0. Tokens used: 0 input, 0 output.
- Tables eligible for the model step (failed the deterministic quality check): 25.
- Estimated cost of a full model run for those tables: 57804 input and 20446 output tokens, about $0.02 with `claude-haiku-5-5`.

## Checks

| Source | Text layer chars (pdfplumber / pdftotext) | Removed running text | Output chars | Lost | Extra |
| --- | --- | --- | --- | --- | --- |
| 2026-Positions-Ordinance-11.04.26 | 98969 / 98969 | 3317 | 95652 | 0 | 0 |
| 2026-Salary-Ordinance-11.04.26 | 218159 / 218159 | 25654 | 192505 | 0 | 0 |
| Master-Charter | 1126825 / 1126600 | 12881 | 1113944 | 0 | 0 |
| Master-V1 | 2015992 / 2015992 | 25247 | 1990745 | 0 | 0 |
| Master-V2 | 1649309 / 1649298 | 22768 | 1626541 | 0 | 0 |
| Master-V3 | 925270 / 925265 | 12215 | 913055 | 0 | 0 |

### Spot checks (5 random sections and 3 random tables per PDF, compared with pdftotext page text)

**2026-Positions-Ordinance-11.04.26**

- s. CLINICAL SERVICES DIVISION (p55-58): 626 words, 0 not found in page text: pass.
- s. PLANTS - SOUTH ORGANIZATION (p135): 126 words, 0 not found in page text: pass.
- s. ADMINISTRATIVE SERVICES DECISION UNIT (p61): 175 words, 0 not found in page text: pass.
- s. ENVIRONMENTAL COLLABORATION OFFICE (p5): 229 words, 0 not found in page text: pass.
- s. OPERATIONS DIVISION (p38-39): 221 words, 0 not found in page text: pass.

**2026-Salary-Ordinance-11.04.26**

- s. Pay Range 5GN (p103-104): 107 words, 0 not found in page text: pass.
- s. Pay Range 6DN (p109): 25 words, 0 not found in page text: pass.
- s. Pay Range 1KX (p14-16): 542 words, 0 not found in page text: pass.
- s. Pay Range 3TN (p59-60): 109 words, 0 not found in page text: pass.
- s. Pay Range 8BN (p130-131): 216 words, 0 not found in page text: pass.
- table `unlabeled-p71` (p71, 3 rows): pass. all tokens match the text layer
- table `unlabeled-p142-4` (p142, 2 rows): pass. all tokens match the text layer
- table `unlabeled-p79` (p79, 3 rows): pass. all tokens match the text layer

**Master-Charter**

- s. 12-05 (p90): 65 words, 0 not found in page text: pass.
- s. 36-10 (p329-331): 1448 words, 1 not found in page text: check. Not found: ['36-10-2-b-3']
- s. 12-23 (p94): 102 words, 0 not found in page text: pass.
- s. 3-09 (p15): 143 words, 0 not found in page text: pass.
- s. 7-27 (p60): 80 words, 0 not found in page text: pass.
- table `toc-ch13` (p95, 11 rows): pass. all tokens match the text layer
- table `toc-ch35` (p211,212, 11 rows): pass. all tokens match the text layer
- table `toc-ch14` (p99, 34 rows): pass. all tokens match the text layer

**Master-V1**

- s. 106-8 (p502-503): 185 words, 0 not found in page text: pass.
- s. 110-4 (p549): 110 words, 0 not found in page text: pass.
- s. 61-23 (p30): 65 words, 0 not found in page text: pass.
- s. 90-28.5 (p314): 45 words, 0 not found in page text: pass.
- s. 118-29 (p628): 280 words, 0 not found in page text: pass.
- table `toc-ch120` (p662, 12 rows): pass. all tokens match the text layer
- table `history-ch83` (p240, 2 rows): pass. all tokens match the text layer
- table `toc-ch110` (p549, 16 rows): pass. all tokens match the text layer

**Master-V2**

- s. 295-1121 (p567-569): 1526 words, 0 not found in page text: pass.
- s. 236-51 (p196): 43 words, 0 not found in page text: pass.
- s. 295-409 (p420): 84 words, 0 not found in page text: pass.
- s. 240-21 (p205-206): 213 words, 0 not found in page text: pass.
- s. 200-15 (p30-31): 144 words, 0 not found in page text: pass.
- table `295-405-5-b` (p410, 5 rows): pass. all tokens match the text layer
- table `295-503-1` (p429, 41 rows): pass. all tokens match the text layer
- table `history-ch217` (p126, 15 rows): pass. all tokens match the text layer

**Master-V3**

- s. 320-3.5 (p146): 81 words, 0 not found in page text: pass.
- s. 385-9 (p293-294): 46 words, 0 not found in page text: pass.
- s. 320-27 (p172-173): 729 words, 0 not found in page text: pass.
- s. 303-23 (p24): 254 words, 0 not found in page text: pass.
- s. 309-72 (p110): 242 words, 1 not found in page text: check. Not found: ['320-11']
- table `toc-ch350` (p205,206, 57 rows): pass. all tokens match the text layer
- table `toc-ch365` (p279, 8 rows): pass. all tokens match the text layer
- table `history-ch302` (p13, 6 rows): pass. all tokens match the text layer


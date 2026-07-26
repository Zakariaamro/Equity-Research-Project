# Equity Research Platform — Architecture

**Version:** 1.5
**Date:** 2026-07-26
**Owner:** Zakaria
**Status:** Approved for V1 implementation

**Changelog**
- v1.5 — SPEC-003: `sections.text` moved out of SQLite into content-addressed files at
  `data/sections/{hash[:2]}/{hash}.txt.gz`; §6 `sections` schema drops the `text` column,
  `text_hash` becomes the sole link to content. §4 gains a note that this stops future
  repo growth but does not reclaim space already spent in `.git` history. Decision log
  entry 12 added.
- v1.4 — §3.7 added: R-files are served wrapped in an SGML `<DOCUMENT>...<TEXT>...
  </TEXT></DOCUMENT>` envelope, not bare HTML, confirmed against AMZN 10-K
  `0001018724-26-000004`'s `R18.htm`. `html_text.py` now strips this explicitly instead
  of relying on parser leniency to find `<body>` by accident.
- v1.3 — §6 `sections` UNIQUE constraint gains `source_file` (table was empty; no
  migration). §3.6 added: `index.json`'s `type` is a display icon class, not a document
  type; `{accession}-index.html`'s Document Format Files table is the authoritative
  source, confirmed against NVDA 8-K `0001045810-26-000019`.
- v1.2 — §3.3 8-K `items` field confirmed populated (was unverified). Archiving vs.
  analysis scope boundary made explicit: archiving unbounded, analysis bounded by a
  configurable start date (arrives with the spec that needs it).
- v1.1 — Watchlist set to AMZN / NVDA / MU with verified CIKs and fiscal year ends.
  Raw archive policy clarified. Retry parameters specified. Q4 reporting note added.
- v1.0 — Initial architecture.

---

## 1. Purpose

Automatically detect new SEC filings by a watchlist of companies, extract financial
statements and narrative sections, compute ratios, analyse the narrative with an LLM,
and surface the results on a dashboard — without the operator's laptop being on.

### Watchlist (verified against SEC 2026-07-25)

| Company | Ticker | CIK | Fiscal year end | 10-K typically filed |
|---|---|---|---|---|
| Amazon.com Inc | AMZN | `0001018724` | 12-31 | Early February |
| NVIDIA Corp | NVDA | `0001045810` | 01-31 | Late February |
| Micron Technology Inc | MU | `0000723125` | 09-03 | Early October |

**Three different fiscal calendars.** This is a feature, not an annoyance — it forces the
system to treat fiscal periods as data rather than assume calendar quarters, which is
correct behaviour for any real research tool. Never infer a period from a filing date.

**Development order:** Amazon first. It is the only watchlist member expected to file
during the V1 build window (Q2 10-Q, historically the first days of August). NVIDIA's
Q2 FY2027 10-Q lands around late August; Micron files nothing until its 10-K in early
October. Adding NVDA and MU is a `config.py` change, not a code change — that is the
test of whether the architecture is right.

---

## 2. Core Design Rule

Four layers of information, never mixed, enforced by separate tables:

```
FACTS            filings, sections, xbrl_facts     observed from SEC, never derived
   ↓
CALCULATIONS     metrics                           deterministic, reproducible, auditable
   ↓
INTERPRETATION   analyses, findings                LLM output, always attributed
   ↓
PRESENTATION     dashboard                         reads all three, labels each clearly
```

Dependencies point **downward only**. A row in `xbrl_facts` never references a finding.
Every AI-generated row records the model, prompt version, and the source section it came from.

This is not a documentation convention. It is the schema. Violating it requires
restructuring tables, which is deliberately harder than doing it correctly.

---

## 3. Verified Findings (2026-07-25)

Confirmed against live SEC data. These are the basis for the design.

### 3.1 `FilingSummary.xml` solves section segmentation

Every XBRL filing has `FilingSummary.xml` listing each report with a `MenuCategory`:

| MenuCategory | Contents | V1 action |
|---|---|---|
| `Cover` | Cover Page, Audit Information | Metadata only |
| `Statements` | Income statement, balance sheet, cash flow, equity, comprehensive income | **Ingest** |
| `Notes` | Individually named footnotes | **Ingest — primary AI input** |
| `Policies` | Accounting policies | Ingest |
| `Tables` | XBRL table detail | **Skip — redundant** |
| `Details` | XBRL tagging detail | **Skip — redundant** |

Confirmed on Amazon 10-K `0001018724-26-000004` (FY2025) and 10-Q
`0001018724-26-000014` (Q1 2026). Structure identical across both form types.

Amazon 10-K notes: Description of Business & Accounting Policies · Financial Instruments ·
Property and Equipment · Leases · Acquisitions, Goodwill & Acquired Intangibles · Debt ·
Commitments and Contingencies · Stockholders' Equity · Income Taxes · Segment Information
(plus Insider Trading and Cybersecurity boilerplate).

**Consequence:** section extraction is XML parsing plus targeted HTML fetches, not
document-structure inference. This removed the largest risk in the project.

### 3.2 MD&A is NOT in the R-files

R-files contain only XBRL-tagged financial statement content. Item 7 (MD&A) and
Item 1A (Risk Factors) live in the primary document and require separate extraction.

**Known trap:** the string "Item 7. Management's Discussion" also appears in the table
of contents. Take the *last* match, not the first.

### 3.3 8-K handling: filter on Item 2.02

Forward guidance appears in the earnings press release, filed as **Exhibit 99.1 to an
8-K carrying Item 2.02 ("Results of Operations and Financial Condition")** — roughly
four per year per company.

**Rule:** ingest 8-Ks only when Item 2.02 is present. **The substantive content is in
Exhibit 99.1, not the 8-K body** — see §4.1.

**Verified (SPEC-001 implementation):** the `items` field in the EDGAR submissions API
IS populated, confirmed against live AMZN, NVDA, and MU data (e.g. `"2.02,9.01"`). The
filing-index-page fallback is retained anyway, as insurance against any filing where
the submissions JSON's `items` value is blank — exclude only if both sources yield
nothing.

### 3.4 Filter at the source

Amazon filed accessions `-25-000070` through `-25-000132` in one stretch of 2025,
overwhelmingly Form 4 insider transactions. The monitor must filter by form type
before doing anything else.

### 3.5 Filing sizes vary widely

Amazon 10-K ≈ 12 MB. NVIDIA ≈ 11 MB. Micron ranges 17–46 MB. This matters because
raw filings are committed to the repo — see §4.1 and the accepted-debt note.

### 3.6 There are two different "filing index" resources, and only one has document types

Easy to conflate; confirmed empirically during SPEC-002 review against NVDA 8-K
`0001045810-26-000019`.

- **`index.json`** (`.../{accession}/index.json`) — the machine-readable directory
  listing. Its `type` field is a **display icon class** (`text.gif`, `compressed.gif`,
  `image2.gif`), not a document type. Every file in the NVDA filing above, including the
  actual Exhibit 99.1, came back `text.gif`. Unusable for identifying exhibit numbers.
- **`{accession}-index.html`** — the human-readable filing page. Its "Document Format
  Files" table has a `Type` column that is SEC-declared and authoritative: it correctly
  labels NVDA's `q4fy26pr.htm` as `EX-99.1` even though the filename gives no hint.
  This is the resource that actually resolves the SPEC-001 Exhibit 99.1 false positive.

**Consequence:** the `-index.html` table does not enumerate every archived file either —
viewer-support files (`FilingSummary.xml`, `R*.htm`, `MetaLinks.json`, `report.css`,
`Show.js`) never appear in it. A complete, typed manifest requires both resources:
`index.json` for the full file list, `-index.html` for types where they exist. This is
exactly the kind of thing that would otherwise be rediscovered painfully in six months.

### 3.7 R-files are served wrapped in an SGML envelope, not as bare HTML

Confirmed empirically during SPEC-002 implementation against AMZN 10-K
`0001018724-26-000004`'s `R18.htm` (Income Taxes note).

Fetching an R-file directly from its archive URL —
`https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_nodash}/R18.htm` — does
**not** return bare HTML. It returns:

```
<DOCUMENT>
<TYPE>XML
<SEQUENCE>31
<FILENAME>R18.htm
<DESCRIPTION>IDEA: XBRL DOCUMENT
<TEXT>
<html>...</html>
</TEXT>
</DOCUMENT>
```

**Reproduction:** `curl -A "<UA>" https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/R18.htm`
— HTTP 200, `content-type: text/html`, byte-identical on repeat fetch. Not a caching
artifact or a fluke of one filing; this is how SEC serves the R-file endpoint.

**Why this matters:** `BeautifulSoup`'s `html.parser` happens to be lenient enough to
find the nested `<body>` regardless of the malformed SGML tags wrapping it (it nests
everything under `<document><type><sequence>...<text><html><body>`, and `.body` still
finds it) — so naive code that assumes bare HTML will silently produce correct-looking
output *by accident*. That is a worse failure mode than an outright error: it means the
parsing was never actually validated to handle this, and a future SEC formatting change
could silently corrupt extracted text with no signal. SPEC-002 R4's `html_text.py`
therefore strips this envelope explicitly before parsing and raises if no `<body>` is
found afterward, rather than depending on parser leniency.

---

## 4. Deployment

Constraint: must run without the operator's machine being on. Cost target: $0 infrastructure.

| Concern | Choice | Why |
|---|---|---|
| Scheduler + compute | **GitHub Actions** (cron workflow) | Always-on, free on public repos, built-in secrets, no Linux admin |
| State | **SQLite committed to the repo** | Runners are ephemeral; repo is the durable store |
| Raw filings | gzipped in `data/raw/` | Permanent archive |
| Dashboard | **Streamlit Community Cloud** | Free, deploys from the same repo, redeploys on push |
| LLM | Anthropic API, section-level calls | Budget ≈ $20 total |

**Why not a VM:** SSH, systemd and deployment would consume ~8 of ~30 available hours
and teach operations rather than system design. Revisit if the watchlist exceeds ~10 companies.

### 4.1 Raw archive policy

**Archive every document containing original content. Do not archive SEC-generated
renderings of content already archived.**

| Form | Archive |
|---|---|
| 10-K, 10-Q | Primary document + `FilingSummary.xml` |
| 8-K | **All documents listed in the filing index**, including Exhibit 99.1 |

Rationale: R-files are SEC renderings derived from the primary document plus its XBRL —
regenerable, so archiving them is duplication. Exhibit 99.1 is genuinely separate content
and is the *only* place guidance appears; an 8-K archived without it is worthless.
8-K filings are small, so archiving them wholesale costs little.

**Archiving is unbounded. LLM analysis is not.** Discovery and archival run over a
company's full filing history with no date floor — archiving is free (SEC hosting,
no API cost) and unbounded history is valuable test material for later specs.
Analysis is different: it calls a paid LLM per section, and an unbounded first run
would walk the *entire* filing history and consume the whole project budget in one
pass. Analysis must therefore be bounded by a configurable start date. That config
key is not added in SPEC-001 — it arrives with the spec that introduces `analyze.py`,
since nothing consumes it yet.

**Accepted debt (time-boxed, deliberate):**
- SQLite-in-git does not scale past a handful of companies. Fine at V1 size.
- Repo growth: three companies at roughly 15 MB per annual filing, gzipped, is tens of
  MB per year. Acceptable now; revisit before adding a fourth company or backfilling
  more than three years.
- GitHub Actions cron drifts 5–30 minutes. Irrelevant given filings are analysed within hours.
- Streamlit Community Cloud apps are public. All source data is public; no secrets in the DB.

**Note (SPEC-003):** section text was moved out of `app.db` into content-addressed files
under `data/sections/` (§6) specifically because it was the dominant contributor to the
33.7 MB `app.db` committed after SPEC-002. This stops *future* growth from repeating that
pattern — it does not shrink `.git` history already spent on the pre-migration `app.db`
blobs committed during SPEC-001 and SPEC-002. Reclaiming that would require a history
rewrite, deliberately out of scope; the existing `.git` directory stays at its
pre-migration size permanently.

---

## 5. Module Layout

```
edgar/
  config.py         Watchlist, CIKs, form types, note allow-list, concept aliases, model names
  db.py             Schema creation, connection handling
  edgar_client.py   ALL HTTP to SEC. Rate limiting + User-Agent live here and nowhere else
  monitor.py        Poll for filings not yet in the database
  fetch.py          Download and archive per §4.1
  sections.py       Parse FilingSummary, fetch R-files, extract MD&A, clean to text
  xbrl.py           Pull companyfacts, normalise into xbrl_facts
  metrics.py        Compute ratios from xbrl_facts, record formula + inputs
  llm.py            Anthropic client, hash-based response cache, cost accounting
  analyze.py        Apply prompts to sections, write analyses + findings
  pipeline.py       Orchestration. Imports everything; nothing imports it
prompts/            Versioned prompt files, e.g. note_materiality_v1.md
dashboard/app.py    Streamlit
tests/
data/
  raw/              Gzipped filings
  app.db            SQLite
.github/workflows/  poll.yml
```

**Rules:**
- Dependencies form a directed acyclic graph. `pipeline.py` is the only orchestrator.
- `edgar_client.py` is the sole module that makes requests to sec.gov.
- No literal values outside `config.py`.
- Every module is importable and testable in isolation.

---

## 6. Database Schema

Seven tables.

```sql
-- ============ REFERENCE ============
CREATE TABLE companies (
    cik              TEXT PRIMARY KEY,   -- zero-padded 10 digits
    ticker           TEXT NOT NULL,
    name             TEXT NOT NULL,
    fiscal_year_end  TEXT                -- 'MMDD'
);

-- ============ LAYER 1: FACTS ============
CREATE TABLE filings (
    accession_no   TEXT PRIMARY KEY,     -- '0001018724-26-000004'
    cik            TEXT NOT NULL REFERENCES companies(cik),
    form_type      TEXT NOT NULL,        -- '10-K' | '10-Q' | '8-K'
    filing_date    TEXT NOT NULL,        -- ISO 8601
    period_end     TEXT,                 -- ISO 8601
    items          TEXT,                 -- 8-K items, comma-separated; NULL otherwise
    primary_doc    TEXT,                 -- 'amzn-20260331.htm'
    raw_path       TEXT,                 -- directory holding gzipped archives
    discovered_at  TEXT NOT NULL,
    status         TEXT NOT NULL         -- discovered|fetched|sectioned|analyzed|failed
);

CREATE TABLE sections (
    id            INTEGER PRIMARY KEY,
    accession_no  TEXT NOT NULL REFERENCES filings(accession_no),
    category      TEXT NOT NULL,         -- Statements|Notes|Policies|MDA|RiskFactors|Exhibit
    short_name    TEXT NOT NULL,         -- 'Income Taxes'
    source_file   TEXT,                  -- 'R14.htm' | 'primary'
    position      INTEGER,
    text_hash     TEXT NOT NULL,         -- sha256 of cleaned plain text; sole link to content
    UNIQUE(accession_no, category, short_name, source_file)
);

CREATE TABLE xbrl_facts (
    id            INTEGER PRIMARY KEY,
    cik           TEXT NOT NULL REFERENCES companies(cik),
    taxonomy      TEXT NOT NULL,         -- 'us-gaap' | 'dei'
    concept       TEXT NOT NULL,
    unit          TEXT NOT NULL,         -- 'USD'
    period_start  TEXT,
    period_end    TEXT NOT NULL,
    fiscal_year   INTEGER,
    fiscal_period TEXT,                  -- 'FY' | 'Q1' | 'Q2' | 'Q3'
    value         REAL NOT NULL,
    accession_no  TEXT,
    form_type     TEXT,
    UNIQUE(cik, concept, unit, period_start, period_end, accession_no)
);

-- ============ LAYER 2: CALCULATIONS ============
CREATE TABLE metrics (
    id           INTEGER PRIMARY KEY,
    cik          TEXT NOT NULL REFERENCES companies(cik),
    accession_no TEXT REFERENCES filings(accession_no),
    period_end   TEXT NOT NULL,
    name         TEXT NOT NULL,          -- 'operating_margin'
    value        REAL,
    formula      TEXT NOT NULL,          -- 'OperatingIncomeLoss / Revenues'
    inputs_json  TEXT NOT NULL,
    calc_version TEXT NOT NULL,
    computed_at  TEXT NOT NULL,
    UNIQUE(cik, period_end, name, calc_version)
);

-- ============ LAYER 3: AI INTERPRETATION ============
CREATE TABLE analyses (
    id             INTEGER PRIMARY KEY,
    section_id     INTEGER NOT NULL REFERENCES sections(id),
    prompt_name    TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model          TEXT NOT NULL,
    input_hash     TEXT NOT NULL,        -- sha256(section text + rendered prompt)
    output_json    TEXT NOT NULL,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    cost_usd       REAL,
    created_at     TEXT NOT NULL,
    UNIQUE(input_hash)                   -- this IS the response cache
);

CREATE TABLE findings (
    id           INTEGER PRIMARY KEY,
    analysis_id  INTEGER NOT NULL REFERENCES analyses(id),
    accession_no TEXT NOT NULL REFERENCES filings(accession_no),
    category     TEXT NOT NULL,          -- red_flag|guidance|management_language|note_item
    severity     TEXT,                   -- high|medium|low
    headline     TEXT NOT NULL,
    detail       TEXT,
    quote        TEXT,                   -- verbatim text from the filing
    created_at   TEXT NOT NULL
);
```

### Note on `sections.text` (removed in SPEC-003)

Cleaned section text is not stored in the database. It lives in gzipped,
content-addressed files at `data/sections/{hash[:2]}/{hash}.txt.gz`, keyed by
`text_hash`. The file path is never stored — it is derivable from the hash, and storing
it would let the two disagree. `text_hash` is therefore not just a cache key (as it was
under SPEC-002) but the sole link between a row and its content.

Why: SQLite files don't delta-compress in git, and section text was ~32 MB of the 33.7 MB
`app.db` committed after SPEC-002. Text is immutable — a filed note never changes — so it
belongs in the same class of storage as `data/raw/`, not in the one file rewritten on
every pipeline run. See SPEC-003 and the §4.1 note above. This is the reasoning future
specs should follow when deciding where something belongs: **keep mutable state small;
make large, immutable data content-addressed and out of the database.**

### Why these columns exist

**`metrics.formula` and `metrics.inputs_json`** — every ratio on the dashboard can display
its own derivation. An analyst tool showing "Operating margin: 11.2%" without letting you
see numerator and denominator is a toy. Auditability at no cost.

**`analyses.input_hash` with a UNIQUE constraint** — the database itself prevents paying
twice for an identical call. Change the prompt, the hash changes, you get a fresh analysis
and the old one is retained for comparison. Prompt versioning and cost control from one column.

**`findings.quote`** — every AI claim must be anchored to verbatim filing text. The strongest
available control against hallucination, and structural rather than a prompt instruction.
A finding without a quote is a bug.

**`filings.status`** — makes the pipeline resumable and idempotent.

### Note on `sections.UNIQUE`

Changed from `UNIQUE(accession_no, category, short_name)` to `UNIQUE(accession_no,
category, short_name, source_file)` during SPEC-002 review, before any row had ever been
written to the table. Two reports in the same filing can legitimately share a
`MenuCategory`/`ShortName` pair; `source_file` (the distinct R-file, e.g. `R14.htm`)
disambiguates them without requiring `short_name` to ever be anything but the verbatim
SEC value. Because the table was empty, this was a schema edit, not a migration.

### Note on `fiscal_period`

There is deliberately no `Q4`. Companies do not file a Q4 10-Q; the fourth quarter is
covered by the 10-K. SEC XBRL reports `FY` for annual figures, and Q4 must be *derived*
(FY minus Q1 + Q2 + Q3). When that derivation is implemented it belongs in `metrics`,
not `xbrl_facts` — it is a calculation, not an observed fact.

---

## 7. V1 Metric Set

Computed in `metrics.py` from `xbrl_facts`. Version as `v1`.

| Metric | Definition |
|---|---|
| `revenue_yoy` | Revenue / prior-year same-period revenue − 1 |
| `gross_margin` | (Revenue − COGS) / Revenue |
| `operating_margin` | Operating income / Revenue |
| `net_margin` | Net income / Revenue |
| `rnd_intensity` | R&D expense / Revenue |
| `current_ratio` | Current assets / Current liabilities |
| `net_debt` | Total debt − cash and equivalents − short-term investments |
| `interest_coverage` | Operating income / Interest expense |
| `free_cash_flow` | Cash from operations − capital expenditures |
| `fcf_margin` | Free cash flow / Revenue |

**Implementation note — concept aliasing.** Companies tag the same idea differently.
Amazon reports net sales as `RevenueFromContractWithCustomerExcludingAssessedTax`, not
`Revenues`, and does not report gross profit directly. Micron and NVIDIA, as semiconductor
manufacturers, do report gross profit. `config.py` must hold an ordered alias list per
canonical concept; `metrics.py` takes the first that resolves. Where no input resolves,
write `NULL` — never a guess.

---

## 8. Scope

### V1 — Must have
- Poll for new 10-K, 10-Q, and Item 2.02 8-K filings for the watchlist
- Store raw filings permanently per §4.1
- Extract statements, notes, and MD&A into `sections`
- Ingest XBRL facts; compute the ten metrics above
- LLM analysis of notes and MD&A producing quote-anchored findings
- Streamlit dashboard showing statements, ratios, findings, and provenance
- Runs unattended on GitHub Actions

### V2 — Should have
- Year-over-year comparison of the same note across filings
- Material change detection
- Historical backfill (same code path, different inputs)
- Derived Q4 figures
- Email or push notification on high-severity findings

### Future — Could have
- Cross-company comparison (NVDA/MU supply-chain linkage is a natural first case)
- Earnings call transcripts
- Full risk factor diffing
- Valuation modelling

---

## 9. Decision Log

| # | Decision | Rationale | Date |
|---|---|---|---|
| 1 | Facts / Calculations / Interpretation as separate tables | Structural enforcement beats convention | 2026-07-25 |
| 2 | GitHub Actions over a rented VM | $0, no ops burden, ~8 hours saved | 2026-07-25 |
| 3 | SQLite committed to git | Ephemeral runners need durable state; free version history | 2026-07-25 |
| 4 | Section-level LLM calls, never whole filings | 10× cheaper and produces better analysis | 2026-07-25 |
| 5 | R-files via FilingSummary for statements and notes | Removes the largest technical risk | 2026-07-25 |
| 6 | 8-Ks filtered to Item 2.02 only | Guidance without the noise | 2026-07-25 |
| 7 | Automate last — manual CLI first, cron at the end | Automation multiplies bugs and hides failures | 2026-07-25 |
| 8 | Watchlist AMZN / NVDA / MU | AI-infrastructure basket; three distinct fiscal calendars stress the design correctly | 2026-07-25 |
| 9 | Archive original content, not SEC renderings | Ex-99.1 is unique and essential; R-files are regenerable | 2026-07-25 |
| 10 | Build against Amazon first | Only watchlist member expected to file during the build window | 2026-07-25 |
| 11 | Archiving unbounded; LLM analysis bounded by a configurable start date | Archiving is free and unbounded history is useful test material; unbounded analysis would burn the entire LLM budget on one run | 2026-07-26 |
| 12 | Section text moved out of SQLite into content-addressed files (`data/sections/`); `sections.text_hash` is the sole link | Mutable state (`app.db`, rewritten every run) must stay small; large, immutable data belongs outside it, one write ever, mirroring `data/raw/` | 2026-07-26 |

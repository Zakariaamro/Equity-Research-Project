# Equity Research Platform — Architecture

**Version:** 1.1
**Date:** 2026-07-25
**Owner:** Zakaria
**Status:** Approved for V1 implementation

**Changelog**
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

*Unverified:* the `items` field in the EDGAR submissions API is expected to carry
8-K item numbers (e.g. `"2.02,9.01"`). Confirm during implementation; if absent,
fall back to the filing index. Exclude only if both sources yield nothing.

### 3.4 Filter at the source

Amazon filed accessions `-25-000070` through `-25-000132` in one stretch of 2025,
overwhelmingly Form 4 insider transactions. The monitor must filter by form type
before doing anything else.

### 3.5 Filing sizes vary widely

Amazon 10-K ≈ 12 MB. NVIDIA ≈ 11 MB. Micron ranges 17–46 MB. This matters because
raw filings are committed to the repo — see §4.1 and the accepted-debt note.

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

**Accepted debt (time-boxed, deliberate):**
- SQLite-in-git does not scale past a handful of companies. Fine at V1 size.
- Repo growth: three companies at roughly 15 MB per annual filing, gzipped, is tens of
  MB per year. Acceptable now; revisit before adding a fourth company or backfilling
  more than three years.
- GitHub Actions cron drifts 5–30 minutes. Irrelevant given filings are analysed within hours.
- Streamlit Community Cloud apps are public. All source data is public; no secrets in the DB.

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
    text          TEXT NOT NULL,         -- cleaned plain text
    text_hash     TEXT NOT NULL,         -- sha256, used as cache key
    UNIQUE(accession_no, category, short_name)
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

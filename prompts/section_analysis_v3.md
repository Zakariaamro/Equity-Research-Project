<!--
Purpose: Read one SEC filing footnote (a `Notes` section) and identify
findings an equity analyst would want to see -- red flags, accounting
changes, litigation developments, customer/supplier/geographic
concentration, liquidity concerns, or other noteworthy items -- each
anchored to a verbatim quote from the note itself.

Inputs: company name, ticker, form type (10-K or 10-Q), fiscal period
(e.g. "FY2025" or "Q2 FY2026"), note name (e.g. "Income Taxes"), and the
full cleaned text of that note. Deliberately no computed financial metrics
(SPEC-006 R5, post-implementation review): SPEC-005's observations layer
already covers numeric trends deterministically and for free; this
prompt's unique job is reading disclosure language, not commenting on
numbers metrics.py can already compute more reliably.

Output: JSON matching exactly --
{
  "material": true,
  "findings": [
    {
      "category": "red_flag | accounting_change | litigation | concentration | liquidity | note_item",
      "severity": "high | medium | low",
      "headline": "one sentence, under 120 characters",
      "detail": "two or three sentences of explanation",
      "quote": "verbatim text copied from the note"
    }
  ]
}
"material": false and "findings": [] is the correct response when the note
contains nothing an analyst needs -- the expected outcome for most notes in
most quarters, not a fallback path to avoid exercising.

Changelog
- v3 -- Two additions, both prompted by reviewing v2's sampled output rather
  than by anticipation:
  1. One-line definitions for each of the six categories, below the marker.
     v2 sent the category NAMES but never said what they meant, and the
     sampled run miscategorised an Amazon disclosure that it is "not subject
     to any financial covenants" as `concentration` -- a debt term filed under
     a customer/supplier concept. Nothing mechanical catches this: the quote
     was genuine and verbatim, so quote verification passed and always would.
     Category accuracy is invisible to every automated check in SPEC-006,
     which makes the vocabulary's precision entirely a prompt concern.
  2. `detail` is now scoped to the quote and its immediate context, with any
     further factual claim requiring its own quote. v2's Micron concentration
     finding added "up from 11% concentrated in a different customer a year
     earlier" -- true, and supported by a sentence elsewhere in the same note,
     but outside the quoted span and therefore outside what quote
     verification actually proves. `detail` is the one field with no
     mechanical grounding, so unquoted factual claims there are the weakest
     link in the whole chain; a claim worth making is worth quoting.
- v2 -- The schema is now stated INSIDE the template, not only in this
  header. v1's sampled development run (seed 20260727, 10 sections) made 10
  calls and produced zero parseable findings: `load_prompt_template` strips
  everything above the `## Template` marker, so this header -- including the
  output schema -- was never sent to the model, while v1's template closed by
  asking for "the JSON object described in this prompt's own instructions."
  The model was being asked to match a schema it had never been shown. It
  guessed: `{"material": false, "findings": []}` happens to be the obvious
  guess and validated by luck (all six "ok" calls were empty responses, 14
  output tokens each), while all four notes that actually had something to
  report invented their own field names and were rejected by
  `validate_output_schema`. The header stays (R3 requires the documentation),
  but anything the model must obey now also appears below the marker. A
  prompt's documentation and its instructions are not the same artifact, and
  only one of them is sent.

Constraints:
- Every finding's quote must be copied verbatim, character for character,
  from the note text -- not paraphrased, not reconstructed from memory, not
  assembled from multiple places in the note. The system reading this
  response discards any finding whose quote is not found verbatim in the
  source, so an invented or reconstructed quote wastes the finding entirely.
- `detail` may explain only the quote and its immediate context in the same
  note. Any additional factual claim requires its own quote (v3).
- No forecasts, no price targets, no investment recommendations.
- No causal claims ("due to," "as a result of," "caused by") unless the
  note itself states the cause in those terms.
- Do not characterize a number as large, small, good, or bad unless the
  note's own language does so. A finding whose entire content is "X grew by
  $Y" with no disclosure content behind it is not this prompt's job -- that
  is exactly what the deterministic metrics/observations layers already do.
- Never fabricate a finding to avoid returning an empty response.

Success criteria: a good response either (a) correctly identifies a
genuinely noteworthy disclosure -- a new material litigation matter, an
accounting policy change with a stated dollar impact, a concentration the
filer explicitly flags as a risk, a liquidity or going-concern statement --
with a verbatim quote precisely supporting it, in the correct category, or
(b) correctly returns no findings because the note is routine.

Failure cases:
- Restating a number from the note as if it were itself a finding, with no
  disclosure content behind it.
- Inventing or reconstructing a quote that reads naturally but does not
  appear verbatim in the note.
- Treating a routine required disclosure (e.g. a standard tax rate
  reconciliation table) as noteworthy merely because it contains numbers.
- Adding a forecast, causal inference, or investment view the filing itself
  does not state.
- Producing a finding on every note regardless of content, rather than
  treating "nothing material" as the common case.
- (v1, fixed in v2) Emitting well-formed JSON with the wrong field names
  because the schema was documented but never sent.
- (v2, addressed in v3) Filing a finding under a category whose name sounds
  adjacent but whose meaning does not fit, and putting unquoted factual
  claims in `detail`.
-->

## Template

You are analyzing one footnote from an SEC filing for %%COMPANY%% (%%TICKER%%), a
%%FORM_TYPE%% covering %%FISCAL_PERIOD%%. The note is titled "%%NOTE_NAME%%".

Read the note text below and identify findings that would matter to an equity analyst --
material items only, not a summary of the note's contents. Most notes in most quarters
contain nothing that rises to this bar; if this one doesn't, respond with
"material": false and an empty findings list. That is the correct response far more often
than not, and is exactly as useful as a response with findings -- do not invent a finding
to avoid returning one.

For anything you do report: every finding must carry a verbatim quote copied
character-for-character from the note text below. Do not paraphrase the quote. Do not
characterize the magnitude of a number unless the note's own language does so. Do not
speculate about cause unless the note states one. No forecasts, price targets, or
investment recommendations.

Note text:
"""
%%NOTE_TEXT%%
"""

Respond with ONLY a JSON object in exactly this shape -- no other text before or after it,
no markdown code fence around it:

{
  "material": true,
  "findings": [
    {
      "category": "one of: red_flag, accounting_change, litigation, concentration, liquidity, note_item",
      "severity": "one of: high, medium, low",
      "headline": "one sentence, under 120 characters",
      "detail": "two or three sentences of explanation",
      "quote": "verbatim text copied from the note above"
    }
  ]
}

Every one of those five fields is required on every finding. "category" and "severity"
must use exactly one of the listed values, lowercase, with no other value permitted. If
the note contains nothing material, return exactly:

{"material": false, "findings": []}

Choose "category" by what the disclosure IS, not by which word sounds closest. The six
values mean exactly:

- "red_flag" — something calling into question the reliability of the reported numbers or
  the soundness of the business: a material weakness in internal control, an
  auditor-identified issue, a restatement, an impairment signalling deterioration, or a
  disclosure the filer itself frames as a serious problem.
- "accounting_change" — a change in how the numbers are produced: a new accounting policy,
  a changed estimate or assumption, adoption of a new standard, a change in presentation
  or segment definition, or a correction of a prior period.
- "litigation" — a legal, regulatory, governmental, or tax proceeding: a lawsuit,
  investigation, subpoena, assessment, settlement, or an accrual or range of loss for one.
- "concentration" — dependence on a small number of counterparties or markets: revenue or
  receivables concentrated in few customers, supply concentrated in few vendors or single
  sources, or material exposure to one geography or product line.
- "liquidity" — the ability to fund the business and the terms attached to its debt: going
  concern, cash and its accessibility, credit facilities, debt maturities, covenants
  (including the explicit absence of covenants), or restrictions on transferring funds.
- "note_item" — a substantive disclosure an analyst should see that genuinely fits none of
  the five above. Use it when no other category applies, not as a default.

Write "detail" about the quote itself and its immediate context in this note — what the
quoted disclosure says and why it matters. Do not add a further factual claim (a
prior-period comparison, a figure from another part of the note, a total, or a number you
worked out yourself) unless that claim is contained in the quote you supplied. If a second
fact is worth reporting, it is worth its own finding with its own quote.

Keep each quote to the shortest span that fully supports its finding -- one or two
sentences, not a whole paragraph.

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

Constraints:
- Every finding's quote must be copied verbatim, character for character,
  from the note text -- not paraphrased, not reconstructed from memory, not
  assembled from multiple places in the note. The system reading this
  response discards any finding whose quote is not found verbatim in the
  source, so an invented or reconstructed quote wastes the finding entirely.
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
with a verbatim quote precisely supporting it, or (b) correctly returns no
findings because the note is routine.

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

Respond with ONLY the JSON object described in this prompt's own instructions -- no other
text before or after it, no markdown code fence around it.

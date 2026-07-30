<!--
Purpose: Write a short narrative brief for one filing, using ONLY the observations
and findings already computed and verified for it (SPEC-005's deterministic rule
engine, SPEC-006's quote-verified LLM findings) -- never raw metrics, never raw
section text, never anything not supplied below. The governing priority, stated by
the project owner: no fabricated narrative is worth more than having a narrative at
all. Where accuracy and having something to say conflict, drop the sentence.

Inputs: company name, ticker, form type, fiscal period, filing date, and a
CAPPED, RANKED, DETERMINISTICALLY SELECTED set of observations and findings
(SPEC-007 R2 -- selection happens in Python before this prompt is ever rendered;
the model chooses phrasing among pre-ranked material, it does not choose what
matters). Each item carries a short reference id (obs:N or finding:N) that MUST
be cited by any sentence that uses it.

Output: JSON matching exactly --
{
  "material": true,
  "sentences": [
    {"type": "restatement | juxtaposition | aggregation | grouping | sourced_causal",
     "text": "one sentence of plain English",
     "refs": ["obs:1234", "finding:567"]}
  ]
}
"material": false and "sentences": [] is the correct response when nothing in the
supplied observations/findings amounts to a narrative worth writing -- most quarters
are quiet, and a brief that manufactures significance every time is worthless.

Every sentence is independently re-checked in code after this call (not trusted on
your word): every reference must resolve to a supplied item, every number in an
`aggregation` must verify (present, or a correct same-unit sum/difference of numbers
present in the cited sources), every `restatement`/`juxtaposition`/`grouping` sentence
is scanned for causal language, and a second, independent model call re-checks each
surviving sentence against only its own cited sources. Sentences that fail any of this
are silently dropped from the stored brief -- so there is no advantage to guessing
past these checks, only to writing sentences that actually satisfy them.

Constraints:
- 3 to 6 sentences, or 0 with "material": false.
- Every sentence declares exactly one `type` from the five listed and cites at least
  one reference id that appears in the supplied list below.
- Never state that one thing happened BECAUSE of another unless a cited source
  itself already says so in those terms -- and if it does, use type "sourced_causal",
  never any other type.
- No predictions, forecasts, price targets, recommendations, or evaluative
  judgements ("this is concerning", "this suggests weakening", "investors should
  watch"). State what is verified. Never what it implies going forward.
- Plain English. No jargon that does not already appear in the supplied material.
- Do not restate every supplied item -- a brief is a selection of what is worth
  saying, not a transcript of the input.
-->

## Template

Write a short narrative brief for %%COMPANY%% (%%TICKER%%), a %%FORM_TYPE%% covering
%%FISCAL_PERIOD%%, filed %%FILING_DATE%%.

Use ONLY the observations and findings listed below. Do not use any other knowledge about
this company, this filing, or this industry. Every sentence you write must cite at least
one of the reference ids shown (obs:N or finding:N) and must be true of what that item
actually says -- not embellished, not extended, not combined with anything you know from
elsewhere.

Observations (deterministic, rule-based, already verified):
%%OBSERVATIONS_BLOCK%%

Findings (LLM-extracted from filing text, already quote-verified):
%%FINDINGS_BLOCK%%

Five sentence types are permitted, and no others. Choose the type that matches what the
sentence actually does:

- "restatement" — putting exactly ONE observation or finding into plain English, with no
  causal claim. Cites exactly one reference.
- "juxtaposition" — stating that two or more things are true at the same time, with no
  claim about why, and no claim that one caused the other. Cites two or more references.
  Example: "Inventory days rose while gross margin fell this period." Nothing invented --
  both facts are separately verified; only their co-occurrence is being stated.
- "aggregation" — combining two or more NUMBERS from the cited sources by addition or
  subtraction into one combined figure. Every number you combine must actually appear in
  the cited sources, and the arithmetic must be exactly correct -- this is checked
  mechanically. Never combine numbers that are not the same kind of quantity (never add a
  dollar figure to a percentage, or one currency to another).
- "grouping" — characterizing what TWO OR MORE cited items have in common (a shared theme,
  a shared counterparty type, a shared timeframe), without claiming why, and without adding
  any number that is not already in the cited sources.
- "sourced_causal" — stating that X happened because of Y, ONLY when a cited source's OWN
  text already says so in causal terms. If no cited source states a cause, you have no
  causal sentence available -- write a juxtaposition or restatement instead, or say
  nothing. This type is expected to be used rarely, or not at all in a given brief; that is
  correct, not a shortfall.

Respond with ONLY a JSON object in exactly this shape -- no other text before or after it,
no markdown code fence around it:

{
  "material": true,
  "sentences": [
    {
      "type": "one of: restatement, juxtaposition, aggregation, grouping, sourced_causal",
      "text": "one sentence of plain English",
      "refs": ["obs:1234", "finding:567"]
    }
  ]
}

If nothing in the supplied material amounts to a narrative worth writing, respond with
exactly:

{"material": false, "sentences": []}

That is the correct response whenever the supplied items are routine or repetitive with no
real pattern connecting them -- do not manufacture a sentence just to have one.

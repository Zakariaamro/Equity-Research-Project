"""SPEC-008 R6: Filings page. Composition only. Every filing in the
database -- all of them, not just the analysed window -- with a detail view
that is the audit trail made navigable: any claim on any other page can be
checked here in two clicks."""

from __future__ import annotations

import streamlit as st

from dashboard import components, data, format as fmt
from edgar import section_store


def _render_filing_list(filings: list[dict]) -> str | None:
    if not filings:
        components.empty_state("No filings in the database yet.")
        return None
    labels = [f"{f['ticker']} — {f['form_type']} — {fmt.format_date(f['filing_date'])} — {f['accession_no']}" for f in filings]
    choice = st.selectbox("Filing", options=range(len(filings)), format_func=lambda i: labels[i])
    return filings[choice]["accession_no"]


def _render_detail(accession_no: str) -> None:
    detail = data.get_filing_detail(accession_no)
    filing = detail["filing"]
    if filing is None:
        components.empty_state("Filing not found.")
        return

    sec_link = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={filing['cik']}"
    st.subheader(f"{filing['company_name']} ({filing['ticker']}) — {filing['form_type']}")
    st.write(f"Filed {fmt.format_date(filing['filing_date'])}, period {fmt.format_date(filing['period_end'])} — [View on SEC]({sec_link})")

    st.markdown("### Brief")
    observations = detail["observations"]
    if detail["brief"] is None:
        components.empty_state("No brief exists for this filing.")
    else:
        sentences = detail["brief"]["sentences"]
        components.brief_section(sentences, top_n=6)
        # SPEC-008-batch-4 item 3 (approved 2026-08-16): D10 solved this
        # exact duplication on the Overview page's "What changed?" --
        # reused here, not reinvented. Excludes an observation already
        # cited by a kept brief sentence (a restatement is close to a
        # direct rewording of its one cited observation) from this
        # section, so the same fact isn't listed twice on one page.
        already_in_brief = components.observation_ids_cited_in_brief(sentences)
        observations = [o for o in observations if o["id"] not in already_in_brief]

    st.markdown("### Observations")
    components.observations_section(observations)

    st.markdown("### Findings")
    if not detail["findings"]:
        components.empty_state("No findings for this filing.")
    else:
        for finding in detail["findings"]:
            components.finding_item(finding, show_quote=True)

    st.markdown("### Sections")
    sections = detail["sections"]
    if not sections:
        components.empty_state("This filing has no extracted sections (e.g. an 8-K).")
        return
    section_labels = [f"{s['category']} — {s['short_name']}" for s in sections]
    section_choice = st.selectbox("Section text", options=range(len(sections)), format_func=lambda i: section_labels[i])
    chosen_section = sections[section_choice]
    text = section_store.read_section_text(chosen_section["text_hash"])
    # SPEC-008-batch-4 item 5 (approved 2026-08-16): DISPLAY-TIME cleanup
    # only -- `text` above is exactly what read_section_text returned,
    # unmodified; the stored, content-addressed row is never touched.
    st.text(fmt.clean_section_display_text(text, chosen_section["short_name"]))


def render() -> None:
    st.title("Filings")
    # SPEC-009 Part B (approved 2026-08-25): this used to say "Data as of
    # the current deployment's database" -- a caption that promised a
    # concrete date and never gave one. Removed, not reworded: the real,
    # dated freshness statement now lives in the sidebar
    # (components.data_freshness_caption, called once from app.py), on
    # every page including this one -- a second, page-local restatement
    # here would just be one more place for the wording to drift.
    all_filings = data.get_all_filings()
    st.write(f"{len(all_filings)} filing(s) in the database.")
    accession_no = _render_filing_list(all_filings)
    if accession_no is not None:
        _render_detail(accession_no)

"""Tests for dashboard.pages.filings (SPEC-008-batch-4 item 3, approved
2026-08-16). D10 solved the brief/observations duplication on the
Overview page's "What changed?" by excluding observations already cited
by a kept brief sentence (`components.observation_ids_cited_in_brief`).
The same duplication existed on the Filings page and was never fixed
there -- this reuses the exact same mechanism, not a second one."""

from __future__ import annotations

import json

from streamlit.testing.v1 import AppTest

from dashboard import data
from edgar import db

AMZN_CIK = "0001018724"


def _insert_filing(db_path, accession_no, cik=AMZN_CIK, form_type="10-K", filing_date="2026-02-06",
                    period_end="2025-12-31", fiscal_year=2025, fiscal_period="FY"):
    conn = db.get_connection(db_path)
    conn.execute(
        "INSERT INTO filings (accession_no, cik, form_type, filing_date, period_end, fiscal_year, "
        "fiscal_period, discovered_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sectioned') "
        "ON CONFLICT(accession_no) DO NOTHING",
        (accession_no, cik, form_type, filing_date, period_end, fiscal_year, fiscal_period, f"{filing_date}T00:00:00"),
    )
    conn.commit()
    conn.close()


def _insert_observation(db_path, accession_no, rule_name, statement, severity, subject="subject",
                         cik=AMZN_CIK, period_end="2025-12-31"):
    from edgar import config

    rule_version = config.RULE_REGISTRY[rule_name].version
    conn = db.get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO observations (cik, accession_no, period_end, rule_name, rule_version, subject, "
        "severity, statement, refs_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)",
        (cik, accession_no, period_end, rule_name, rule_version, subject, severity, statement, "2026-01-01T00:00:00"),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def _insert_brief(db_path, accession_no, cik, sentences):
    """`sentences`: list of (sentence_type, text, refs) -- `refs` is a list
    of ref strings, e.g. ["obs:123"], matching data.get_brief_sentences'
    own resolution format."""
    conn = db.get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO briefs (accession_no, cik, prompt_name, prompt_version, verifier_version, model, "
        "input_hash, created_at) VALUES (?, ?, 'filing_brief', 'v1', 'v1', 'claude-sonnet-5', ?, ?)",
        (accession_no, cik, f"hash-{accession_no}", "2026-01-01T00:00:00"),
    )
    brief_id = cur.lastrowid
    for i, (sentence_type, text, refs) in enumerate(sentences):
        conn.execute(
            "INSERT INTO brief_sentences (brief_id, position, sentence_type, text, refs_json) VALUES (?, ?, ?, ?, ?)",
            (brief_id, i, sentence_type, text, json.dumps(refs)),
        )
    conn.commit()
    conn.close()


def test_filings_page_source_reuses_observation_ids_cited_in_brief():
    # The item's own explicit instruction: "Reuse it. Do not write a
    # second mechanism." Grep-style, matching this project's own
    # established architectural-boundary tests (test_dashboard_structure.py)
    # rather than a full page render, for the "reuse, don't reinvent" half
    # of this item -- the underlying primitive itself is already tested in
    # test_dashboard_components.py.
    from pathlib import Path

    text = (Path(__file__).parent.parent / "dashboard" / "pages" / "filings.py").read_text()
    assert "components.observation_ids_cited_in_brief(" in text


def test_filings_page_excludes_observations_already_cited_in_the_brief(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    accession_no = "acc1"
    _insert_filing(db_path, accession_no)
    cited_id = _insert_observation(
        db_path, accession_no, "metric_multi_year_extreme",
        "Gross margin of 52.3% is the highest in 19 quarters.", "high", subject="gross_margin",
    )
    _insert_observation(
        db_path, accession_no, "metric_multi_year_extreme", "A second, uncited observation.", "low",
        subject="other_metric",
    )
    _insert_brief(
        db_path, accession_no, AMZN_CIK,
        [("restatement", "Gross margin reached 52.3%, the highest level in 19 quarters.", [f"obs:{cited_id}"])],
    )

    real_get_filing_detail = data.get_filing_detail
    monkeypatch.setattr(data, "get_filing_detail", lambda accession_no: real_get_filing_detail(accession_no, db_path=db_path))

    def script(accession_no):
        from dashboard.pages import filings

        filings._render_detail(accession_no)

    at = AppTest.from_function(script, kwargs={"accession_no": accession_no})
    at.run()
    assert at.exception == []
    # brief_sentence renders via _safe_markdown; observation_item renders
    # via _safe_write, but st.write's own polymorphic dispatch renders a
    # plain string argument as markdown too -- AppTest has no separate
    # `.write` bucket at all (checked: `st.write` isn't one of AppTest's
    # own element attributes), so both land in `at.markdown`.
    markdowns = [m.value for m in at.markdown]
    assert any("Gross margin reached 52.3%" in m for m in markdowns)
    # The observation the brief sentence CITES must not also appear under
    # "Observations" -- the duplication this item exists to remove.
    assert not any("highest in 19 quarters" in m for m in markdowns)
    # The uncited observation must still be there -- this is an exclusion
    # of the specific cited fact, not a blanket suppression of the section.
    assert any("second, uncited observation" in m for m in markdowns)

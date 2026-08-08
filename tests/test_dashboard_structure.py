"""SPEC-008: architectural boundaries enforced by grep, not just by review.
'Pages contain no SQL' and 'components.py owns every st.session_state key'
are both rules a normal test can't catch (nothing breaks at runtime if a
page sneaks in a query or touches session_state directly) -- so they are
checked here, directly, against the source text."""

from __future__ import annotations

import ast
from pathlib import Path

DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
PAGES_DIR = DASHBOARD_DIR / "pages"

_SQL_MARKERS = ("SELECT ", ".execute(", "import sqlite3", "from sqlite3")

# SPEC-008 review D1: these three functions render model-/DB-generated
# prose (brief sentences, finding quotes, observation statements) -- exactly
# the text that can contain an arbitrary '$', which Streamlit's markdown
# renders as LaTeX math-mode delimiters. They must render ONLY through
# components.py's `_safe_*` wrappers (which always escape), never call
# st.markdown/st.write/st.caption directly -- so a future edit can't forget
# the escape by omission, matching app.py's own "impossible, not merely
# discouraged" argument for its url_path= choice.
_NARRATIVE_RENDER_FUNCTIONS = ("brief_sentence", "observation_item", "finding_item")
_RAW_RENDER_CALLS = ("st.markdown(", "st.write(", "st.caption(")


def _function_source(path: Path, func_name: str) -> str:
    text = path.read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"{func_name!r} not found in {path}")


def _page_files() -> list[Path]:
    return sorted(p for p in PAGES_DIR.glob("*.py") if p.name != "__init__.py")


def test_no_sql_outside_data_module():
    """Pages contain no SQL. They call data.py."""
    offenders = []
    for path in _page_files() + [DASHBOARD_DIR / "components.py"]:
        text = path.read_text()
        for marker in _SQL_MARKERS:
            if marker in text:
                offenders.append(f"{path.name}: contains {marker!r}")
    assert not offenders, "\n".join(offenders)


def test_no_session_state_outside_components():
    """components.py owns every st.session_state key (SPEC-008 v1.1) --
    pages, and app.py, never touch it directly."""
    offenders = []
    for path in _page_files() + [DASHBOARD_DIR / "app.py"]:
        text = path.read_text()
        if "st.session_state" in text:
            offenders.append(path.name)
    assert not offenders, f"st.session_state referenced outside components.py in: {offenders}"


def test_components_module_is_the_only_one_using_session_state():
    """Sanity check on the test above: components.py DOES use it (otherwise
    the previous test would be vacuous)."""
    text = (DASHBOARD_DIR / "components.py").read_text()
    assert "st.session_state" in text


def test_narrative_renderers_use_the_escaping_wrapper():
    """SPEC-008 review, post-D1: brief_sentence/observation_item/finding_item
    must call only `_safe_markdown`/`_safe_write`/`_safe_caption`, never the
    raw st.* functions directly -- otherwise a future edit could silently
    drop the currency-escaping call and neither a unit test on the escaping
    helper nor AppTest (which asserts on values, not rendering) would catch
    it. This test would fail the moment a raw call reappears."""
    path = DASHBOARD_DIR / "components.py"
    offenders = []
    for func_name in _NARRATIVE_RENDER_FUNCTIONS:
        source = _function_source(path, func_name)
        for marker in _RAW_RENDER_CALLS:
            if marker in source:
                offenders.append(f"{func_name}: calls {marker!r} directly instead of a _safe_* wrapper")
    assert not offenders, "\n".join(offenders)

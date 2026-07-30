"""SPEC-008: architectural boundaries enforced by grep, not just by review.
'Pages contain no SQL' and 'components.py owns every st.session_state key'
are both rules a normal test can't catch (nothing breaks at runtime if a
page sneaks in a query or touches session_state directly) -- so they are
checked here, directly, against the source text."""

from __future__ import annotations

from pathlib import Path

DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
PAGES_DIR = DASHBOARD_DIR / "pages"

_SQL_MARKERS = ("SELECT ", ".execute(", "import sqlite3", "from sqlite3")


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

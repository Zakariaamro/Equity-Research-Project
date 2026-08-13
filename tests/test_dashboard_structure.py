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
    it. This test would fail the moment a raw call reappears.

    Deliberately kept alongside test_no_unescaped_currency_reaches_
    streamlit_markdown below, not replaced by it: these three functions
    render ARBITRARY, DB-generated prose (`sentence["text"]` and similar --
    a Subscript, not a string literal or a resolvable module constant), so
    the general check below has nothing to statically resolve there and
    would pass silently even if the wrapper were bypassed. This one
    forbids the raw call outright, by name, regardless of content;
    the general check below covers everything the by-name list can't
    (every other STATIC string in the file, and every other file)."""
    path = DASHBOARD_DIR / "components.py"
    offenders = []
    for func_name in _NARRATIVE_RENDER_FUNCTIONS:
        source = _function_source(path, func_name)
        for marker in _RAW_RENDER_CALLS:
            if marker in source:
                offenders.append(f"{func_name}: calls {marker!r} directly instead of a _safe_* wrapper")
    assert not offenders, "\n".join(offenders)


# SPEC-008-batch-3 item 3 (approved 2026-08-14): the currency/LaTeX bug
# (Streamlit's markdown renders `$...$` as inline LaTeX math mode, D1)
# happened a SECOND time -- in a page file's caption, which the guard
# above never looked at (scoped to three named functions in components.py
# only). "The fix is the guard, not the instance": this check is general
# across BOTH files it needs to be (components.py and every file under
# dashboard/pages/), and general across HOW a `$` can reach a render call
# -- a literal in the call itself, or (the caption bug's exact shape) a
# module-level string constant reached only through a local variable, an
# f-string, or a ternary.
_ESCAPE_FUNCTION_NAME = "escape_markdown_currency"


def _all_name_assignments(tree: ast.Module) -> dict[str, list[ast.expr]]:
    """Every simple `NAME = <expr>` assignment ANYWHERE in the file --
    module level (`_KEY_METRICS_CAPTION`) or inside a function body
    (`unit_caption`, the caption bug's exact shape: a module constant
    reached only through a local variable a few lines later). Every RHS
    collected per name, not just the last -- more than one assignment to
    the same local name (e.g. across branches) is realistic here."""
    assignments: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(node.value)
    return assignments


def _has_unescaped_dollar(s: str) -> bool:
    """A `\\$` is already escaped (SPEC-008 review D1's own fix); a bare
    `$` is not."""
    return any(ch == "$" and (i == 0 or s[i - 1] != "\\") for i, ch in enumerate(s))


def _resolved_strings(expr: ast.expr, assignments: dict[str, list[ast.expr]], _depth: int = 0) -> list[str]:
    """Every string literal `expr` could evaluate to -- walks f-strings
    (JoinedStr's own Constant pieces), ternaries (both branches), and NAME
    references resolved against `_all_name_assignments`, recursively (a
    NAME can itself be assigned from another NAME, as `unit_caption` <-
    `_KEY_METRICS_CAPTION` is here) -- depth-bounded, not because this
    codebase has cycles, but because a generic walker shouldn't assume no
    future file ever will."""
    if _depth > 5:
        return []
    found: list[str] = []
    for node in ast.walk(expr):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
        elif isinstance(node, ast.Name) and node.id in assignments:
            for rhs in assignments[node.id]:
                found.extend(_resolved_strings(rhs, assignments, _depth + 1))
    return found


def _is_streamlit_render_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("markdown", "write", "caption")
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
    )


def _unescaped_currency_offenders(path: Path) -> list[str]:
    """Every raw `st.markdown`/`st.write`/`st.caption` call in `path` whose
    argument resolves to a string literal or local/module-level assignment
    containing an unescaped '$', and which doesn't already route through
    `escape_markdown_currency` at the call site."""
    text = path.read_text()
    tree = ast.parse(text)
    assignments = _all_name_assignments(tree)
    offenders = []
    for node in ast.walk(tree):
        if not _is_streamlit_render_call(node) or not node.args:
            continue
        call_source = ast.get_source_segment(text, node) or ""
        if f"{_ESCAPE_FUNCTION_NAME}(" in call_source:
            continue  # escaped inline at this call site
        for s in _resolved_strings(node.args[0], assignments):
            if _has_unescaped_dollar(s):
                offenders.append(f"{path.name}:{node.lineno}: {s!r}")
                break
    return offenders


def test_no_unescaped_currency_reaches_streamlit_markdown():
    """SPEC-008-batch-3 item 3 (approved 2026-08-14). General across
    components.py AND dashboard/pages/*.py, and across every way a
    resolvable string can reach a render call -- see the module comment
    and helpers immediately above. Confirmed to actually catch the
    caption bug before it was fixed: run against the pre-fix source, this
    test failed with `financials.py:<line>: 'EPS in $ (2dp), ...'` listed
    -- not assumed to work, verified red before it was made green."""
    offenders = []
    for path in [DASHBOARD_DIR / "components.py", *_page_files()]:
        offenders.extend(_unescaped_currency_offenders(path))
    assert not offenders, "\n".join(offenders)

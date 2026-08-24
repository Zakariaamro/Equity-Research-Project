"""Found live, first real GitHub Actions run of SPEC-009 Part A's scheduled-
ingestion workflow: `tests/test_validate.py`'s own SQL contained a Python
numeric-underscore literal (`999_000_000`) written directly into a raw SQL
string. Python accepts numeric underscores in any int/float literal;
SQLite's own parser only accepts them from 3.46 (2024). macOS ships a newer
SQLite than the GitHub Actions runner used, so this passed locally and
failed in CI -- exactly the class of environment gap SPEC-009 Part B's
"fresh clone, fresh install" acceptance criterion exists to catch, proven
non-hypothetical by this incident (see ARCHITECTURE.md's decision log).

Same SHAPE as the currency-escaping guard in test_dashboard_structure.py,
not the same rule: scan every .py file for string literals reaching one
specific dangerous context, and fail if a bad pattern is present, rather
than trying to enumerate every place SQL text might get built by hand.
Unlike the currency guard, this one genuinely needs to know WHICH calls are
dangerous -- but that's a closed, complete, stdlib-defined set
(`sqlite3.Cursor.execute`/`executemany`/`executescript`, the entire raw-SQL-
execution surface of the `sqlite3` module this project uses everywhere),
not an open-ended, ever-growing UI surface the way Streamlit's markdown
rendering is -- so naming these three by attribute is not the same
fragility the currency guard moved away from.

Python-level numeric underscores stay allowed EVERYWHERE else in this
codebase (checked: dozens of real, legitimate uses -- LLM_MAX_INPUT_TOKENS_
ESTIMATE, /1_000_000 scale divisors, test fixture dollar amounts). Only a
literal that becomes part of the SQL TEXT itself is a problem: a Python int
like `999_000_000` bound as a query PARAMETER (`conn.execute("... = ?",
(999_000_000,))`) has its underscore stripped at Python parse time and
never reaches SQLite as text at all -- confirmed this project's own
convention is exclusively '?' placeholders bound as parameters, never
f-string-interpolated values, so this distinction is real and load-bearing,
not just theoretical."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
_EXCLUDED_DIR_NAMES = {".venv", "venv", ".git", "__pycache__", ".pytest_cache", ".claude", "node_modules"}

_EXECUTE_METHOD_NAMES = ("execute", "executemany", "executescript")

# A digit immediately next to an underscore, itself next to a digit -- the
# exact shape of a Python numeric-underscore literal (999_000_000, 1_5,
# 0x_FF is a different case not used anywhere in this codebase's SQL).
# Deliberately narrow: this is NOT "any digit near an underscore" checked
# repo-wide (that would flag hundreds of real, legitimate Python literals
# like LLM_MAX_INPUT_TOKENS_ESTIMATE = 150_000) -- it is applied ONLY to
# strings already identified as reaching a .execute()-family call's SQL
# text argument, below.
_NUMERIC_UNDERSCORE_RE = re.compile(r"\d_\d")


def _project_py_files() -> list[Path]:
    return [
        p for p in ROOT.rglob("*.py")
        if not any(part in _EXCLUDED_DIR_NAMES or part.endswith(".egg-info") for part in p.parts)
    ]


def _all_name_assignments(tree: ast.Module) -> dict[str, list[ast.expr]]:
    """Every simple `NAME = <expr>` assignment anywhere in the file --
    module level or inside a function body, matching the exact shape SQL
    text is actually built in this codebase (an f-string assigned to a
    `query` variable a few lines above `conn.execute(query, params)`,
    confirmed live in brief.py/analyze.py/fetch.py/sections.py/
    dashboard/data.py before writing this -- a direct-literal-only check
    would have missed most real call sites)."""
    assignments: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(node.value)
    return assignments


def _resolved_strings(expr: ast.expr, assignments: dict[str, list[ast.expr]], _depth: int = 0) -> list[str]:
    """Every string literal `expr` could evaluate to -- walks f-strings
    (JoinedStr's own Constant pieces) and NAME references resolved against
    `_all_name_assignments`, recursively, depth-bounded defensively."""
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


def _strip_sql_line_comments(sql: str) -> str:
    """Everything from a `--` to end of line is a SQL comment, stripped by
    SQLite before tokenization -- never reaches its numeric parser.
    Line-based, not a full SQL lexer: found live, applying the pattern
    below to db.py's own schema-creation script BEFORE this existed, a
    genuine false positive turned up -- a comment referencing
    "scripts/backfill_2026_07_28_..." (a real filename, containing
    digit_underscore_digit purely by coincidence of its own date-stamped
    naming convention) inside a `--` comment explaining an unrelated past
    incident. Stripping comments first removes exactly that class of
    false positive without weakening the real check: a numeric-underscore
    literal actually written INTO live SQL, even on a line that also
    carries a trailing comment, is still caught, since only text from the
    first `--` onward on each line is removed."""
    return "\n".join(line.split("--", 1)[0] for line in sql.split("\n"))


def _is_execute_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _EXECUTE_METHOD_NAMES
    )


def _sql_numeric_underscore_offenders(path: Path) -> list[str]:
    text = path.read_text()
    tree = ast.parse(text)
    assignments = _all_name_assignments(tree)
    offenders = []
    for node in ast.walk(tree):
        if not _is_execute_call(node) or not node.args:
            continue
        for s in _resolved_strings(node.args[0], assignments):
            if _NUMERIC_UNDERSCORE_RE.search(_strip_sql_line_comments(s)):
                # relative_to ROOT for a real project file; falls back to
                # the bare path for a test's own tempfile fixture, which
                # by construction sits outside ROOT.
                display_path = path.relative_to(ROOT) if ROOT in path.parents else path
                offenders.append(f"{display_path}:{node.lineno}: {s!r}")
                break
    return offenders


def test_no_numeric_underscore_literal_reaches_sql_text():
    """General across the whole project, not just the one file that broke
    -- SQL is built in edgar/, dashboard/, and tests/ alike. Confirmed to
    actually catch the real incident before it was fixed: run against
    tests/test_validate.py before the 999_000_000 -> 999000000 fix, this
    test failed with exactly that line listed -- not assumed to work,
    verified red before it was made green."""
    offenders = []
    for path in _project_py_files():
        offenders.extend(_sql_numeric_underscore_offenders(path))
    assert not offenders, "\n".join(offenders)


def test_python_level_numeric_underscores_are_unaffected():
    # The guard above must not become a blanket "no digit_digit anywhere"
    # rule -- confirmed here that a real, legitimate Python-level literal
    # bound as a query PARAMETER (never reaching SQL text) is untouched.
    import tempfile

    source = (
        "import sqlite3\n"
        "conn = sqlite3.connect(':memory:')\n"
        "conn.execute('CREATE TABLE t (value INTEGER)')\n"
        "conn.execute('INSERT INTO t (value) VALUES (?)', (999_000_000,))\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        temp_path = Path(f.name)
    try:
        assert _sql_numeric_underscore_offenders(temp_path) == []
    finally:
        temp_path.unlink()


def test_numeric_underscore_inside_a_sql_comment_is_not_flagged():
    # The false positive found live while building this guard: edgar/db.py's
    # own schema script has a `--` comment referencing a real, date-stamped
    # filename ("scripts/backfill_2026_07_28_...") that happens to contain
    # digit_underscore_digit purely by coincidence -- never reaches
    # SQLite's numeric parser at all, since SQL strips `--` comments before
    # tokenizing. A genuine dangerous literal on the SAME line, before the
    # comment starts, must still be caught.
    import tempfile

    source = (
        "import sqlite3\n"
        "conn = sqlite3.connect(':memory:')\n"
        "conn.executescript('''\n"
        "CREATE TABLE t (value INTEGER);\n"
        "-- see scripts/backfill_2026_07_28_gap.py for context\n"
        "''')\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        temp_path = Path(f.name)
    try:
        assert _sql_numeric_underscore_offenders(temp_path) == []
    finally:
        temp_path.unlink()

    dangerous_source = (
        "import sqlite3\n"
        "conn = sqlite3.connect(':memory:')\n"
        "conn.execute('UPDATE t SET value = 999_000_000 -- see scripts/backfill_2026_07_28_gap.py\\n')\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(dangerous_source)
        temp_path = Path(f.name)
    try:
        assert _sql_numeric_underscore_offenders(temp_path) != []  # still caught, before the comment
    finally:
        temp_path.unlink()

"""Guards for two things that rot silently: the docs, and the import layering.

Both failures found by audit rather than by anything automatic:

- ``docs/ARCHITECTURE.md`` described ``core/tool_gate.py`` as a live component
  long after it was deleted. Prose does not get type-checked, so a module can be
  removed and its documentation left describing the system as it used to be.
- Every doc states that ``tools/`` must not import ``ui.*`` — the tool modules
  are the product and the dashboard is a consumer of them. It held, but only by
  habit; nothing failed if you wrote the import.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Runtime state, not source: created on demand, gitignored, and correctly absent
# from a clean checkout. Referencing one in prose is not drift.
_RUNTIME_PREFIXES = ("data/", "extensions/", "ui/static/dist/", "ui/web/node_modules/")


def _doc_files() -> list[pathlib.Path]:
    docs = sorted((ROOT / "docs").glob("*.md"))
    for extra in ("README.md", "CLAUDE.md"):
        p = ROOT / extra
        if p.exists():
            docs.append(p)
    return docs


# A path-shaped reference inside backticks: `core/foo.py`, `ui/api/bar.py`.
# Requires a directory component, so bare prose like `prompts.py` is not treated
# as a path claim — those are ambiguous by design in narrative text.
_PATH_RE = re.compile(r'`((?:[\w.-]+/)+[\w.-]+\.(?:py|ts|tsx))`')


def _referenced_paths(text: str) -> set[str]:
    return {m for m in _PATH_RE.findall(text) if not m.startswith(_RUNTIME_PREFIXES)}


@pytest.mark.parametrize("doc", _doc_files(), ids=lambda p: p.name)
def test_docs_do_not_reference_source_files_that_are_gone(doc):
    """A doc naming `core/x.py` has to be naming a file that exists.

    Two kinds of doc are exempt, both because they describe a moment rather than
    the current system: CHANGELOG entries must stay free to name the file a
    commit deleted, and a past audit's recommendations name files it thought
    should be *created*, which is not a claim that they exist.
    """
    if doc.name in {"CHANGELOG.md", "AGENT_AUDIT.md"}:
        pytest.skip(f"{doc.name} records a past moment, not the current system")

    missing = sorted(p for p in _referenced_paths(doc.read_text(encoding="utf-8"))
                     if not (ROOT / p).exists())
    assert not missing, (
        f"{doc.name} references source files that do not exist: {missing}. "
        "Either the file moved and the doc needs updating, or it was deleted and "
        "the doc still describes it as part of the system.")


def test_the_architecture_module_map_names_only_real_modules():
    """The map lists bare filenames in a code fence, not backticked paths, so the
    check above cannot see it — and that is exactly where the stale
    ``tool_gate.py`` entry survived. Resolve each name against the packages the
    map actually describes.
    """
    doc = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "## 2. Module map" in doc, "the map moved — update this guard"
    fence = doc.split("## 2. Module map")[1].split("```")[1]

    named = set(re.findall(r"([a-z_][a-z0-9_]*\.py)", fence))
    real = set()
    for pkg in (".", "core", "tools", "tests"):
        real |= {p.name for p in (ROOT / pkg).glob("*.py")}
    real |= {p.name for p in (ROOT / "ui").rglob("*.py")}

    ghosts = sorted(named - real)
    assert not ghosts, (
        f"docs/ARCHITECTURE.md's module map names modules that do not exist: "
        f"{ghosts}. A deleted module left in the map describes a system that is "
        "no longer there.")


def test_the_architecture_map_covers_every_core_module():
    """core/ is where the load-bearing logic lives; a module missing from the map
    is a subsystem a cold reader has no way to discover."""
    doc = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    fence = doc.split("## 2. Module map")[1].split("```")[1]
    named = set(re.findall(r"([a-z_][a-z0-9_]*\.py)", fence))

    real = {p.name for p in (ROOT / "core").glob("*.py")} - {"__init__.py"}
    undocumented = sorted(real - named)
    assert not undocumented, (
        f"{len(undocumented)} core/ modules are absent from the module map: "
        f"{undocumented}. Add them under the group they belong to.")


def _imports(path: pathlib.Path) -> set[str]:
    """Top-level module names imported by a file, including inside functions."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


TOOL_MODULES = sorted((ROOT / "tools").glob("*.py"))


@pytest.mark.parametrize("mod", TOOL_MODULES, ids=lambda p: p.name)
def test_tools_never_import_the_ui_layer(mod):
    """``tools/`` is the product; ``ui/`` is one of its consumers.

    The dependency only runs one way. A tool reaching into ``ui.runtime`` for,
    say, the MCP endpoint drags the whole dashboard — FastAPI, the health cache,
    the scheduler — into the MCP process's import graph, and makes the tool
    untestable without standing the UI up. Anything shared belongs in ``core/``.

    Deferred (function-level) imports count: they are the tempting way to do it
    and they couple the two layers just as firmly at run time.
    """
    offenders = sorted(m for m in _imports(mod) if m == "ui" or m.startswith("ui."))
    assert not offenders, (
        f"tools/{mod.name} imports {offenders}. Move what it needs into core/ — "
        "tools must not depend on the dashboard.")


def test_the_layering_guard_would_actually_catch_a_violation(tmp_path):
    """A guard that cannot fail is not a guard."""
    bad = tmp_path / "bad_tool.py"
    bad.write_text("def f():\n    from ui.runtime import thing\n    return thing\n",
                   encoding="utf-8")
    assert "ui.runtime" in _imports(bad)


def test_the_doc_guard_would_actually_catch_a_missing_file():
    assert _referenced_paths("see `core/does_not_exist.py` for details") == \
        {"core/does_not_exist.py"}
    # …and does not fire on runtime state or on bare filenames in prose.
    assert _referenced_paths("`data/agent_config.json`") == set()
    assert _referenced_paths("the `prompts.py` module") == set()

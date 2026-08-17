# top-pypi-dependents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank PyPI projects by how many other projects depend on them, refreshed monthly, published as JSON, a GitHub Pages site, and a DuckDB graph database.

**Architecture:** Four independent CLI stages — `extract` pulls one winner release per project from BigQuery, `build` loads it into DuckDB and explodes dependency edges, `artifacts` emits ranked JSON with month-over-month deltas, `render` emits a static site. A `MetadataSource` protocol lets everything except the live BigQuery call run against a checked-in fixture, so the whole pipeline is testable before GCP credentials exist.

**Tech Stack:** Python 3.14, uv, DuckDB, PyArrow, `packaging`, Jinja2, httpx, google-cloud-bigquery, ruff, ty, pytest, prek.

**Spec:** `docs/superpowers/specs/2026-08-16-top-pypi-dependents-design.md`

## Global Constraints

- Python 3.14. `requires-python = ">=3.14"`. `.python-version` contains `3.14`.
- Build backend `uv_build`, src layout, package name `top_pypi_dependents`, distribution name `top-pypi-dependents`.
- Repo-only tooling. **No PyPI publish workflow, no classifiers, no CHANGELOG, no version bumping.**
- License MIT. Author `Mike Fiedler <miketheman@gmail.com>`.
- Dependencies are added with `uv add` / `uv add --group <name>`, never by hand-editing `pyproject.toml`.
- Every GitHub Action is pinned to a full commit SHA with a `# vX.Y.Z` trailing comment.
- Tests never touch the network. `pytest-socket` is enabled globally via `addopts = ["--disable-socket"]`.
- Every project name is canonicalized with `packaging.utils.canonicalize_name` before any comparison, join, or grouping. The source table stores `Django`, not `django`.
- The headline count is `COUNT(DISTINCT dependent)`, not an edge count.
- `dependents` (ranked) counts only edges with no `extra ==` clause in their marker. `dependents_all` counts every edge.
- "Latest" means the highest final release by PEP 440, falling back to the highest prerelease when a project has only prereleases.
- Ranking ties break by canonical name ascending, so ranks are stable across runs.
- Only projects live on PyPI are ranked, **and only live projects count as dependents**. `distribution_metadata` never removes deleted projects, so without this filter deleted projects accumulate forever and silently inflate every count.
- Templates and SQL files live inside the package (`src/top_pypi_dependents/templates/`, `.../sql/`), not at the repo root as the spec's layout sketch shows, so the CLI works from an installed wheel rather than only from a checkout.
- `render` reads the JSON artifact, not the database. The artifact already carries the computed rank deltas, and recomputing them after `artifacts` has overwritten `data/latest.json` would yield all-zero movement.
- Test fixtures are JSONL, not Parquet — the corpus is tiny and text stays reviewable in diffs.
- `google-cloud-bigquery` is imported lazily, inside `BigQuerySource`, so CI can run the full test suite without installing it.

---

### Task 1: Repo scaffold with green lint, types, and tests

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.editorconfig`, `LICENSE`, `Makefile`, `.pre-commit-config.yaml`
- Create: `.github/workflows/ci.yml`, `.github/workflows/zizmor.yml`, `.github/dependabot.yml`
- Create: `src/top_pypi_dependents/__init__.py`
- Test: `tests/test_package.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `top_pypi_dependents.__version__: str`. A working `make lint`, `make test`, `make format`.

- [ ] **Step 1: Initialize the project with uv**

```bash
cd /Users/miketheman/workspace/miketheman/top-pypi-dependents
uv init --package --name top-pypi-dependents --python 3.14
```

This creates `pyproject.toml`, `src/top_pypi_dependents/__init__.py`, `.python-version`, and `README.md`. Delete the generated `src/top_pypi_dependents/py.typed` placeholder only if `uv` created one you do not want; keep everything else.

- [ ] **Step 2: Add dependencies**

```bash
uv add duckdb packaging pyarrow jinja2 httpx
uv add --group dev pytest pytest-socket 'coverage[toml]' ruff ty
uv add --group bigquery google-cloud-bigquery google-cloud-bigquery-storage
```

The `bigquery` group is deliberately separate: CI never installs it, which is what forces the lazy import in Task 9.

- [ ] **Step 3: Write the tool configuration into `pyproject.toml`**

Append these tables. Do not touch the `[project]` or `[dependency-groups]` tables that `uv` manages.

```toml
[project.scripts]
top-pypi-dependents = "top_pypi_dependents.cli:main"

[tool.ruff.lint]
select = ["ALL"]
ignore = [
    "COM812",  # conflicts with the formatter
    "D",       # docstring style; not enforced on a tooling repo
    "ISC001",  # conflicts with the formatter
]

[tool.ruff.lint.per-file-ignores]
"tests/*" = [
    "PLR2004",  # magic values are the point of a test
    "S101",     # assert is how pytest works
]

[tool.ty.environment]
python-version = "3.14"

[tool.ty.terminal]
error-on-warning = true

[tool.pytest]
addopts = ["--disable-socket"]

[tool.coverage.run]
branch = true
source = ["src/top_pypi_dependents", "tests"]

[tool.coverage.report]
show_missing = true

[build-system]
requires = ["uv_build>=0.7.20,<0.12.0"]
build-backend = "uv_build"
```

- [ ] **Step 4: Write `.editorconfig`**

```ini
# http://editorconfig.org

root = true

[*]
indent_style = space
insert_final_newline = true
trim_trailing_whitespace = true
end_of_line = lf
charset = utf-8

[*.py]
indent_size = 4

[*.{yml,yaml,json,toml}]
indent_size = 2

[*.j2]
indent_size = 2

[Makefile]
indent_style = tab
```

- [ ] **Step 5: Write `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
build/
dist/
site/
*.egg-info/
.coverage
.pytest_cache/
.ruff_cache/
.install.stamp
```

`build/` and `site/` are pipeline scratch directories — they hold the DuckDB file, the extracted Parquet, and the rendered HTML, none of which belong in git. `data/` is deliberately absent from this list: `data/latest.json` is the one committed artifact.

- [ ] **Step 6: Write `LICENSE`**

MIT, copyright `2026 Mike Fiedler`. Use the standard MIT text verbatim.

- [ ] **Step 7: Write the `Makefile`**

```makefile
.PHONY: all clean install lint format test hooks

INSTALL_STAMP := .install.stamp
UV := $(shell command -v uv 2> /dev/null)

all: lint test

clean:
	@rm -rf $(INSTALL_STAMP) .coverage .pytest_cache/ .ruff_cache/ build/ site/ .venv/

install: $(INSTALL_STAMP)
$(INSTALL_STAMP): pyproject.toml uv.lock
ifndef UV
	$(error "uv is not available, please install it first.")
endif
	@uv sync
	@touch $(INSTALL_STAMP)

lint: $(INSTALL_STAMP)
	@uv run ruff format --check
	@uv run ruff check
	@uv run ty check src tests

format: $(INSTALL_STAMP)
	@uv run ruff format
	@uv run ruff check --fix

test: $(INSTALL_STAMP)
	@uv run coverage run -m pytest ; uv run coverage report

hooks:
	@prek run --all-files
```

- [ ] **Step 8: Write `.pre-commit-config.yaml`**

```yaml
# See https://pre-commit.com for more information. Run with prek.
repos:
  - repo: builtin
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-merge-conflict
      - id: check-added-large-files
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.16.3
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/astral-sh/ty-pre-commit
    rev: v0.0.72
    hooks:
      - id: ty
  - repo: https://github.com/editorconfig-checker/editorconfig-checker.python
    rev: 3.11.1
    hooks:
      - id: editorconfig-checker
        alias: ec
  - repo: https://github.com/zizmorcore/zizmor-pre-commit
    rev: v1.29.0
    hooks:
      - id: zizmor
```

`check-added-large-files` matters here: it is the guard that stops a stray `build/dependents.duckdb` from being committed.

- [ ] **Step 9: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

permissions: {}

jobs:
  test:
    name: Lint, type-check, test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          persist-credentials: false
      - name: Install uv
        uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2
        with:
          enable-cache: true
          prune-cache: false
      - name: Set up Python
        run: uv python install 3.14
      - name: Sync
        run: uv sync
      - name: Format check
        run: uv run ruff format --check
      - name: Lint
        run: uv run ruff check
      - name: Type check
        run: uv run ty check src tests
      - name: Test
        run: |
          uv run coverage run -m pytest
          uv run coverage report
```

Note `uv sync` without `--group bigquery`. If the suite passes here, the lazy import in Task 9 is working.

- [ ] **Step 10: Write `.github/workflows/zizmor.yml`**

```yaml
name: Zizmor

on:
  push:
    branches: [main]
  pull_request:

permissions: {}

jobs:
  zizmor:
    name: Audit workflows
    runs-on: ubuntu-latest
    permissions:
      security-events: write
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          persist-credentials: false
      - uses: zizmorcore/zizmor-action@3dc1ecc9bcb9e94e9b2c709687979e1298497054 # v0.6.2
```

- [ ] **Step 11: Write `.github/dependabot.yml`**

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 7

  - package-ecosystem: "uv"
    directory: "/"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 7
      semver-major-days: 30
```

- [ ] **Step 12: Write the failing test**

Create `tests/test_package.py`:

```python
import top_pypi_dependents


def test_package_exposes_a_version() -> None:
    assert isinstance(top_pypi_dependents.__version__, str)
    assert top_pypi_dependents.__version__
```

- [ ] **Step 13: Run it to verify it fails**

Run: `uv run pytest tests/test_package.py -v`
Expected: FAIL with `AttributeError: module 'top_pypi_dependents' has no attribute '__version__'`

- [ ] **Step 14: Implement**

Replace `src/top_pypi_dependents/__init__.py` with:

```python
"""Rank PyPI projects by how many other projects depend on them."""

from importlib.metadata import version

__version__ = version("top-pypi-dependents")
```

- [ ] **Step 15: Run the full gate**

Run: `make lint test`
Expected: ruff format clean, ruff check clean, ty clean, 1 test passing.

Fix any ruff `ALL` complaints now rather than adding blanket ignores. If a rule is genuinely wrong for this repo, add it to the `ignore` list in `pyproject.toml` with a trailing comment explaining why.

- [ ] **Step 16: Commit**

```bash
git add -A
git commit -m "Scaffold project with uv, ruff, ty, prek, and CI"
```

---

### Task 2: Name canonicalization and requirement parsing

**Files:**
- Create: `src/top_pypi_dependents/normalize.py`
- Test: `tests/test_normalize.py`

**Interfaces:**
- Consumes: `packaging.utils.canonicalize_name`, `packaging.requirements.Requirement`.
- Produces:
  - `canonical(name: str) -> str`
  - `ParsedRequirement` frozen dataclass with fields `dependency: str`, `dependency_raw: str`, `specifier: str`, `extra: str | None`, `marker: str | None`, `is_runtime: bool`
  - `parse_requirement(text: str) -> ParsedRequirement | None` — returns `None` for unparseable input
  - `gates(marker_text: str | None) -> tuple[str, ...]` — the `extra == "..."` names in a marker, sorted

- [ ] **Step 1: Write the failing tests**

Create `tests/test_normalize.py`:

```python
import pytest

from top_pypi_dependents.normalize import canonical, gates, parse_requirement


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Django", "django"),
        ("zope.interface", "zope-interface"),
        ("ruamel_yaml", "ruamel-yaml"),
        ("Flask-SQLAlchemy", "flask-sqlalchemy"),
        ("a__b..c--d", "a-b-c-d"),
    ],
)
def test_canonical_normalizes_case_and_separators(raw: str, expected: str) -> None:
    assert canonical(raw) == expected


def test_parse_plain_requirement() -> None:
    parsed = parse_requirement("requests")
    assert parsed is not None
    assert parsed.dependency == "requests"
    assert parsed.dependency_raw == "requests"
    assert parsed.specifier == ""
    assert parsed.extra is None
    assert parsed.marker is None
    assert parsed.is_runtime is True


def test_parse_requirement_with_specifier_and_case() -> None:
    parsed = parse_requirement("Zope.Interface (>=5.0,<6)")
    assert parsed is not None
    assert parsed.dependency == "zope-interface"
    assert parsed.dependency_raw == "Zope.Interface"
    assert parsed.specifier == "<6,>=5.0"
    assert parsed.is_runtime is True


def test_extras_gated_requirement_is_not_runtime() -> None:
    parsed = parse_requirement('pytest>=7 ; extra == "test"')
    assert parsed is not None
    assert parsed.dependency == "pytest"
    assert parsed.extra == "test"
    assert parsed.is_runtime is False


def test_multiple_extras_gates_are_joined_and_sorted() -> None:
    parsed = parse_requirement('pytest ; extra == "test" or extra == "dev"')
    assert parsed is not None
    assert parsed.extra == "dev,test"
    assert parsed.is_runtime is False


def test_non_extra_marker_stays_runtime() -> None:
    parsed = parse_requirement('tomli ; python_version < "3.11"')
    assert parsed is not None
    assert parsed.extra is None
    assert parsed.marker == 'python_version < "3.11"'
    assert parsed.is_runtime is True


def test_unparseable_requirement_returns_none() -> None:
    assert parse_requirement("this is not (((a requirement") is None
    assert parse_requirement("") is None


def test_gates_reads_extras_out_of_a_marker() -> None:
    assert gates('extra == "test"') == ("test",)
    assert gates('extra == "b" or extra == "a"') == ("a", "b")
    assert gates('python_version < "3.11"') == ()
    assert gates(None) == ()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'top_pypi_dependents.normalize'`

- [ ] **Step 3: Implement**

Create `src/top_pypi_dependents/normalize.py`:

```python
"""PEP 503 name normalization and PEP 508 requirement parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

_EXTRA_GATE = re.compile(r"""extra\s*==\s*(?P<q>["'])(?P<name>[^"']+)(?P=q)""")


def canonical(name: str) -> str:
    """Normalize a project name per PEP 503."""
    return str(canonicalize_name(name))


def gates(marker_text: str | None) -> tuple[str, ...]:
    """Return the sorted, deduplicated ``extra == "..."`` names in a marker."""
    if not marker_text:
        return ()
    return tuple(sorted({m.group("name") for m in _EXTRA_GATE.finditer(marker_text)}))


@dataclass(frozen=True, slots=True)
class ParsedRequirement:
    """One declared dependency edge, decomposed."""

    dependency: str
    dependency_raw: str
    specifier: str
    extra: str | None
    marker: str | None
    is_runtime: bool


def parse_requirement(text: str) -> ParsedRequirement | None:
    """Parse one ``requires_dist`` entry, or return ``None`` if it is malformed."""
    try:
        req = Requirement(text)
    except InvalidRequirement:
        return None

    marker = str(req.marker) if req.marker is not None else None
    found = gates(marker)
    return ParsedRequirement(
        dependency=canonical(req.name),
        dependency_raw=req.name,
        specifier=str(req.specifier),
        extra=",".join(found) if found else None,
        marker=marker,
        is_runtime=not found,
    )
```

The extras gate is read off `str(marker)` with a regex rather than by walking `Marker._markers`, because that attribute is private and has changed shape across `packaging` releases.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_normalize.py -v`
Expected: all PASS.

If `test_parse_requirement_with_specifier_and_case` fails on the `specifier` string, print the actual value and update the expectation — `SpecifierSet.__str__` sorts its clauses, and the sort order is what the assertion must match.

- [ ] **Step 5: Commit**

```bash
git add src/top_pypi_dependents/normalize.py tests/test_normalize.py
git commit -m "Add name canonicalization and requirement parsing"
```

---

### Task 3: PEP 440 version selection and the SQL audit oracle

**Files:**
- Create: `src/top_pypi_dependents/versions.py`
- Test: `tests/test_versions.py`

**Interfaces:**
- Consumes: `packaging.version.Version`, `packaging.version.InvalidVersion`.
- Produces:
  - `select_latest(versions: Iterable[str]) -> str | None`
  - `Disagreement` frozen dataclass with fields `project: str`, `sql_pick: str | None`, `packaging_pick: str | None`
  - `audit(sample: Mapping[str, Sequence[str]], sql_picks: Mapping[str, str]) -> list[Disagreement]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_versions.py`:

```python
from top_pypi_dependents.versions import Disagreement, audit, select_latest


def test_picks_highest_final_release() -> None:
    assert select_latest(["1.0", "1.10", "1.9", "2.0"]) == "2.0"


def test_numeric_not_lexicographic_ordering() -> None:
    assert select_latest(["1.9.0", "1.10.0"]) == "1.10.0"


def test_final_beats_a_higher_prerelease() -> None:
    assert select_latest(["1.9.9", "2.0.0rc1", "2.0.0b2"]) == "1.9.9"


def test_prerelease_only_project_falls_back_to_highest_prerelease() -> None:
    assert select_latest(["0.1.0a1", "0.1.0rc1", "0.1.0b1"]) == "0.1.0rc1"


def test_post_release_beats_its_base() -> None:
    assert select_latest(["1.0", "1.0.post1"]) == "1.0.post1"


def test_dev_release_loses_to_final() -> None:
    assert select_latest(["1.0", "1.1.dev3"]) == "1.0"


def test_epoch_dominates_release_number() -> None:
    assert select_latest(["99.0", "1!0.1"]) == "1!0.1"


def test_equivalent_padded_releases_do_not_crash() -> None:
    assert select_latest(["1.2", "1.2.0"]) in {"1.2", "1.2.0"}


def test_unparseable_versions_are_ignored() -> None:
    assert select_latest(["not-a-version", "1.0"]) == "1.0"


def test_all_unparseable_returns_none() -> None:
    assert select_latest(["not-a-version", "???"]) is None


def test_empty_returns_none() -> None:
    assert select_latest([]) is None


def test_audit_reports_nothing_when_sql_agrees() -> None:
    sample = {"alpha": ["1.0", "2.0"], "beta": ["0.1", "0.2"]}
    assert audit(sample, {"alpha": "2.0", "beta": "0.2"}) == []


def test_audit_reports_a_disagreement() -> None:
    sample = {"alpha": ["1.9.9", "2.0.0rc1"]}
    assert audit(sample, {"alpha": "2.0.0rc1"}) == [
        Disagreement(project="alpha", sql_pick="2.0.0rc1", packaging_pick="1.9.9")
    ]


def test_audit_reports_a_project_sql_omitted() -> None:
    sample = {"alpha": ["1.0"]}
    assert audit(sample, {}) == [
        Disagreement(project="alpha", sql_pick=None, packaging_pick="1.0")
    ]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_versions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'top_pypi_dependents.versions'`

- [ ] **Step 3: Implement**

Create `src/top_pypi_dependents/versions.py`:

```python
"""Latest-release selection, and the oracle that keeps the SQL honest."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version


def select_latest(versions: Iterable[str]) -> str | None:
    """Return the highest final release, or the highest prerelease if that is all there is.

    Mirrors Warehouse's ``ORDER BY is_prerelease ASC, _pypi_ordering DESC``.
    Versions that ``packaging`` cannot parse are skipped.
    """
    parsed: list[tuple[Version, str]] = []
    for raw in versions:
        try:
            parsed.append((Version(raw), raw))
        except InvalidVersion:
            continue

    if not parsed:
        return None

    finals = [pair for pair in parsed if not pair[0].is_prerelease]
    pool = finals or parsed
    return max(pool, key=lambda pair: pair[0])[1]


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One project where the SQL sort key and ``packaging`` chose differently."""

    project: str
    sql_pick: str | None
    packaging_pick: str | None


def audit(
    sample: Mapping[str, Sequence[str]],
    sql_picks: Mapping[str, str],
) -> list[Disagreement]:
    """Compare the extract query's winners against ``packaging`` over a sample.

    ``sample`` maps a canonical project name to every version it has published.
    ``sql_picks`` maps a canonical project name to the version the query chose.
    """
    found: list[Disagreement] = []
    for project, all_versions in sorted(sample.items()):
        expected = select_latest(all_versions)
        actual = sql_picks.get(project)
        if actual != expected:
            found.append(
                Disagreement(project=project, sql_pick=actual, packaging_pick=expected)
            )
    return found
```

`Version.is_prerelease` is True for alpha, beta, rc, and dev releases and False for post releases, which is exactly the split the spec calls for.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_versions.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/top_pypi_dependents/versions.py tests/test_versions.py
git commit -m "Add PEP 440 latest-release selection and audit oracle"
```

---

### Task 4: Metadata source protocol and the fixture corpus

**Files:**
- Create: `src/top_pypi_dependents/sources/__init__.py`
- Create: `src/top_pypi_dependents/sources/base.py`
- Create: `src/top_pypi_dependents/sources/fixture.py`
- Create: `tests/fixtures/winners.jsonl`, `tests/fixtures/audit_sample.jsonl`, `tests/fixtures/live_names.txt`
- Test: `tests/test_fixture_source.py`

**Interfaces:**
- Consumes: `top_pypi_dependents.normalize.canonical`.
- Produces:
  - `Winner` frozen dataclass: `name: str`, `canonical_name: str`, `version: str`, `upload_time: datetime`, `requires_dist: tuple[str, ...]`, `summary: str`, `requires_python: str`
  - `MetadataSource` protocol with `name: str`, `winners() -> list[Winner]`, `audit_sample() -> dict[str, list[str]]`, `live_names() -> set[str]`
  - `FixtureSource(directory: Path)` implementing it

- [ ] **Step 1: Write the fixture corpus**

Create `tests/fixtures/winners.jsonl`. One JSON object per line. Every field is present on every row.

```jsonl
{"name": "requests", "version": "2.34.2", "upload_time": "2026-05-14T19:25:27Z", "requires_dist": ["urllib3>=1.21.1", "certifi>=2017.4.17", "pysocks>=1.5.6 ; extra == \"socks\""], "summary": "Python HTTP for Humans.", "requires_python": ">=3.9"}
{"name": "Django", "version": "6.0.1", "upload_time": "2026-08-05T19:21:53Z", "requires_dist": ["asgiref>=3.9", "sqlparse>=0.3.1", "tzdata ; sys_platform == \"win32\"", "argon2-cffi>=23.1.0 ; extra == \"argon2\""], "summary": "A high-level Python web framework.", "requires_python": ">=3.12"}
{"name": "zope.interface", "version": "8.0", "upload_time": "2026-02-01T00:00:00Z", "requires_dist": ["setuptools"], "summary": "Interfaces for Python", "requires_python": ">=3.9"}
{"name": "flask", "version": "3.2.0", "upload_time": "2026-03-11T00:00:00Z", "requires_dist": ["Werkzeug>=3.1", "Jinja2>=3.1.2", "click>=8.1.3", "pytest ; extra == \"test\"", "pytest ; extra == \"dev\""], "summary": "A simple framework for building complex web applications.", "requires_python": ">=3.10"}
{"name": "onlyprereleases", "version": "0.1.0rc1", "upload_time": "2026-01-05T00:00:00Z", "requires_dist": ["requests"], "summary": "Never had a final release.", "requires_python": ">=3.9"}
{"name": "epochal", "version": "1!0.1", "upload_time": "2026-01-06T00:00:00Z", "requires_dist": ["requests>=2"], "summary": "Uses a PEP 440 epoch.", "requires_python": ">=3.9"}
{"name": "postal", "version": "1.0.post3", "upload_time": "2026-01-07T00:00:00Z", "requires_dist": ["Django"], "summary": "Ships post releases.", "requires_python": ">=3.9"}
{"name": "malformed-deps", "version": "0.3.0", "upload_time": "2026-01-08T00:00:00Z", "requires_dist": ["requests", "this is not (((a requirement"], "summary": "Has one unparseable requirement.", "requires_python": ">=3.9"}
{"name": "ghost-dep", "version": "0.1.0", "upload_time": "2026-01-09T00:00:00Z", "requires_dist": ["totally-not-on-pypi", "requests"], "summary": "Depends on something not live on PyPI.", "requires_python": ">=3.9"}
{"name": "no-deps", "version": "5.0.0", "upload_time": "2026-01-10T00:00:00Z", "requires_dist": [], "summary": "Declares nothing.", "requires_python": ">=3.9"}
{"name": "dupe-decl", "version": "1.0.0", "upload_time": "2026-01-11T00:00:00Z", "requires_dist": ["requests>=2 ; python_version < \"3.11\"", "requests>=3 ; python_version >= \"3.11\""], "summary": "Declares the same target twice.", "requires_python": ">=3.9"}
{"name": "Ruamel_YAML", "version": "0.19.0", "upload_time": "2026-01-12T00:00:00Z", "requires_dist": ["requests"], "summary": "Underscore and mixed case in its own name.", "requires_python": ">=3.9"}
{"name": "deleted-project", "version": "1.0.0", "upload_time": "2020-01-01T00:00:00Z", "requires_dist": ["requests"], "summary": "No longer live on PyPI.", "requires_python": ">=3.6"}
{"name": "pytest", "version": "9.1.1", "upload_time": "2026-04-01T00:00:00Z", "requires_dist": ["iniconfig", "pluggy>=1.5"], "summary": "Testing framework.", "requires_python": ">=3.10"}
{"name": "urllib3", "version": "2.7.0", "upload_time": "2026-05-07T16:13:18Z", "requires_dist": [], "summary": "HTTP library.", "requires_python": ">=3.9"}
```

Each row earns its place: `dupe-decl` proves `COUNT(DISTINCT dependent)` rather than an edge count, `flask` proves multi-gate extras, `ghost-dep` proves non-PyPI targets survive into the graph but not the ranking, `deleted-project` proves `is_live` works on both ends — it is neither ranked nor counted as anyone's dependent — and `Ruamel_YAML` and `zope.interface` prove canonicalization on the dependent side.

`pytest` and `urllib3` are here for a structural reason, not a semantic one: rankings are driven off the `projects` table, so a project only gets a ranking row if it has a winner release of its own. In the real corpus effectively every live project does. In a fixture it has to be arranged deliberately, or an assertion about `pytest`'s rank has nothing to assert against.

Create `tests/fixtures/audit_sample.jsonl`:

```jsonl
{"name": "requests", "version": "2.34.2"}
{"name": "requests", "version": "2.34.1"}
{"name": "requests", "version": "3.0.0rc1"}
{"name": "onlyprereleases", "version": "0.1.0a1"}
{"name": "onlyprereleases", "version": "0.1.0rc1"}
{"name": "epochal", "version": "1!0.1"}
{"name": "epochal", "version": "99.0"}
```

`requests` here has a `3.0.0rc1` that must lose to `2.34.2`, and `epochal` has a `99.0` that must lose to `1!0.1`. Both are cases a naive sort gets wrong.

Create `tests/fixtures/live_names.txt`, one canonical name per line:

```text
requests
django
zope-interface
flask
onlyprereleases
epochal
postal
malformed-deps
ghost-dep
no-deps
dupe-decl
ruamel-yaml
urllib3
certifi
pysocks
asgiref
sqlparse
tzdata
argon2-cffi
setuptools
werkzeug
jinja2
click
pytest
iniconfig
pluggy
```

`deleted-project` and `totally-not-on-pypi` are absent on purpose.

- [ ] **Step 2: Write the failing test**

Create `tests/test_fixture_source.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from top_pypi_dependents.sources.fixture import FixtureSource

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def source() -> FixtureSource:
    return FixtureSource(FIXTURES)


def test_winners_are_loaded_with_canonical_names(source: FixtureSource) -> None:
    by_canonical = {w.canonical_name: w for w in source.winners()}
    assert by_canonical["zope-interface"].name == "zope.interface"
    assert by_canonical["ruamel-yaml"].name == "Ruamel_YAML"
    assert by_canonical["django"].version == "6.0.1"


def test_winner_upload_time_is_timezone_aware(source: FixtureSource) -> None:
    winner = next(w for w in source.winners() if w.canonical_name == "requests")
    assert winner.upload_time == datetime(2026, 5, 14, 19, 25, 27, tzinfo=UTC)


def test_requires_dist_is_a_tuple(source: FixtureSource) -> None:
    winner = next(w for w in source.winners() if w.canonical_name == "no-deps")
    assert winner.requires_dist == ()


def test_audit_sample_groups_versions_by_canonical_project(source: FixtureSource) -> None:
    sample = source.audit_sample()
    assert sorted(sample["requests"]) == ["2.34.1", "2.34.2", "3.0.0rc1"]
    assert sorted(sample["epochal"]) == ["1!0.1", "99.0"]


def test_live_names_are_canonical_and_exclude_deleted(source: FixtureSource) -> None:
    live = source.live_names()
    assert "django" in live
    assert "deleted-project" not in live
    assert "totally-not-on-pypi" not in live


def test_source_reports_its_name(source: FixtureSource) -> None:
    assert source.name == "fixture"
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_fixture_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'top_pypi_dependents.sources'`

- [ ] **Step 4: Implement the protocol**

Create `src/top_pypi_dependents/sources/__init__.py`:

```python
"""Metadata sources for the extract stage."""

from top_pypi_dependents.sources.base import MetadataSource, Winner

__all__ = ["MetadataSource", "Winner"]
```

Create `src/top_pypi_dependents/sources/base.py`:

```python
"""The contract every metadata source satisfies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Winner:
    """The release of one project that the ranking reads dependencies from."""

    name: str
    canonical_name: str
    version: str
    upload_time: datetime
    requires_dist: tuple[str, ...]
    summary: str
    requires_python: str


@runtime_checkable
class MetadataSource(Protocol):
    """Supplies everything the build stage needs, from anywhere."""

    @property
    def name(self) -> str:
        """Identifier recorded in ``snapshots.source``."""
        ...

    def winners(self) -> list[Winner]:
        """One selected release per project."""
        ...

    def audit_sample(self) -> dict[str, list[str]]:
        """Canonical project name to every version it has published, for a subset."""
        ...

    def live_names(self) -> set[str]:
        """Canonical names of every project currently live on PyPI."""
        ...
```

- [ ] **Step 5: Implement the fixture source**

Create `src/top_pypi_dependents/sources/fixture.py`:

```python
"""A metadata source backed by checked-in JSONL, for tests and offline development."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from top_pypi_dependents.normalize import canonical
from top_pypi_dependents.sources.base import Winner


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class FixtureSource:
    """Reads ``winners.jsonl``, ``audit_sample.jsonl``, and ``live_names.txt``."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def name(self) -> str:
        return "fixture"

    def winners(self) -> list[Winner]:
        rows = _read_jsonl(self._directory / "winners.jsonl")
        return [
            Winner(
                name=str(row["name"]),
                canonical_name=canonical(str(row["name"])),
                version=str(row["version"]),
                upload_time=datetime.fromisoformat(str(row["upload_time"])),
                requires_dist=tuple(str(item) for item in row["requires_dist"]),  # type: ignore[union-attr]
                summary=str(row["summary"]),
                requires_python=str(row["requires_python"]),
            )
            for row in rows
        ]

    def audit_sample(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for row in _read_jsonl(self._directory / "audit_sample.jsonl"):
            grouped[canonical(str(row["name"]))].append(str(row["version"]))
        return dict(grouped)

    def live_names(self) -> set[str]:
        text = (self._directory / "live_names.txt").read_text(encoding="utf-8")
        return {canonical(line) for line in text.split() if line}
```

`datetime.fromisoformat` handles the trailing `Z` on Python 3.11 and later, yielding a UTC-aware datetime.

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/test_fixture_source.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/top_pypi_dependents/sources tests/fixtures tests/test_fixture_source.py
git commit -m "Add metadata source protocol and fixture corpus"
```

---

### Task 5: DuckDB schema, snapshot loading, and rankings

**Files:**
- Create: `src/top_pypi_dependents/warehouse.py`
- Test: `tests/test_warehouse.py`

**Interfaces:**
- Consumes: `Winner` from Task 4, `parse_requirement` from Task 2, `audit`/`Disagreement` from Task 3.
- Produces:
  - `AuditFailedError(Exception)` with attribute `disagreements: list[Disagreement]`
  - `connect(path: Path | None) -> duckdb.DuckDBPyConnection` — `None` means in-memory
  - `create_schema(con) -> None`
  - `load_snapshot(con, *, source: MetadataSource, captured_at: datetime) -> int` — returns `snapshot_id`, raises `AuditFailedError`
  - `compute_rankings(con, snapshot_id: int) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_warehouse.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from top_pypi_dependents import warehouse
from top_pypi_dependents.sources.base import Winner
from top_pypi_dependents.sources.fixture import FixtureSource

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURED = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
def con_and_snapshot():
    con = warehouse.connect(None)
    warehouse.create_schema(con)
    snapshot_id = warehouse.load_snapshot(
        con, source=FixtureSource(FIXTURES), captured_at=CAPTURED
    )
    warehouse.compute_rankings(con, snapshot_id)
    return con, snapshot_id


def test_snapshot_row_records_provenance(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    row = con.execute(
        "SELECT source, captured_at, unparsed_count FROM snapshots WHERE snapshot_id = ?",
        [snapshot_id],
    ).fetchone()
    assert row[0] == "fixture"
    assert row[2] == 1  # malformed-deps has one unparseable entry


def test_is_live_reflects_the_simple_index(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    live = dict(
        con.execute(
            "SELECT canonical_name, is_live FROM projects WHERE snapshot_id = ?",
            [snapshot_id],
        ).fetchall()
    )
    assert live["django"] is True
    assert live["deleted-project"] is False


def test_edges_are_canonical_on_both_ends(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    rows = con.execute(
        "SELECT dependent, dependency FROM dependencies "
        "WHERE snapshot_id = ? AND dependent = 'ruamel-yaml'",
        [snapshot_id],
    ).fetchall()
    assert rows == [("ruamel-yaml", "requests")]


def test_extras_gated_edges_are_marked_not_runtime(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    rows = con.execute(
        "SELECT extra, is_runtime FROM dependencies "
        "WHERE snapshot_id = ? AND dependent = 'flask' AND dependency = 'pytest'",
        [snapshot_id],
    ).fetchall()
    assert sorted(rows) == [("dev", False), ("test", False)]


def test_unparseable_requirement_is_not_an_edge(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    count = con.execute(
        "SELECT count(*) FROM dependencies WHERE snapshot_id = ? AND dependent = 'malformed-deps'",
        [snapshot_id],
    ).fetchone()[0]
    assert count == 1


def test_duplicate_declarations_count_once(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    edges = con.execute(
        "SELECT count(*) FROM dependencies "
        "WHERE snapshot_id = ? AND dependent = 'dupe-decl' AND dependency = 'requests'",
        [snapshot_id],
    ).fetchone()[0]
    dependents = con.execute(
        "SELECT dependents_runtime FROM rankings "
        "WHERE snapshot_id = ? AND canonical_name = 'requests'",
        [snapshot_id],
    ).fetchone()[0]
    assert edges == 2
    # requests is depended on by: onlyprereleases, epochal, malformed-deps,
    # ghost-dep, dupe-decl (twice, counted once), ruamel-yaml.
    # deleted-project also declares it but is not live, so it does not count.
    assert dependents == 6


def test_deleted_projects_neither_rank_nor_vote(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    ranked = con.execute(
        "SELECT count(*) FROM rankings "
        "WHERE snapshot_id = ? AND canonical_name = 'deleted-project'",
        [snapshot_id],
    ).fetchone()[0]
    edge = con.execute(
        "SELECT count(*) FROM dependencies "
        "WHERE snapshot_id = ? AND dependent = 'deleted-project'",
        [snapshot_id],
    ).fetchone()[0]
    assert ranked == 0
    assert edge == 1  # the edge survives in the graph, it just does not count


def test_runtime_and_all_counts_differ_for_an_extras_only_target(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    row = con.execute(
        "SELECT dependents_runtime, dependents_all FROM rankings "
        "WHERE snapshot_id = ? AND canonical_name = 'pytest'",
        [snapshot_id],
    ).fetchone()
    assert row == (0, 1)


def test_non_pypi_targets_are_stored_but_not_ranked(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    edge = con.execute(
        "SELECT count(*) FROM dependencies "
        "WHERE snapshot_id = ? AND dependency = 'totally-not-on-pypi'",
        [snapshot_id],
    ).fetchone()[0]
    ranked = con.execute(
        "SELECT count(*) FROM rankings "
        "WHERE snapshot_id = ? AND canonical_name = 'totally-not-on-pypi'",
        [snapshot_id],
    ).fetchone()[0]
    assert edge == 1
    assert ranked == 0


def test_ranks_are_dense_and_tie_broken_by_name(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    rows = con.execute(
        "SELECT canonical_name, rank_runtime FROM rankings "
        "WHERE snapshot_id = ? ORDER BY rank_runtime LIMIT 3",
        [snapshot_id],
    ).fetchall()
    assert rows[0] == ("requests", 1)
    ranks = [rank for _, rank in rows]
    assert ranks == sorted(ranks)


def test_audit_disagreement_aborts_the_load() -> None:
    class LyingSource(FixtureSource):
        """Picks the prerelease that packaging says should lose."""

        def winners(self) -> list[Winner]:
            kept = [w for w in super().winners() if w.canonical_name != "requests"]
            return [
                *kept,
                Winner(
                    name="requests",
                    canonical_name="requests",
                    version="3.0.0rc1",
                    upload_time=datetime(2026, 5, 14, tzinfo=UTC),
                    requires_dist=(),
                    summary="",
                    requires_python="",
                ),
            ]

    con = warehouse.connect(None)
    warehouse.create_schema(con)
    with pytest.raises(warehouse.AuditFailedError) as excinfo:
        warehouse.load_snapshot(
            con, source=LyingSource(FIXTURES), captured_at=CAPTURED
        )
    assert excinfo.value.disagreements[0].project == "requests"
    assert excinfo.value.disagreements[0].sql_pick == "3.0.0rc1"
    assert excinfo.value.disagreements[0].packaging_pick == "2.34.2"
```

Before implementing, hand-verify the two arithmetic assertions (`dependents == 7`, `pytest == (0, 1)`) against `tests/fixtures/winners.jsonl`. If the fixture and the expectation disagree, the fixture is the thing to trust — update the number here.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_warehouse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'top_pypi_dependents.warehouse'`

- [ ] **Step 3: Implement the schema**

Create `src/top_pypi_dependents/warehouse.py` starting with:

```python
"""DuckDB schema, snapshot loading, and ranking."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from top_pypi_dependents.normalize import canonical, parse_requirement
from top_pypi_dependents.versions import Disagreement, audit

if TYPE_CHECKING:
    from top_pypi_dependents.sources.base import MetadataSource

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id    INTEGER PRIMARY KEY,
    captured_at    TIMESTAMPTZ NOT NULL,
    source         VARCHAR NOT NULL,
    project_count  INTEGER NOT NULL,
    edge_count     INTEGER NOT NULL,
    unparsed_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    snapshot_id        INTEGER NOT NULL,
    canonical_name     VARCHAR NOT NULL,
    name               VARCHAR NOT NULL,
    latest_version     VARCHAR NOT NULL,
    latest_upload_time TIMESTAMPTZ,
    summary            VARCHAR,
    requires_python    VARCHAR,
    is_live            BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS dependencies (
    snapshot_id    INTEGER NOT NULL,
    dependent      VARCHAR NOT NULL,
    dependency     VARCHAR NOT NULL,
    dependency_raw VARCHAR NOT NULL,
    specifier      VARCHAR,
    extra          VARCHAR,
    marker         VARCHAR,
    is_runtime     BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS rankings (
    snapshot_id        INTEGER NOT NULL,
    canonical_name     VARCHAR NOT NULL,
    rank_runtime       INTEGER,
    dependents_runtime INTEGER NOT NULL,
    rank_all           INTEGER,
    dependents_all     INTEGER NOT NULL
);
"""


class AuditFailedError(Exception):
    """The source's version selection disagreed with ``packaging``."""

    def __init__(self, disagreements: list[Disagreement]) -> None:
        self.disagreements = disagreements
        preview = ", ".join(
            f"{d.project}: source={d.sql_pick!r} packaging={d.packaging_pick!r}"
            for d in disagreements[:5]
        )
        super().__init__(
            f"{len(disagreements)} version-selection disagreement(s): {preview}"
        )


def connect(path: Path | None) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection; ``None`` opens an in-memory database."""
    return duckdb.connect(":memory:" if path is None else str(path))


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create every table if it does not already exist."""
    con.execute(SCHEMA)
```

- [ ] **Step 4: Implement loading**

Append to `warehouse.py`:

```python
def load_snapshot(
    con: duckdb.DuckDBPyConnection,
    *,
    source: MetadataSource,
    captured_at: datetime,
) -> int:
    """Load one snapshot. Raises ``AuditFailedError`` before writing anything."""
    winners = source.winners()
    sql_picks = {w.canonical_name: w.version for w in winners}
    sample = source.audit_sample()
    disagreements = audit(sample, sql_picks)
    if disagreements:
        raise AuditFailedError(disagreements)

    for winner in winners:
        expected = canonical(winner.name)
        if winner.canonical_name != expected:
            msg = (
                f"source canonicalized {winner.name!r} to {winner.canonical_name!r}; "
                f"packaging says {expected!r}"
            )
            raise ValueError(msg)

    live = source.live_names()
    next_id = con.execute("SELECT coalesce(max(snapshot_id), 0) + 1 FROM snapshots").fetchone()
    snapshot_id = int(next_id[0])  # type: ignore[index]

    project_rows = [
        (
            snapshot_id,
            w.canonical_name,
            w.name,
            w.version,
            w.upload_time,
            w.summary,
            w.requires_python,
            w.canonical_name in live,
        )
        for w in winners
    ]
    con.executemany(
        "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?)", project_rows
    )

    edge_rows = []
    unparsed = 0
    for winner in winners:
        for raw in winner.requires_dist:
            parsed = parse_requirement(raw)
            if parsed is None:
                unparsed += 1
                continue
            edge_rows.append(
                (
                    snapshot_id,
                    winner.canonical_name,
                    parsed.dependency,
                    parsed.dependency_raw,
                    parsed.specifier,
                    parsed.extra,
                    parsed.marker,
                    parsed.is_runtime,
                )
            )
    if edge_rows:
        con.executemany(
            "INSERT INTO dependencies VALUES (?, ?, ?, ?, ?, ?, ?, ?)", edge_rows
        )

    con.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
        [snapshot_id, captured_at, source.name, len(winners), len(edge_rows), unparsed],
    )
    return snapshot_id
```

The canonicalization re-derivation loop is the full oracle the spec calls for: it runs over every winner, not a sample, because it is cheap.

- [ ] **Step 5: Implement rankings**

Append to `warehouse.py`:

```python
RANKINGS_SQL = """
INSERT INTO rankings
WITH live_edges AS (
    SELECT d.dependent, d.dependency, d.is_runtime
    FROM dependencies AS d
    JOIN projects AS dep
        ON dep.snapshot_id = d.snapshot_id AND dep.canonical_name = d.dependent
    WHERE d.snapshot_id = ? AND dep.is_live
),
counted AS (
    SELECT
        p.canonical_name,
        count(DISTINCT CASE WHEN e.is_runtime THEN e.dependent END) AS dependents_runtime,
        count(DISTINCT e.dependent) AS dependents_all
    FROM projects AS p
    LEFT JOIN live_edges AS e ON e.dependency = p.canonical_name
    WHERE p.snapshot_id = ? AND p.is_live
    GROUP BY p.canonical_name
)
SELECT
    ? AS snapshot_id,
    canonical_name,
    row_number() OVER (ORDER BY dependents_runtime DESC, canonical_name ASC) AS rank_runtime,
    dependents_runtime,
    row_number() OVER (ORDER BY dependents_all DESC, canonical_name ASC) AS rank_all,
    dependents_all
FROM counted
"""


def compute_rankings(con: duckdb.DuckDBPyConnection, snapshot_id: int) -> None:
    """Populate ``rankings`` for one snapshot."""
    con.execute(RANKINGS_SQL, [snapshot_id, snapshot_id, snapshot_id])
```

Two filters carry real weight here:

- Ranking is driven off `projects`, not off `dependencies`. That keeps non-PyPI targets like `totally-not-on-pypi` out of the ranking while leaving their edges intact in the graph.
- Both endpoints are filtered to live projects. `distribution_metadata` never deletes rows, so `deleted-project` would otherwise keep voting for `requests` forever, and every count would drift upward month over month for no real reason.

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/test_warehouse.py -v`
Expected: all PASS.

If a count assertion fails, re-derive it by hand from the fixture before changing any code — the test is more likely wrong than the SQL, and silently loosening the assertion defeats its purpose.

- [ ] **Step 7: Commit**

```bash
git add src/top_pypi_dependents/warehouse.py tests/test_warehouse.py
git commit -m "Add DuckDB schema, snapshot loading, and rankings"
```

---

### Task 6: JSON artifact with month-over-month rank deltas

**Files:**
- Create: `src/top_pypi_dependents/artifacts.py`
- Test: `tests/test_artifacts.py`

**Interfaces:**
- Consumes: a populated connection from Task 5.
- Produces:
  - `SCHEMA_VERSION: int = 1`
  - `read_payload(path: Path) -> dict | None`
  - `build_payload(con, snapshot_id: int, *, limit: int, previous: dict | None) -> dict`
  - `write_json(payload: dict, path: Path) -> None`
  - `export_edges(con, snapshot_id: int, path: Path) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_artifacts.py`:

```python
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from top_pypi_dependents import artifacts, warehouse
from top_pypi_dependents.sources.fixture import FixtureSource

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def con_and_snapshot():
    con = warehouse.connect(None)
    warehouse.create_schema(con)
    snapshot_id = warehouse.load_snapshot(
        con, source=FixtureSource(FIXTURES), captured_at=datetime(2026, 9, 1, tzinfo=UTC)
    )
    warehouse.compute_rankings(con, snapshot_id)
    return con, snapshot_id


def test_payload_header_fields(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    payload = artifacts.build_payload(con, snapshot_id, limit=100, previous=None)
    assert payload["schema_version"] == 1
    assert payload["source"] == "fixture"
    assert payload["generated_at"] == "2026-09-01T00:00:00+00:00"
    assert payload["counting"] == {
        "basis": "latest non-prerelease release",
        "ranked_on": "runtime",
    }
    assert payload["previous_generated_at"] is None


def test_rows_are_ranked_and_capped(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    payload = artifacts.build_payload(con, snapshot_id, limit=3, previous=None)
    assert len(payload["rows"]) == 3
    assert payload["rows"][0]["rank"] == 1
    assert payload["rows"][0]["project"] == "requests"
    assert payload["rows"][0]["previous_rank"] is None
    assert payload["rows"][0]["rank_change"] is None


def test_rank_change_is_positive_when_a_project_climbs(con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    previous = {
        "generated_at": "2026-08-01T00:00:00+00:00",
        "rows": [{"rank": 4, "project": "requests"}],
    }
    payload = artifacts.build_payload(con, snapshot_id, limit=5, previous=previous)
    row = next(r for r in payload["rows"] if r["project"] == "requests")
    assert row["previous_rank"] == 4
    assert row["rank_change"] == 3
    assert payload["previous_generated_at"] == "2026-08-01T00:00:00+00:00"


def test_read_payload_returns_none_when_absent(tmp_path: Path) -> None:
    assert artifacts.read_payload(tmp_path / "nope.json") is None


def test_write_json_round_trips(tmp_path: Path, con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    payload = artifacts.build_payload(con, snapshot_id, limit=5, previous=None)
    path = tmp_path / "latest.json"
    artifacts.write_json(payload, path)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_export_edges_writes_every_edge(tmp_path: Path, con_and_snapshot) -> None:
    con, snapshot_id = con_and_snapshot
    path = tmp_path / "edges.parquet"
    artifacts.export_edges(con, snapshot_id, path)
    count = con.execute(
        "SELECT count(*) FROM read_parquet(?)", [str(path)]
    ).fetchone()[0]
    expected = con.execute(
        "SELECT count(*) FROM dependencies WHERE snapshot_id = ?", [snapshot_id]
    ).fetchone()[0]
    assert count == expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_artifacts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'top_pypi_dependents.artifacts'`

- [ ] **Step 3: Implement**

Create `src/top_pypi_dependents/artifacts.py`:

```python
"""Emit the ranked JSON artifact and the Parquet edge export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

SCHEMA_VERSION = 1

_ROWS_SQL = """
SELECT
    r.rank_runtime,
    r.canonical_name,
    r.dependents_runtime,
    r.dependents_all
FROM rankings AS r
WHERE r.snapshot_id = ?
ORDER BY r.rank_runtime
LIMIT ?
"""


def read_payload(path: Path) -> dict[str, Any] | None:
    """Load a JSON artifact from disk, or ``None`` if it is not there yet.

    Serves two callers: ``artifacts`` reads the file it is about to overwrite, to
    compute rank movement; ``render`` reads the finished file it renders from.
    """
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_payload(
    con: duckdb.DuckDBPyConnection,
    snapshot_id: int,
    *,
    limit: int,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the JSON payload, including rank movement against ``previous``."""
    header = con.execute(
        "SELECT captured_at, source, project_count, edge_count "
        "FROM snapshots WHERE snapshot_id = ?",
        [snapshot_id],
    ).fetchone()
    if header is None:
        msg = f"no snapshot with id {snapshot_id}"
        raise ValueError(msg)
    captured_at, source, project_count, edge_count = header

    prior_ranks: dict[str, int] = {}
    if previous is not None:
        prior_ranks = {
            str(row["project"]): int(row["rank"]) for row in previous.get("rows", [])
        }

    rows = []
    for rank, name, runtime, all_count in con.execute(
        _ROWS_SQL, [snapshot_id, limit]
    ).fetchall():
        prior = prior_ranks.get(name)
        rows.append(
            {
                "rank": int(rank),
                "project": name,
                "dependents": int(runtime),
                "dependents_all": int(all_count),
                "previous_rank": prior,
                "rank_change": None if prior is None else prior - int(rank),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": captured_at.isoformat(),
        "source": source,
        "counting": {
            "basis": "latest non-prerelease release",
            "ranked_on": "runtime",
        },
        "previous_generated_at": (
            None if previous is None else previous.get("generated_at")
        ),
        "project_count": int(project_count),
        "edge_count": int(edge_count),
        "rows": rows,
    }


def write_json(payload: dict[str, Any], path: Path) -> None:
    """Write the payload deterministically, so git diffs stay readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def export_edges(
    con: duckdb.DuckDBPyConnection, snapshot_id: int, path: Path
) -> None:
    """Write one snapshot's full edge list to Parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # DuckDB rejects a bound parameter as a COPY target ("Unsupported parameter
    # type for filename"), so the path is interpolated as a SQL string literal
    # with embedded quotes doubled. The predicate stays parameterized.
    target = str(path).replace("'", "''")
    con.execute(
        f"COPY (SELECT * FROM dependencies WHERE snapshot_id = ?) "  # noqa: S608
        f"TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)",
        [snapshot_id],
    )
```

`rank_change` is `previous - current`, so a positive number means the project climbed.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_artifacts.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/top_pypi_dependents/artifacts.py tests/test_artifacts.py
git commit -m "Add ranked JSON artifact and Parquet edge export"
```

---

### Task 7: Static site rendering

**Files:**
- Create: `src/top_pypi_dependents/render.py`
- Create: `src/top_pypi_dependents/templates/base.html.j2`
- Create: `src/top_pypi_dependents/templates/index.html.j2`
- Create: `src/top_pypi_dependents/templates/table.html.j2`
- Modify: `pyproject.toml` — package the templates
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: a payload dict from `artifacts.build_payload` (Task 6).
- Produces: `TIERS: tuple[int, ...] = (100, 1000, 10000)`, `render_site(payload: dict[str, Any], out_dir: Path, *, tiers: Sequence[int] = TIERS) -> None`

`render` takes the finished payload rather than a database connection. The payload already carries `previous_rank` and `rank_change`; recomputing them from DuckDB after `artifacts` has overwritten `data/latest.json` would compare the new ranking against itself and render every row as unchanged. It also keeps `render.py` free of a DuckDB import.

Templates live inside the package so the CLI works from an installed wheel, not just a checkout. Add to `pyproject.toml`:

```toml
[tool.uv.build-backend]
source-include = ["src/top_pypi_dependents/templates/*"]
```

- [ ] **Step 1: Write the failing test**

Create `tests/test_render.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from top_pypi_dependents import artifacts, render, warehouse
from top_pypi_dependents.sources.fixture import FixtureSource

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def payload() -> dict:
    con = warehouse.connect(None)
    warehouse.create_schema(con)
    snapshot_id = warehouse.load_snapshot(
        con, source=FixtureSource(FIXTURES), captured_at=datetime(2026, 9, 1, tzinfo=UTC)
    )
    warehouse.compute_rankings(con, snapshot_id)
    return artifacts.build_payload(
        con,
        snapshot_id,
        limit=50,
        previous={
            "generated_at": "2026-08-01T00:00:00+00:00",
            "rows": [{"rank": 4, "project": "requests"}],
        },
    )


def test_renders_one_page_per_tier_plus_index(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2, 5))
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "top-2.html").exists()
    assert (tmp_path / "top-5.html").exists()


def test_page_contains_ranked_rows(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "top-2.html").read_text(encoding="utf-8")
    assert "requests" in html
    assert "pypi.org/project/requests/" in html


def test_page_shows_rank_movement(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "top-2.html").read_text(encoding="utf-8")
    assert "&#9650; 3" in html  # requests climbed from 4 to 1


def test_tier_larger_than_the_payload_does_not_crash(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(10_000,))
    html = (tmp_path / "top-10000.html").read_text(encoding="utf-8")
    assert "requests" in html


def test_page_is_self_contained(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "top-2.html").read_text(encoding="utf-8")
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "cdn." not in html


def test_index_states_the_methodology(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "yanked" in html.lower()
    assert "extra" in html.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'top_pypi_dependents.render'`

- [ ] **Step 3: Write the templates**

Create `src/top_pypi_dependents/templates/base.html.j2`:

```jinja
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Top PyPI Dependents{% endblock %}</title>
<style>
  :root { color-scheme: light dark; --fg: #1a1a1a; --bg: #fff; --muted: #666; --line: #ddd; }
  @media (prefers-color-scheme: dark) {
    :root { --fg: #e8e8e8; --bg: #16181c; --muted: #999; --line: #333; }
  }
  body { margin: 0 auto; max-width: 60rem; padding: 2rem 1rem; background: var(--bg);
         color: var(--fg); font: 16px/1.5 system-ui, sans-serif; }
  a { color: inherit; }
  nav a { margin-right: 1rem; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border-bottom: 1px solid var(--line); padding: .4rem .6rem; text-align: left; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .up { color: #1a7f37; }
  .down { color: #b3261e; }
  .muted { color: var(--muted); }
  input { width: 100%; padding: .5rem; margin: 1rem 0; font: inherit;
          background: var(--bg); color: var(--fg); border: 1px solid var(--line); }
</style>
</head>
<body>
<nav>
  <a href="index.html">About</a>
  {% for tier in tiers %}<a href="top-{{ tier }}.html">Top {{ "{:,}".format(tier) }}</a>{% endfor %}
</nav>
{% block content %}{% endblock %}
<footer class="muted">
  <p>Generated {{ generated_at }} from {{ source }}.
  {{ "{:,}".format(project_count) }} projects, {{ "{:,}".format(edge_count) }} edges.</p>
</footer>
</body>
</html>
```

Create `src/top_pypi_dependents/templates/table.html.j2`:

```jinja
{% extends "base.html.j2" %}
{% block title %}Top {{ "{:,}".format(tier) }} PyPI Dependents{% endblock %}
{% block content %}
<h1>Top {{ "{:,}".format(tier) }} PyPI projects by dependent count</h1>
<p class="muted">Ranked by how many projects declare an unconditional runtime dependency
on them, using each project's latest release.</p>
<input id="filter" type="search" placeholder="Filter by project name" autocomplete="off">
<table id="rows">
  <thead>
    <tr>
      <th class="num">#</th><th>Project</th>
      <th class="num">Dependents</th><th class="num">Incl. extras</th><th>Change</th>
    </tr>
  </thead>
  <tbody>
  {% for row in rows %}
    <tr>
      <td class="num">{{ row.rank }}</td>
      <td><a href="https://pypi.org/project/{{ row.project }}/">{{ row.project }}</a></td>
      <td class="num">{{ "{:,}".format(row.dependents) }}</td>
      <td class="num">{{ "{:,}".format(row.dependents_all) }}</td>
      <td>
      {%- if row.rank_change is none %}<span class="muted">new</span>
      {%- elif row.rank_change > 0 %}<span class="up">&#9650; {{ row.rank_change }}</span>
      {%- elif row.rank_change < 0 %}<span class="down">&#9660; {{ -row.rank_change }}</span>
      {%- else %}<span class="muted">&ndash;</span>{% endif %}
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>
<script>
document.getElementById("filter").addEventListener("input", (event) => {
  const needle = event.target.value.toLowerCase();
  for (const row of document.querySelectorAll("#rows tbody tr")) {
    row.hidden = !row.cells[1].textContent.toLowerCase().includes(needle);
  }
});
</script>
{% endblock %}
```

Create `src/top_pypi_dependents/templates/index.html.j2`:

```jinja
{% extends "base.html.j2" %}
{% block content %}
<h1>Top PyPI Dependents</h1>
<p>Which PyPI projects does the rest of PyPI actually depend on? This ranks projects by
the number of other projects that declare a dependency on them, refreshed monthly.</p>

<h2>How it is counted</h2>
<ul>
  <li>One release per project &mdash; the highest final release by PEP&nbsp;440 ordering,
      falling back to the highest prerelease for projects that have only prereleases.</li>
  <li>The headline number counts distinct projects declaring an <em>unconditional runtime</em>
      dependency. Dependencies gated behind an <code>extra</code> are counted separately, in
      the "incl. extras" column. That distinction matters: roughly 37% of all declared edges
      are extras-gated, which is why an unfiltered count puts test tooling above numpy.</li>
  <li>Names are normalized per PEP&nbsp;503, so <code>Zope.Interface</code> and
      <code>zope-interface</code> are one project.</li>
</ul>

<h2>Known limitations</h2>
<ul>
  <li>Yanked releases cannot be excluded. The source table carries no yank status, and
      determining it would take one request per project.</li>
  <li>Only declared dependencies count. Transitive dependencies are not resolved.</li>
</ul>

<h2>Data</h2>
<p>The full ranked list is available as
<a href="https://github.com/miketheman/top-pypi-dependents/blob/main/data/latest.json">JSON</a>.
The complete dependency graph is published as a DuckDB database and a Parquet edge list on
each <a href="https://github.com/miketheman/top-pypi-dependents/releases">release</a>.</p>
{% endblock %}
```

- [ ] **Step 4: Implement the renderer**

Create `src/top_pypi_dependents/render.py`:

```python
"""Render the static site."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

TIERS: tuple[int, ...] = (100, 1000, 10000)


def _environment() -> Environment:
    return Environment(
        loader=PackageLoader("top_pypi_dependents", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_site(
    payload: dict[str, Any],
    out_dir: Path,
    *,
    tiers: Sequence[int] = TIERS,
) -> None:
    """Write ``index.html`` and one page per tier into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    env = _environment()
    shared = {
        "tiers": list(tiers),
        "generated_at": payload["generated_at"],
        "source": payload["source"],
        "project_count": payload["project_count"],
        "edge_count": payload["edge_count"],
    }

    (out_dir / "index.html").write_text(
        env.get_template("index.html.j2").render(**shared), encoding="utf-8"
    )
    table = env.get_template("table.html.j2")
    for tier in tiers:
        (out_dir / f"top-{tier}.html").write_text(
            table.render(tier=tier, rows=payload["rows"][:tier], **shared),
            encoding="utf-8",
        )
```

Slicing `payload["rows"][:tier]` is deliberately tolerant of a tier larger than the payload — Python truncates rather than raising, so a 10,000-row page renders whatever exists.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_render.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/top_pypi_dependents/render.py src/top_pypi_dependents/templates tests/test_render.py pyproject.toml
git commit -m "Render static site with Jinja2"
```

---

### Task 8: CLI wiring and an end-to-end fixture run

**Files:**
- Create: `src/top_pypi_dependents/cli.py`
- Create: `src/top_pypi_dependents/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 4 through 7.
- Produces: `main(argv: Sequence[str] | None = None) -> int`, with subcommands `extract`, `build`, `artifacts`, `render`.

The stage boundary: `extract` writes JSONL into a build directory in exactly the shape `FixtureSource` reads, which means `build` consumes a `FixtureSource` pointed at that directory regardless of where the data came from. One reader, one format, no branching downstream of extract.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
import json
import shutil
from pathlib import Path

import pytest

from top_pypi_dependents.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_end_to_end_from_fixture(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    for filename in ("winners.jsonl", "audit_sample.jsonl", "live_names.txt"):
        shutil.copy(FIXTURES / filename, build_dir / filename)

    db = build_dir / "dependents.duckdb"
    out_json = tmp_path / "data" / "latest.json"
    site = tmp_path / "site"

    assert main(["build", "--input", str(build_dir), "--database", str(db)]) == 0
    assert main(["artifacts", "--database", str(db), "--output", str(out_json),
                 "--limit", "5", "--edges", str(build_dir / "edges.parquet")]) == 0
    assert main(["render", "--payload", str(out_json), "--output", str(site),
                 "--tiers", "2,5"]) == 0

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["rows"][0]["project"] == "requests"
    assert (site / "index.html").exists()
    assert (build_dir / "edges.parquet").exists()


def test_artifacts_computes_deltas_against_the_existing_file(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    for filename in ("winners.jsonl", "audit_sample.jsonl", "live_names.txt"):
        shutil.copy(FIXTURES / filename, build_dir / filename)
    db = build_dir / "dependents.duckdb"
    out_json = tmp_path / "latest.json"
    out_json.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-01T00:00:00+00:00",
                "rows": [{"rank": 9, "project": "requests"}],
            }
        ),
        encoding="utf-8",
    )

    assert main(["build", "--input", str(build_dir), "--database", str(db)]) == 0
    assert main(["artifacts", "--database", str(db), "--output", str(out_json),
                 "--limit", "5"]) == 0

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    row = next(r for r in payload["rows"] if r["project"] == "requests")
    assert row["previous_rank"] == 9
    assert row["rank_change"] == 8


def test_render_without_a_payload_exits_nonzero(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["render", "--payload", str(tmp_path / "missing.json"),
              "--output", str(tmp_path / "site")])


def test_unknown_subcommand_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["nonsense"])
    assert excinfo.value.code != 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'top_pypi_dependents.cli'`

- [ ] **Step 3: Implement**

Create `src/top_pypi_dependents/cli.py`:

```python
"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from top_pypi_dependents import artifacts, render, warehouse
from top_pypi_dependents.sources.fixture import FixtureSource

DEFAULT_LIMIT = 100_000


def _build(args: argparse.Namespace) -> int:
    con = warehouse.connect(Path(args.database))
    warehouse.create_schema(con)
    snapshot_id = warehouse.load_snapshot(
        con,
        source=FixtureSource(Path(args.input)),
        captured_at=datetime.now(tz=UTC),
    )
    warehouse.compute_rankings(con, snapshot_id)
    con.close()
    return 0


def _latest_snapshot(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT max(snapshot_id) FROM snapshots").fetchone()
    if row is None or row[0] is None:
        msg = "database contains no snapshots; run `build` first"
        raise SystemExit(msg)
    return int(row[0])


def _artifacts(args: argparse.Namespace) -> int:
    con = warehouse.connect(Path(args.database))
    snapshot_id = _latest_snapshot(con)
    out = Path(args.output)
    payload = artifacts.build_payload(
        con, snapshot_id, limit=args.limit, previous=artifacts.read_payload(out)
    )
    artifacts.write_json(payload, out)
    if args.edges:
        artifacts.export_edges(con, snapshot_id, Path(args.edges))
    con.close()
    return 0


def _render(args: argparse.Namespace) -> int:
    payload = artifacts.read_payload(Path(args.payload))
    if payload is None:
        msg = f"{args.payload} not found; run `artifacts` first"
        raise SystemExit(msg)
    tiers = tuple(int(part) for part in args.tiers.split(","))
    render.render_site(payload, Path(args.output), tiers=tiers)
    return 0


def _extract(args: argparse.Namespace) -> int:
    from top_pypi_dependents.sources.bigquery import extract_to_directory

    return extract_to_directory(
        Path(args.output), project=args.project, dry_run=args.dry_run
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="top-pypi-dependents")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="pull winners and audit sample from BigQuery")
    extract.add_argument("--output", default="build", help="directory to write JSONL into")
    extract.add_argument("--project", default=None, help="GCP billing project id")
    extract.add_argument(
        "--dry-run", action="store_true", help="report bytes to be scanned and exit"
    )
    extract.set_defaults(func=_extract)

    build = sub.add_parser("build", help="load extracted data into DuckDB")
    build.add_argument("--input", default="build", help="directory holding the JSONL")
    build.add_argument("--database", default="build/dependents.duckdb")
    build.set_defaults(func=_build)

    art = sub.add_parser("artifacts", help="emit ranked JSON and the edge export")
    art.add_argument("--database", default="build/dependents.duckdb")
    art.add_argument("--output", default="data/latest.json")
    art.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    art.add_argument("--edges", default=None, help="path for the Parquet edge export")
    art.set_defaults(func=_artifacts)

    site = sub.add_parser("render", help="render the static site")
    site.add_argument("--payload", default="data/latest.json", help="the ranked JSON to render")
    site.add_argument("--output", default="site")
    site.add_argument("--tiers", default="100,1000,10000")
    site.set_defaults(func=_render)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch to a stage."""
    args = _parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
```

Create `src/top_pypi_dependents/__main__.py`:

```python
"""Allow ``python -m top_pypi_dependents``."""

import sys

from top_pypi_dependents.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

The `bigquery` import inside `_extract` is deliberate and load-bearing: it keeps `google-cloud-bigquery` out of the import path for every other subcommand and for the whole test suite.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the whole gate**

Run: `make lint test`
Expected: everything green.

- [ ] **Step 6: Commit**

```bash
git add src/top_pypi_dependents/cli.py src/top_pypi_dependents/__main__.py tests/test_cli.py
git commit -m "Add CLI with build, artifacts, and render stages"
```

---

### Task 9: BigQuery extract with the PEP 440 sort key

**Files:**
- Create: `src/top_pypi_dependents/sources/bigquery.py`
- Create: `src/top_pypi_dependents/sql/winners.sql`
- Create: `src/top_pypi_dependents/sql/audit_sample.sql`
- Modify: `pyproject.toml` — include `src/top_pypi_dependents/sql/*` in `source-include`
- Test: `tests/test_bigquery_sql.py`

**Interfaces:**
- Consumes: `Winner` from Task 4.
- Produces:
  - `WINNERS_SQL: str`, `AUDIT_SQL: str` (loaded from the `.sql` files)
  - `extract_to_directory(out_dir: Path, *, project: str | None, dry_run: bool) -> int`

This task is not exercised against live BigQuery in CI. Its tests check the SQL text and the file-writing contract; the query itself is proven by the audit oracle on the first real run.

- [ ] **Step 1: Write `src/top_pypi_dependents/sql/winners.sql`**

```sql
-- One selected release per project, with the dependency metadata to read from it.
--
-- Version ordering is decomposed into typed INT64 columns rather than a packed,
-- zero-padded string key. BigQuery compares integers natively; the components stay
-- readable when a pick looks wrong; and no LPAD width can silently truncate an
-- unusually long release segment (LPAD truncates rather than erroring).
--
-- Significance order, most significant first:
--   is_final, epoch, rel1..rel6, pre_rank, pre_num, post_rank, post_num, dev_rank, dev_num
--
-- is_final leads so any final release outranks any prerelease -- Warehouse's
-- `ORDER BY is_prerelease ASC`. The rest reproduce packaging's own sort key
-- (epoch, release, pre, post, dev), where an absent pre segment sorts as -inf for a
-- dev-only version and +inf for a final one, an absent post sorts as -inf, and an
-- absent dev sorts as +inf. Note post-releases are FINAL, not prereleases:
-- 1.0.post1 must outrank 1.0.
WITH parsed AS (
  SELECT
    name,
    version,
    upload_time,
    requires_dist,
    summary,
    requires_python,
    LOWER(REGEXP_REPLACE(name, r'[-_.]+', '-')) AS canonical_name,
    IFNULL(SAFE_CAST(REGEXP_EXTRACT(version, r'^(\d+)!') AS INT64), 0) AS epoch,
    IFNULL(
      REGEXP_EXTRACT(REGEXP_REPLACE(version, r'^\d+!', ''), r'^(\d+(?:\.\d+)*)'), '0'
    ) AS release_seg,
    IFNULL(
      REGEXP_EXTRACT(REGEXP_REPLACE(version, r'^\d+!', ''), r'^\d+(?:\.\d+)*(.*)$'), ''
    ) AS suffix
  FROM `bigquery-public-data.pypi.distribution_metadata`
),
-- Alternations are ordered longest-first (alpha before a, preview before pre)
-- so RE2's leftmost-first matching cannot stop at the short branch.
scored AS (
  SELECT
    *,
    SPLIT(release_seg, '.') AS rel,
    CASE
      WHEN REGEXP_CONTAINS(suffix, r'(?i)^[._-]?(alpha|a)\d*') THEN 1
      WHEN REGEXP_CONTAINS(suffix, r'(?i)^[._-]?(beta|b)\d*') THEN 2
      WHEN REGEXP_CONTAINS(suffix, r'(?i)^[._-]?(preview|pre|rc|c)\d*') THEN 3
      WHEN REGEXP_CONTAINS(suffix, r'(?i)^[._-]?dev\d*') THEN 0
      ELSE 4
    END AS pre_rank,
    IFNULL(
      SAFE_CAST(
        REGEXP_EXTRACT(
          suffix, r'(?i)^[._-]?(?:alpha|beta|preview|pre|rc|a|b|c)[._-]?(\d+)'
        ) AS INT64
      ), 0
    ) AS pre_num,
    IF(REGEXP_CONTAINS(suffix, r'(?i)post'), 1, 0) AS post_rank,
    IFNULL(SAFE_CAST(REGEXP_EXTRACT(suffix, r'(?i)post[._-]?(\d+)') AS INT64), 0) AS post_num,
    IF(REGEXP_CONTAINS(suffix, r'(?i)dev'), 0, 1) AS dev_rank,
    IFNULL(SAFE_CAST(REGEXP_EXTRACT(suffix, r'(?i)dev[._-]?(\d+)') AS INT64), 0) AS dev_num
  FROM parsed
),
keyed AS (
  SELECT
    *,
    -- Absent segments read as 0, so 1.2 and 1.2.0 compare equal, as PEP 440 requires.
    IFNULL(SAFE_CAST(rel[SAFE_OFFSET(0)] AS INT64), 0) AS rel1,
    IFNULL(SAFE_CAST(rel[SAFE_OFFSET(1)] AS INT64), 0) AS rel2,
    IFNULL(SAFE_CAST(rel[SAFE_OFFSET(2)] AS INT64), 0) AS rel3,
    IFNULL(SAFE_CAST(rel[SAFE_OFFSET(3)] AS INT64), 0) AS rel4,
    IFNULL(SAFE_CAST(rel[SAFE_OFFSET(4)] AS INT64), 0) AS rel5,
    IFNULL(SAFE_CAST(rel[SAFE_OFFSET(5)] AS INT64), 0) AS rel6,
    IF(pre_rank = 4 AND dev_rank = 1, 1, 0) AS is_final
  FROM scored
),
-- One row per (project, version): keep the most recently uploaded file's metadata.
per_version AS (
  SELECT * EXCEPT (rn) FROM (
    SELECT
      *,
      ROW_NUMBER() OVER (
        PARTITION BY canonical_name, version ORDER BY upload_time DESC
      ) AS rn
    FROM keyed
  )
  WHERE rn = 1
)
SELECT
  name,
  canonical_name,
  version,
  upload_time,
  requires_dist,
  IFNULL(summary, '') AS summary,
  IFNULL(requires_python, '') AS requires_python
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY canonical_name
      ORDER BY
        is_final DESC, epoch DESC,
        rel1 DESC, rel2 DESC, rel3 DESC, rel4 DESC, rel5 DESC, rel6 DESC,
        pre_rank DESC, pre_num DESC,
        post_rank DESC, post_num DESC,
        dev_rank DESC, dev_num DESC,
        version DESC
    ) AS wn
  FROM per_version
)
WHERE wn = 1
```

- [ ] **Step 2: Write `src/top_pypi_dependents/sql/audit_sample.sql`**

```sql
-- Every version published by a deterministic 1% sample of projects.
-- The build stage re-selects a winner from these with packaging.version and
-- fails the run if it disagrees with winners.sql.
SELECT DISTINCT
  LOWER(REGEXP_REPLACE(name, r'[-_.]+', '-')) AS canonical_name,
  name,
  version
FROM `bigquery-public-data.pypi.distribution_metadata`
WHERE MOD(ABS(FARM_FINGERPRINT(LOWER(REGEXP_REPLACE(name, r'[-_.]+', '-')))), 100) = 0
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_bigquery_sql.py`:

```python
import importlib.resources

import pytest

from top_pypi_dependents.sources import bigquery


def test_winners_sql_targets_the_public_table() -> None:
    assert "`bigquery-public-data.pypi.distribution_metadata`" in bigquery.WINNERS_SQL


def test_winners_sql_canonicalizes_the_same_way_packaging_does() -> None:
    assert "LOWER(REGEXP_REPLACE(name, r'[-_.]+', '-'))" in bigquery.WINNERS_SQL


def test_post_releases_are_not_classified_as_prereleases() -> None:
    """PEP 440 sorts 1.0.post1 ABOVE 1.0. Lumping `post` in with a/b/rc/dev is the
    most tempting way to get this wrong, and it makes every post-release lose to
    its own base version."""
    pre_rank_block = bigquery.WINNERS_SQL[
        bigquery.WINNERS_SQL.index("AS pre_rank") - 600 : bigquery.WINNERS_SQL.index(
            "AS pre_rank"
        )
    ]
    assert "post" not in pre_rank_block
    assert "AS post_rank" in bigquery.WINNERS_SQL
    assert "IF(pre_rank = 4 AND dev_rank = 1, 1, 0) AS is_final" in bigquery.WINNERS_SQL


def test_is_final_is_the_most_significant_sort_component() -> None:
    """Warehouse prefers any final release over any prerelease, so a 2.0.0rc1 must
    lose to a 1.9.9. That only holds if is_final leads the ORDER BY."""
    order_by = bigquery.WINNERS_SQL[bigquery.WINNERS_SQL.rindex("ORDER BY") :]
    components = [
        "is_final",
        "epoch",
        "rel1",
        "pre_rank",
        "post_rank",
        "dev_rank",
    ]
    positions = [order_by.index(name) for name in components]
    assert positions == sorted(positions), (
        f"sort components out of order: {list(zip(components, positions, strict=True))}"
    )


def test_prerelease_stages_are_ordered_not_collapsed() -> None:
    """1.0b1 must beat 1.0a2, which requires distinct ranks per stage rather than
    a single is_prerelease flag with a shared numeric tiebreak."""
    for stage, rank in (("alpha|a", 1), ("beta|b", 2), ("preview|pre|rc|c", 3)):
        assert f"THEN {rank}" in bigquery.WINNERS_SQL
        assert stage in bigquery.WINNERS_SQL


def test_audit_sql_uses_a_deterministic_sample() -> None:
    assert "FARM_FINGERPRINT" in bigquery.AUDIT_SQL
    assert ", 100) = 0" in bigquery.AUDIT_SQL


def test_sql_files_are_packaged() -> None:
    files = importlib.resources.files("top_pypi_dependents") / "sql"
    assert (files / "winners.sql").is_file()
    assert (files / "audit_sample.sql").is_file()


def test_importing_the_module_does_not_require_the_google_client() -> None:
    # The module must import cleanly without google-cloud-bigquery installed;
    # only calling extract_to_directory may need it.
    assert callable(bigquery.extract_to_directory)


def test_writes_jsonl_in_fixture_shape(tmp_path) -> None:
    rows = [
        {
            "name": "Requests",
            "canonical_name": "requests",
            "version": "2.34.2",
            "upload_time": "2026-05-14T19:25:27+00:00",
            "requires_dist": ["urllib3>=1.21.1"],
            "summary": "s",
            "requires_python": ">=3.9",
        }
    ]
    bigquery.write_winners(tmp_path, rows)
    written = (tmp_path / "winners.jsonl").read_text(encoding="utf-8").strip()
    assert '"name": "Requests"' in written
    assert written.count("\n") == 0


def test_write_audit_sample_groups_by_project(tmp_path) -> None:
    bigquery.write_audit_sample(
        tmp_path,
        [
            {"canonical_name": "requests", "name": "Requests", "version": "2.0"},
            {"canonical_name": "requests", "name": "Requests", "version": "3.0rc1"},
        ],
    )
    lines = (tmp_path / "audit_sample.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
```

- [ ] **Step 4: Run to verify it fails**

Run: `uv run pytest tests/test_bigquery_sql.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'top_pypi_dependents.sources.bigquery'`

- [ ] **Step 5: Implement**

Create `src/top_pypi_dependents/sources/bigquery.py`:

```python
"""Extract winners and the audit sample from PyPI's BigQuery metadata table."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx

TABLE = "bigquery-public-data.pypi.distribution_metadata"
SIMPLE_INDEX = "https://pypi.org/simple/"
SIMPLE_ACCEPT = "application/vnd.pypi.simple.v1+json"

_SQL = files("top_pypi_dependents") / "sql"
WINNERS_SQL = (_SQL / "winners.sql").read_text(encoding="utf-8")
AUDIT_SQL = (_SQL / "audit_sample.sql").read_text(encoding="utf-8")

EXPECTED_COLUMNS = frozenset(
    {"name", "version", "upload_time", "requires_dist", "summary", "requires_python"}
)


def write_winners(out_dir: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write winner rows as JSONL, in the shape ``FixtureSource`` reads."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "winners.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), default=str) + "\n")


def write_audit_sample(out_dir: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write the audit sample as JSONL."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "audit_sample.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps({"name": row["name"], "version": row["version"]}) + "\n"
            )


def fetch_live_names(out_dir: Path) -> int:
    """Write every live PyPI project name, one per line. Returns the count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    response = httpx.get(
        SIMPLE_INDEX, headers={"Accept": SIMPLE_ACCEPT}, timeout=120.0
    )
    response.raise_for_status()
    names = [entry["name"] for entry in response.json()["projects"]]
    (out_dir / "live_names.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
    return len(names)


def _validate_schema(client: Any, table: str) -> None:  # noqa: ANN401
    actual = {field.name for field in client.get_table(table).schema}
    missing = EXPECTED_COLUMNS - actual
    if missing:
        msg = f"{table} is missing expected column(s): {sorted(missing)}"
        raise RuntimeError(msg)


def extract_to_directory(
    out_dir: Path, *, project: str | None = None, dry_run: bool = False
) -> int:
    """Run both queries and write the three files the build stage reads."""
    from google.cloud import bigquery  # noqa: PLC0415

    client = bigquery.Client(project=project)
    _validate_schema(client, TABLE)

    if dry_run:
        config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        for label, sql in (("winners", WINNERS_SQL), ("audit", AUDIT_SQL)):
            job = client.query(sql, job_config=config)
            gib = job.total_bytes_processed / 1024**3
            print(f"{label}: {job.total_bytes_processed:,} bytes ({gib:.2f} GiB)")  # noqa: T201
        return 0

    write_winners(out_dir, (dict(row) for row in client.query(WINNERS_SQL).result()))
    write_audit_sample(out_dir, (dict(row) for row in client.query(AUDIT_SQL).result()))
    fetch_live_names(out_dir)
    return 0
```

Add the SQL directory to the packaged files in `pyproject.toml`:

```toml
[tool.uv.build-backend]
source-include = [
    "src/top_pypi_dependents/templates/*",
    "src/top_pypi_dependents/sql/*",
]
```

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/test_bigquery_sql.py -v`
Expected: all PASS.

Confirm the lazy import is actually working rather than assuming it. `uv sync` installs only the default groups, so `google-cloud-bigquery` should be absent from `.venv`:

```bash
uv sync
uv run python -c "import google.cloud.bigquery" && echo "STILL INSTALLED - lazy import unproven"
uv run pytest tests/test_bigquery_sql.py -v
```

The middle command should fail with `ModuleNotFoundError` while the test run passes. If it succeeds instead, the `bigquery` group is being installed by default — check that `uv add --group bigquery` did not add it to `dev`.

- [ ] **Step 7: Commit**

```bash
git add src/top_pypi_dependents/sources/bigquery.py src/top_pypi_dependents/sql tests/test_bigquery_sql.py pyproject.toml
git commit -m "Add BigQuery extract with PEP 440 sort key and audit sample"
```

---

### Task 10: Monthly refresh workflow and GCP setup documentation

**Files:**
- Create: `.github/workflows/refresh.yml`
- Create: `docs/gcp-setup.md`
- Create: `data/.gitkeep`

**Interfaces:**
- Consumes: the CLI from Task 8, the BigQuery source from Task 9.
- Produces: a scheduled workflow that commits `data/latest.json`, uploads release assets, and deploys Pages.

- [ ] **Step 1: Write `docs/gcp-setup.md`**

Cover, in this order, with copy-pasteable `gcloud` commands:

1. Create a GCP project and note its id. A billing account must be attached even though the expected usage falls inside the 1TB/month free query tier.
2. Enable `bigquery.googleapis.com`.
3. Create a service account with only `roles/bigquery.jobUser` on the project. It needs no dataset roles — `bigquery-public-data` is world-readable and the pipeline creates no tables.
4. Create a Workload Identity Pool and an OIDC provider for `https://token.actions.githubusercontent.com`, with an attribute condition restricting `assertion.repository` to `miketheman/top-pypi-dependents`. State plainly that omitting this condition would let any GitHub repository impersonate the service account.
5. Bind `roles/iam.workloadIdentityUser` on the service account to the pool's principal set for that repository.
6. Set the repository variables `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, and `GCP_PROJECT_ID`. These are variables, not secrets — none is a credential.
7. Local development: `gcloud auth application-default login` and `gcloud config set project <id>`.
8. First run: `uv run top-pypi-dependents extract --dry-run` and record the reported bytes in the spec's cost section.

- [ ] **Step 2: Create `data/.gitkeep`**

An empty file, so the directory exists before the first refresh writes `latest.json`.

- [ ] **Step 3: Write `.github/workflows/refresh.yml`**

```yaml
name: Refresh

on:
  schedule:
    - cron: "17 4 2 * *"  # 04:17 UTC on the 2nd of each month
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Report bytes to be scanned and stop"
        type: boolean
        default: false

concurrency:
  group: refresh
  cancel-in-progress: false

permissions: {}

jobs:
  refresh:
    name: Extract, build, publish
    runs-on: ubuntu-latest
    permissions:
      contents: write
      id-token: write
      pages: write
    outputs:
      tag: ${{ steps.stamp.outputs.tag }}
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

      - name: Install uv
        uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2
        with:
          enable-cache: true

      - name: Set up Python
        run: uv python install 3.14

      - name: Sync with the bigquery group
        run: uv sync --group bigquery

      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3
        with:
          workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}
          service_account: ${{ vars.GCP_SERVICE_ACCOUNT }}

      - name: Dry run
        if: inputs.dry_run
        run: uv run top-pypi-dependents extract --dry-run --project "${GCP_PROJECT_ID}"
        env:
          GCP_PROJECT_ID: ${{ vars.GCP_PROJECT_ID }}

      - name: Extract
        if: ${{ !inputs.dry_run }}
        run: uv run top-pypi-dependents extract --output build --project "${GCP_PROJECT_ID}"
        env:
          GCP_PROJECT_ID: ${{ vars.GCP_PROJECT_ID }}

      - name: Build
        if: ${{ !inputs.dry_run }}
        run: uv run top-pypi-dependents build --input build --database build/dependents.duckdb

      - name: Stamp the release tag
        id: stamp
        if: ${{ !inputs.dry_run }}
        run: echo "tag=data-$(date -u +%Y-%m)" >> "${GITHUB_OUTPUT}"

      - name: Artifacts
        if: ${{ !inputs.dry_run }}
        run: |
          uv run top-pypi-dependents artifacts \
            --database build/dependents.duckdb \
            --output data/latest.json \
            --edges "build/edges-$(date -u +%Y-%m).parquet"
          mv build/dependents.duckdb "build/dependents-$(date -u +%Y-%m).duckdb"

      - name: Render
        if: ${{ !inputs.dry_run }}
        run: uv run top-pypi-dependents render --payload data/latest.json --output site

      - name: Commit the ranked JSON
        if: ${{ !inputs.dry_run }}
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          if git diff --quiet -- data/latest.json; then
            echo "No ranking change; nothing to commit."
          else
            git add data/latest.json
            git commit -m "Refresh rankings for $(date -u +%Y-%m)"
            git push
          fi

      - name: Publish release assets
        if: ${{ !inputs.dry_run }}
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ steps.stamp.outputs.tag }}
        run: |
          gh release create "${TAG}" --title "${TAG}" --notes "Monthly dependency graph snapshot." \
            "build/dependents-$(date -u +%Y-%m).duckdb" \
            "build/edges-$(date -u +%Y-%m).parquet"

      - name: Upload the Pages artifact
        if: ${{ !inputs.dry_run }}
        uses: actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9 # v5.0.0
        with:
          path: site

  deploy:
    name: Deploy to GitHub Pages
    needs: refresh
    if: ${{ !inputs.dry_run }}
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    steps:
      - id: deploy
        uses: actions/deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128 # v5.0.0
```

This checkout deliberately keeps credentials, because the job pushes a commit — note that in a comment above the step so a future reader does not "fix" it by adding `persist-credentials: false`. Zizmor will flag it; add a `# zizmor: ignore[artipacked]` comment with the same reasoning.

- [ ] **Step 4: Validate the workflow syntax**

Run: `prek run --all-files`
Expected: `zizmor` passes, or flags only the documented `artipacked` case.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/refresh.yml docs/gcp-setup.md data/.gitkeep
git commit -m "Add monthly refresh workflow and GCP setup guide"
```

---

### Task 11: README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything.
- Produces: nothing code depends on.

- [ ] **Step 1: Write `README.md`**

Cover:

1. What the project answers, in one sentence: which PyPI projects the rest of PyPI depends on.
2. Links to the Pages site and to `data/latest.json`.
3. The counting rules — one release per project, runtime versus extras, PEP 503 normalization — stated compactly, with the 37% extras figure as the reason the split exists.
4. The yanked-release limitation, and that ClickHouse's mirror was rejected for staleness (link the spec rather than restating the table).
5. Local development: `make install`, `make test`, `make lint`, and a fixture-driven run:
   ```bash
   uv run top-pypi-dependents build --input tests/fixtures --database build/dev.duckdb
   uv run top-pypi-dependents artifacts --database build/dev.duckdb --output build/latest.json --limit 20
   uv run top-pypi-dependents render --payload build/latest.json --output site --tiers 5,20
   ```
6. A pointer to `docs/gcp-setup.md` for anyone wanting to run the live extract.
7. Where the graph data lives (release assets) and that visualization is intentionally out of scope for now.

- [ ] **Step 2: Verify the fixture-driven commands in the README actually run**

Run each of the three commands from step 1.5 in a clean checkout. Fix the README if any of them fails.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document the project, counting rules, and local development"
```

---

### Task 12: Push edge explosion into SQL (optional, gated on its oracle)

Do this only after Task 10 has produced at least one successful live run, so there is a known-correct reference to compare against. If the Python edge explosion is not a measurable bottleneck, skip this task and say so — the spec treats it as an optimization, not a requirement.

**Files:**
- Modify: `src/top_pypi_dependents/sql/winners.sql` — add an `edges.sql` sibling
- Create: `src/top_pypi_dependents/sql/edges.sql`
- Modify: `src/top_pypi_dependents/sources/bigquery.py`
- Test: `tests/test_edge_pushdown.py`

**Interfaces:**
- Produces: `EDGES_SQL: str`, and a `--pushdown-edges` flag on `extract` that writes `edges.jsonl` alongside `winners.jsonl`.

- [ ] **Step 1: Write the oracle test first**

The test loads a corpus of real `requires_dist` strings, runs both the SQL-derived extraction and `parse_requirement`, and asserts they agree on `dependency` and `is_runtime` for every row. Build the corpus by taking the `dependency_raw` and `marker` columns from a real `edges.parquet` produced by Task 10 and checking a sampled subset into `tests/fixtures/`.

- [ ] **Step 2: Write `edges.sql`**

Extract the target name with `REGEXP_EXTRACT(entry, r'^\s*([A-Za-z0-9._-]+)')`, canonicalize it with the same `LOWER(REGEXP_REPLACE(...))` expression the winners query uses, and set `is_runtime` from `NOT REGEXP_CONTAINS(entry, r'extra\s*==')`.

- [ ] **Step 3: Wire the flag, keeping Python as the default**

The SQL path is opt-in. Both paths stay in the codebase until the oracle has passed on a full month's data at least twice.

- [ ] **Step 4: Measure and record**

Record the before and after wall-clock of the `build` stage in `docs/gcp-setup.md`. If the saving is under 30 seconds, revert this task — the second implementation is not worth maintaining.

- [ ] **Step 5: Commit**

```bash
git add src/top_pypi_dependents/sql/edges.sql src/top_pypi_dependents/sources/bigquery.py tests/test_edge_pushdown.py
git commit -m "Add optional SQL edge explosion behind an oracle"
```

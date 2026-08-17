# top-pypi-dependents — design

Date: 2026-08-16
Status: approved, pending implementation plan

## Goal

Rank PyPI projects by how many other projects depend on them, refresh the ranking
monthly, and publish three artifacts:

1. A downloadable JSON list of the top 100,000 projects by dependent count.
2. A browsable static HTML site on GitHub Pages showing the top 100 / 1,000 / 10,000,
   with month-over-month rank movement.
3. A DuckDB database and Parquet edge export carrying the full dependency graph,
   attached to a dated GitHub Release.

The edge list is a first-class output, not a byproduct. A later project will build
network visualizations from it, so every declared dependency relationship is
persisted with its specifier, extra, and environment marker intact — even though the
headline ranking collapses them to a count.

This is repo-only tooling. Nothing is published to PyPI.

## Prior art and how this differs

- `hugovk/top-pypi-packages` ranks by download count from ClickHouse. Same cadence
  and artifact spirit, different metric and different data source.
- Warehouse's `compute_top_dependents_corpus` (`warehouse/packaging/tasks.py`) counts
  one row per (latest non-yanked release, `requires_dist` entry), grouped by
  dependency name, limited to 10,000. It caches only the counts and discards the
  relationships. This project keeps the relationships and goes to 100,000.

## Data source

### Decision: `bigquery-public-data.pypi.distribution_metadata`

PyPI publishes this table itself (docs.pypi.org/api/bigquery), CC-licensed. It is the
authoritative source for distribution metadata including `requires_dist`.

### Why not ClickHouse

The public ClickHouse service at `sql-clickhouse.clickhouse.com` exposes `pypi.projects`,
a mirror of the same BigQuery table. It requires no authentication and answers a full
top-dependents query in about 1.7 seconds. It was the original plan and it was rejected
after measurement.

The mirror has drifted. It holds 17.9M file rows across 800,510 project names, and
2.7M of those files were uploaded in 2026, so it looks current in aggregate. Per project
it is not:

| project | latest version in ClickHouse | actual latest on PyPI |
| --- | --- | --- |
| numpy | 1.14.2 (Mar 2018) | 2.5.2 |
| scipy | 2017 | current |
| boto3 | 2015 | current |
| click | 2022 | current |
| Flask | 2023 | current |
| Django, pandas, pip, pydantic, requests, ruff, setuptools, urllib3 | current | current |

Measured across the 1,000 most-downloaded projects: **265 have no metadata newer than
2025** in that table. A ranking built on it would silently read 2018-era dependency
lists for a quarter of the projects that matter most. `pypi.projects` is the only
metadata table in that database — the other 39 are download logs — so there is no
fresher alternative inside ClickHouse.

The ClickHouse path is not retained as a fallback. Maintaining two extractors doubles
the surface area for a source we have already disqualified.

### Known limitation: yanked releases

`distribution_metadata` rows are immutable and are never removed, even when a release
or project is deleted. It carries no `yanked` column. Warehouse excludes yanked
releases; this project cannot, because determining yank status requires a per-project
fetch of the PEP 691 simple index (872,447 requests). A yanked release can therefore
win version selection for its project.

Deleted *projects* are handled: a single 42MB, 1.2-second request to
`https://pypi.org/simple/` returns all 872,447 live project names, which is used to set
`projects.is_live`.

## Version selection

### What "latest" means

The latest **final** release per project, by PEP 440 ordering. When a project has only
prereleases, its highest prerelease is used instead. This mirrors Warehouse's
`ORDER BY is_prerelease ASC, _pypi_ordering DESC`.

### Where the selection happens: in BigQuery

Selection is pushed into SQL so that only ~800k winner rows leave the warehouse rather
than all ~8M `(name, version)` pairs. Measured alternatives:

- Pull every deduped `(name, version, upload_time, requires_dist)` row and select in
  Python. Exactly correct with zero SQL logic, but roughly 3GB of egress per run.
- Cap to the N most recent versions per project, then select exactly in Python.
  Measured against the ClickHouse mirror: capping at 25 keeps 4,854,615 of 8,072,261
  pairs. A 40% saving for real added complexity and a residual correctness hole.
  Rejected.
- Select in SQL, download only winners. ~60MB egress. Chosen.

### The sort key

BigQuery cannot `ORDER BY` an array, so the key is a zero-padded concatenated string.
It encodes, in order of significance:

1. `is_final` — 1 when the version has no pre-release or dev segment, else 0. Leading
   with this makes `MAX(key)` prefer any final release over any prerelease, which is
   the Warehouse behavior. It is *not* sufficient on its own; the remaining components
   still need full stage ordering to compare two prereleases of the same release.
2. `epoch` — the `N!` prefix, 0 when absent.
3. `release` — the dotted numeric segments, each `LPAD`-ed to a fixed width and
   right-padded to a fixed segment count so that `1.2` and `1.2.0` compare equal.
4. `pre_stage` — `dev` = 0, `a`/`alpha` = 1, `b`/`beta` = 2, `c`/`rc`/`pre`/`preview` = 3,
   none = 4. PEP 440 orders `dev < a < b < rc < final < post`.
5. `pre_num` — the numeric suffix of the pre-release segment.
6. `post_num` — the `.postN` value, 0 when absent.
7. `dev_num` — a `.devN` appearing after a post segment.

### The correctness guard

The SQL key is an approximation of PEP 440 and can drift as PyPI accumulates versions
that it mishandles. Rather than assert it is right, every run proves it.

The extract stage runs a second, much smaller query alongside the winners query —
BigQuery jobs return a single result set, so this is two queries rather than one job.
It returns every `(name, version)` pair for a deterministic 1% sample of projects,
selected by `MOD(ABS(FARM_FINGERPRINT(name)), 100) = 0`. That is roughly 8,000 projects
and a few megabytes, and it scans only the `name` and `version` columns. The build stage re-runs selection over that sample using
`packaging.version.parse` and compares the result to SQL's pick. **Any disagreement
fails the run.** This turns a silent correctness risk into a loud one, on every refresh,
using the real corpus rather than a fixture.

## SQL pushdown

Version selection is already server-side. The same reasoning applies to the other string
work, and the design keeps that door open rather than committing Python to it.

The boundary is drawn by whether the operation has an exact, cheap SQL equivalent:

- **Name canonicalization — already pushed down, by necessity.** Version selection has
  to group by project, and "project" means the canonical name, so the extract query
  cannot avoid computing it. PEP 503 normalization is
  `re.sub(r"[-_.]+", "-", name).lower()`, which BigQuery expresses exactly as
  `LOWER(REGEXP_REPLACE(name, r'[-_.]+', '-'))`, so there is no behavioral gap. The
  build stage re-derives `canonical_name` with `packaging.utils.canonicalize_name` for
  every winner row — all ~800k, not a sample, since it is cheap — and fails on any
  disagreement. The pushdown ships with a full oracle rather than a sampled one.
- **Dependency target extraction and the extras gate — push down where it holds up.**
  Pulling the leading name out of a `requires_dist` string and detecting an
  `extra ==` clause are both regex-shaped, and doing them in SQL would let the edge
  explosion happen server-side, returning a ready-made edge list instead of arrays to
  unpack locally.
- **Full PEP 508 marker parsing — keep in Python.** Markers nest, quote, and combine with
  `and`/`or`. A regex that appears to work on today's corpus is the kind of thing that
  breaks silently, and unlike version ordering there is no cheap sampled oracle for it.

`packaging` stays the authority in either case. Anything moved into SQL is validated the
same way version selection is: the audit sample carries the raw strings alongside the
SQL-derived values, and the build stage compares them against `packaging` on every run
and fails on disagreement. That is the precondition for each pushdown — a pushdown
without a run-time oracle does not ship.

Implementation order is Python first, pushdown second, so there is a known-correct
reference to compare against. The plan should treat each pushdown as an optimization
step gated on its oracle, not as part of the initial build.

## Cost model

Not yet measured — there is no GCP project wired up, so bytes-scanned is unknown.
The `extract` command therefore takes a `--dry-run` flag that reports BigQuery's
estimated bytes-to-be-scanned and exits without running a billable query. That number
gets recorded here once known.

Expected shape, monthly:

- Query scan: one pass over `name`, `version`, `upload_time`, `requires_dist`,
  `summary`, `requires_python` for the winners query, plus a second pass over `name`
  and `version` alone for the audit sample. Expected to land inside BigQuery's
  1TB/month free tier. To be confirmed by `--dry-run`.
- Storage Read API: ~60MB of results at $1.10/TB. Negligible.
- Network egress: ~60MB at roughly $0.12/GB. Negligible.

A billing account is required regardless of whether any charge is incurred.

### Schema validation

The exact column names and types of `distribution_metadata` are unverified — they
cannot be inspected without credentials. The ClickHouse mirror's schema is the working
assumption. The first live `--dry-run` validates it, and the extract stage fails loudly
on a schema mismatch rather than silently producing empty columns.

## Counting rules

### What counts as a dependent

`dependents` — the headline, ranked figure — counts **distinct projects whose latest
release declares an unconditional runtime dependency** on the target. An edge is
runtime when its environment marker contains no `extra ==` clause.

`dependents_all` counts distinct projects declaring *any* `requires_dist` edge,
extras included, and is reported alongside but not ranked on.

Both are `COUNT(DISTINCT dependent)`, not an edge count. A project that lists the same
target twice under different markers contributes 1, not 2.

Only live projects are ranked, **and only live projects count as dependents**. Since
`distribution_metadata` never removes rows, a project deleted from PyPI in 2019 would
otherwise keep voting for its dependencies forever, and every count would drift upward
month over month for no real reason. Their edges are still stored — they are part of the
historical graph — they just do not contribute to the ranking.

This split matters. Measured against the ClickHouse mirror, 1,004,794 of 2,723,923 edges
(37%) are extras-gated, which is why an unfiltered count puts pytest, ruff, black, and
mypy above numpy and requests. Both numbers are stored so either view is available, and
the extras breakdown survives in the edge table for later analysis.

### Name normalization

Every project name is normalized with `packaging.utils.canonicalize_name` on both sides
of every join and comparison. This is not optional: the source table stores `Django`,
not `django`, and a query filtering on the lowercase form returns nothing. The same
applies to `zope.interface` versus `zope-interface`.

### Requirement parsing

Each `requires_dist` string is parsed with `packaging.requirements.Requirement`, which
yields the target name, the version specifier, and the marker. Strings that fail to
parse are counted and reported in the snapshot summary rather than dropped silently.

Edges pointing at names that are not live PyPI projects — typos, private-index packages,
deleted projects — are kept. They are detectable by a left join against `projects` and
are excluded from the ranking, but they are part of the graph.

## Data model (DuckDB)

```sql
snapshots (
  snapshot_id       INTEGER PRIMARY KEY,
  captured_at       TIMESTAMP,
  source            VARCHAR,   -- 'bigquery-public-data.pypi.distribution_metadata'
  project_count     INTEGER,
  edge_count        INTEGER,
  unparsed_count    INTEGER
)

projects (
  snapshot_id        INTEGER,
  canonical_name     VARCHAR,  -- packaging.utils.canonicalize_name
  name               VARCHAR,  -- as declared in metadata
  latest_version     VARCHAR,
  latest_upload_time TIMESTAMP,
  summary            VARCHAR,
  requires_python    VARCHAR,
  is_live            BOOLEAN   -- present in pypi.org/simple/
)

dependencies (
  snapshot_id      INTEGER,
  dependent        VARCHAR,   -- canonical
  dependency       VARCHAR,   -- canonical
  dependency_raw   VARCHAR,   -- as written in requires_dist
  specifier        VARCHAR,   -- '>=2.0,<3'
  extra            VARCHAR,   -- comma-joined sorted gate names, NULL when unconditional
  marker           VARCHAR,   -- full PEP 508 marker text
  is_runtime       BOOLEAN
)

rankings (
  snapshot_id         INTEGER,
  canonical_name      VARCHAR,
  rank_runtime        INTEGER,
  dependents_runtime  INTEGER,
  rank_all            INTEGER,
  dependents_all      INTEGER
)
```

`dependencies` is the graph. Both endpoints are canonical names, one row per declared
edge. This is the table the future visualization work reads, and it is why the design
stores every edge rather than only the counts.

## Pipeline

Four CLI subcommands, each independently runnable and testable:

| command | reads | writes |
| --- | --- | --- |
| `extract` | BigQuery, `pypi.org/simple/` | `build/winners.parquet`, `build/audit_sample.parquet`, `build/live_names.txt` |
| `build` | those files | `build/dependents.duckdb` |
| `artifacts` | the DuckDB file, existing `data/latest.json` | `data/latest.json`, `build/edges.parquet` |
| `render` | `data/latest.json` | `site/` |

`build/winners.parquet` carries one row per project: `name`, `version`, `upload_time`,
`requires_dist`, `summary`, and `requires_python`. `build/audit_sample.parquet` carries
`(name, version)` only.

`build` is where the audit-sample check runs and where the run fails on disagreement.

`artifacts` computes month-over-month rank movement by reading the `data/latest.json`
already present in the working tree *before* overwriting it. No prior release needs to
be downloaded, and git's history of that one file becomes the monthly archive.

`render` then reads that finished JSON rather than the database. The payload already
carries `previous_rank` and `rank_change`; recomputing them from DuckDB after
`artifacts` has overwritten the file would compare the new ranking against itself and
render every row as unchanged.

## Artifacts

### `data/latest.json` (committed)

The only artifact in git. Top 100,000 by `rank_runtime`, or fewer if fewer than 100,000
projects have at least one runtime dependent. The tail is dominated by projects with a
single dependent; ties are broken by canonical name ascending so that rank assignment is
stable across runs.

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-01T00:00:00Z",
  "source": "bigquery-public-data.pypi.distribution_metadata",
  "counting": {
    "basis": "latest non-prerelease release",
    "ranked_on": "runtime"
  },
  "previous_generated_at": "2026-08-01T00:00:00Z",
  "project_count": 872447,
  "edge_count": 2723923,
  "rows": [
    {
      "rank": 1,
      "project": "requests",
      "dependents": 66743,
      "dependents_all": 71204,
      "previous_rank": 2,
      "rank_change": 1
    }
  ]
}
```

`previous_rank` and `rank_change` are null for projects new to the list.

### Release assets (not committed)

Attached to a dated GitHub Release each month:

- `dependents-YYYY-MM.duckdb` — the full database, all four tables.
- `edges-YYYY-MM.parquet` — the `dependencies` table alone, for anyone who wants the
  graph without DuckDB.

## Site

Static HTML generated with Jinja2. Three pages — top 100, top 1,000, top 10,000 —
plus an index explaining the methodology, the extras split, and the yanked-release
limitation. Rank movement renders as ▲/▼ with the delta.

A client-side filter box searches the rows already embedded in the page. No JS
framework, no bundler, no build step beyond Python. Deployed with
`actions/deploy-pages`.

## Tooling

Following the conventions in `miketheman/pytest-socket`, modernized:

- `uv` for dependency management and the build backend (`uv_build`), src layout.
- `ruff` for linting and formatting (replaces flake8, isort, black, pyupgrade).
- `ty` for type checking (replaces mypy).
- `prek` for git hooks, reading `.pre-commit-config.yaml`.
- `.editorconfig` plus `editorconfig-checker`.
- `pytest` with `coverage[toml]`, branch coverage on.
- `zizmor` for GitHub Actions security linting.
- `dependabot` for github-actions and uv, weekly, with cooldown.
- Python 3.14.

Dropped relative to pytest-socket: publish workflow, PyPI classifiers, CHANGELOG,
codspeed, mutmut, vulture, and the codeclimate config.

## Repo layout

```
.editorconfig
.gitignore
.pre-commit-config.yaml
.python-version                    # 3.14
LICENSE                            # MIT
Makefile
README.md
pyproject.toml
.github/
  dependabot.yml
  workflows/ci.yml                 # ruff, ty, pytest + coverage
  workflows/refresh.yml            # monthly cron
  workflows/zizmor.yml
data/
  latest.json
docs/
  gcp-setup.md
  superpowers/specs/
src/top_pypi_dependents/
  __init__.py
  __main__.py
  cli.py
  sources/
    base.py                        # MetadataSource protocol
    bigquery.py
    fixture.py
  normalize.py                     # canonicalize_name, requirement parsing
  versions.py                      # PEP 440 selection + audit comparison
  warehouse.py                     # DuckDB schema, load, ranking SQL
  artifacts.py                     # JSON emitters, rank deltas
  render.py                        # Jinja2 → site/
  sql/                             # winners.sql, audit_sample.sql
  templates/                       # base/index/table .html.j2
tests/
```

## Credentials

GCP is not set up yet. `sources/base.py` defines a `MetadataSource` protocol;
`sources/fixture.py` implements it against checked-in Parquet. Everything except the
live extract is buildable, testable, and reviewable before any GCP work happens.
`sources/bigquery.py` implements the same protocol and stays unexercised by CI until
credentials exist.

`docs/gcp-setup.md` covers Workload Identity Federation — GitHub Actions OIDC to GCP,
no long-lived key in repo secrets. Local development uses
`gcloud auth application-default login`.

## Testing

No network. Tests run with `pytest-socket` enabled, which also guarantees the BigQuery
client is never invoked accidentally.

- **Fixture corpus.** Checked-in JSONL rather than Parquet — the corpus is tiny and a
  text fixture stays reviewable in diffs. It covers PEP 440 epochs, `.post`, `.dev`,
  prerelease-only projects, `Django` vs `django`, `zope.interface` vs `zope-interface`,
  extras-gated markers, unparseable requirement strings, and edges pointing at
  nonexistent projects.
- **Version selection oracle.** Property-based comparison of the selection logic
  against `packaging.version.parse` over generated version sets.
- **Audit-check behavior.** A test proving the build stage *fails* when the SQL winner
  and the `packaging` winner disagree — the guard itself needs a failing case.
- **Counting rules.** Runtime versus all-edges counts on the fixture, verified against
  hand-computed expectations.
- **Rank deltas.** Two successive `artifacts` runs over different snapshots, asserting
  `previous_rank`, `rank_change`, and null handling for newly-appearing projects.
- **Golden tests.** JSON schema shape and rendered HTML.

## Out of scope

Deliberately deferred, with the data model sized to accommodate them later:

- Network and dependency-graph visualization.
- Transitive dependency resolution.
- Blending download counts with dependent counts.
- Per-extra breakdown reporting (the data is stored; no artifact exposes it yet).
- Backfilling historical months from earlier `distribution_metadata` states.

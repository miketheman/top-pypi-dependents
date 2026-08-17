# top-pypi-dependents

Which PyPI projects does the rest of PyPI actually depend on? This ranks projects
by how many other live projects declare a dependency on them, refreshed monthly
from PyPI's own BigQuery metadata table.

- Site: <https://miketheman.github.io/top-pypi-dependents/>
- Ranked JSON: <https://miketheman.github.io/top-pypi-dependents/latest.json> to
  read, or [`latest.min.json`](https://miketheman.github.io/top-pypi-dependents/latest.min.json)
  to fetch — same payload, published by the same monthly run
- Full dependency graph: a DuckDB database and a Parquet edge list on each
  [release](https://github.com/miketheman/top-pypi-dependents/releases)

`data/latest.json` is also committed each month, but it exists to give the next
run something to compute rank movement against. Prefer the published URLs above:
they are not tied to the commit history.

This is repo-only tooling. It is not published to PyPI.

## Counting rules

- **One release per project.** The latest final release by PEP 440 ordering, or the
  latest prerelease for projects that have only prereleases.
- **Runtime versus extras.** The headline, ranked count (`dependents`) includes only
  edges whose environment marker has no `extra ==` clause — an unconditional runtime
  dependency. `dependents_all` counts every declared edge, extras included, and is
  reported alongside but not ranked on. The split exists because roughly 37% of all
  declared dependency edges are extras-gated; without it, pytest, ruff, black, and
  mypy would outrank numpy and requests.
- **PEP 503 normalization.** Every project name is canonicalized before comparison,
  so `Django`/`django` and `zope.interface`/`zope-interface` are the same project.
- **Only live projects count.** Both as ranked targets and as dependents. The source
  table never removes a deleted project's rows, so without this filter a project
  gone from PyPI since 2019 would keep voting for its dependencies forever.

## Known limitations

- **Yanked releases cannot be excluded.** The source table carries no yank status,
  and determining it would mean one request per project — 872,447 of them. A yanked
  release can therefore win version selection.
- **ClickHouse was evaluated and rejected as a data source, for staleness.** Its
  public PyPI mirror looked current in aggregate but was badly stale per project —
  265 of the 1,000 most-downloaded projects had no metadata newer than 2025, and
  numpy's latest version there was 1.14.2 from March 2018. Details and the full
  comparison table are in
  [the design spec](docs/superpowers/specs/2026-08-16-top-pypi-dependents-design.md#why-not-clickhouse).
- Transitive dependencies are not resolved; only declared, direct edges count.

## Pipeline

Four subcommands, each independently runnable:

| command | needs GCP credentials | reads | writes |
| --- | --- | --- | --- |
| `extract` | yes | BigQuery, `pypi.org/simple/` | winner/audit JSONL |
| `build` | no | extracted data | a DuckDB database |
| `artifacts` | no | the database | `data/latest.json`, an edge export |
| `render` | no | `data/latest.json` | a static site, including both JSON copies |

Only `extract` touches BigQuery. `build`, `artifacts`, and `render` run against
either extracted data or checked-in test fixtures, so most of the pipeline is
exercisable from a plain checkout with no credentials at all.

## Local development

```bash
make install   # uv sync
make test      # coverage run + report
make lint      # ruff format --check, ruff check, ty check
```

To run the pipeline end to end against the fixture corpus in `tests/fixtures`,
without any GCP credentials:

```bash
mkdir -p build
uv run top-pypi-dependents build --input tests/fixtures --database build/dev.duckdb \
  --min-projects 1 --min-audit-sample 1
uv run top-pypi-dependents artifacts --database build/dev.duckdb --output build/latest.json \
  --limit 20 --min-dependents 1
uv run top-pypi-dependents render --payload build/latest.json --output site --tiers 5,20
```

`build` refuses a corpus smaller than half a million projects, because a real run
never sees one and a truncated extract must not overwrite a good ranking. The two
`--min-*` flags lower that floor for the 16-project fixture corpus; a production
run passes neither.

`artifacts` defaults to `--min-dependents 2`, dropping the single-dependent tail
that was 54% of the first real payload's rows. The fixture ranks three projects,
two of them singletons, so a fixture run passes `--min-dependents 1` to see
anything at all.

Every stage logs its progress and outcome to stderr, leaving stdout for results.
`--log-level debug` adds row and byte counters inside the long loops:

```bash
uv run top-pypi-dependents --log-level debug build --input tests/fixtures ...
```

That leaves a rendered site under `site/` and a ranked JSON under `build/`,
neither of which is committed.

To run `extract` for real, against live BigQuery data, see
[`docs/gcp-setup.md`](docs/gcp-setup.md) for the credentials and GCP project setup
it needs.

## Graph data

The full dependency graph — every declared edge, not just the ranking — is
published as a DuckDB database and a Parquet edge export, attached to a dated
[GitHub Release](https://github.com/miketheman/top-pypi-dependents/releases) each
month. Network and dependency-graph visualization from that data is intentionally
out of scope for now.

## License

MIT, see [LICENSE](LICENSE).

# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

`top-pypi-dependents` ranks PyPI projects by how many other projects depend on
them. Once a month an unattended GitHub Actions job reads PyPI's BigQuery
metadata table, picks one release per project, explodes `requires_dist` into a
dependency graph in DuckDB, and publishes three things: a ranked JSON committed
to this repo, a static site on GitHub Pages, and a DuckDB database plus Parquet
edge list attached to a dated Release.

Repo-only tooling. Nothing here is published to PyPI.

The design reasoning — why BigQuery rather than the ClickHouse mirror, what
counts as a dependent, which alternatives were rejected — lives in
`docs/superpowers/specs/2026-08-16-top-pypi-dependents-design.md`. Read it before
changing how anything is counted.

## Status: live

The whole pipeline ran end to end against live BigQuery on 2026-08-17 and
published everything it is meant to publish. The monthly cron (`17 4 2 * *`)
now runs unattended.

First real run, snapshot 1:

```
1,003,087 projects, 3,721,326 edges, 374 unparsed requirements, 3 audit-skipped
```

What that settled, none of which a fixture or a dry run could:

- **The audit oracle did not fire.** Version selection pushed into BigQuery SQL
  agrees with `packaging` on the 1% resample.
- **The floors are calibrated.** 1,003,087 winners against a `MIN_WINNERS` of
  500,000 — about 2× headroom, tight enough to mean something and loose enough
  not to false-alarm.
- **`fetch_live_names` survives the real `/simple/` endpoint**, well under its
  300 MB cap.
- Parsing is clean: 374 unparsed out of 3.72M edges, 0.01%.
- A run scans ~8.33 GiB (winners 7.80, audit 0.53) — ~16% of the 50 GiB
  `MAX_BYTES_BILLED` cap and inside BigQuery's 1 TiB/month free tier, so the
  monthly job costs effectively nothing. Re-check as the upstream table grows.
- Workload identity federation works from Actions: OIDC mint, STS exchange,
  service account impersonation, BigQuery job.

Published by that run: commit `8eba00e` (`data/latest.json`), release
`data-2026-08` (DuckDB 208 MB, Parquet 36.5 MB), and the site at
<https://miketheman.github.io/top-pypi-dependents/>.

Repository state: private, personally owned. Pages is enabled with
`build_type: workflow`, and the **site is public even though the repo is not** —
that is deliberate. Code scanning is unavailable on a private personal repo, so
`zizmor.yml` reports findings as annotations rather than SARIF; if this repo
ever goes public, switch `advanced-security` back to `true`.

Two things still unexercised:

1. **Rank movement.** `_deltas_baseline` has never had a prior month to compare
   against. Month two is its first real test, and it is also the first run that
   can trip `_check_top_row_has_not_collapsed`.
2. **A second snapshot in the same database.** Every run so far wrote snapshot
   1 into an empty file.

Known wart: `_build` always constructs a `FixtureSource`, because `extract`
writes the JSONL that `build` reads — the class name describes the format, not
the provenance. The consequence is that the published payload reports
`"source": "fixture"` for data that came from BigQuery. Harmless internally,
misleading in a public artifact.

## Traps that have already cost time

Each of these looks like a bug or a cleanup opportunity and is not. Two of them
were nearly "fixed" into breakage by previous reviews.

**`pytz` is not unused.** Nothing imports it because DuckDB imports it
internally, and without it *every* `TIMESTAMPTZ` read raises
`InvalidInputException: Required module 'pytz' failed to import`. That breaks the
whole artifacts stage, and only on a fresh resolve — a warm venv hides it. It has
been proposed for removal twice. Leave it.

**`google-cloud-bigquery` and `urllib3` are imported lazily, inside functions.**
They live in the `bigquery` dependency group, and CI runs `uv sync` *without*
that group specifically to prove every other subcommand still imports. Hoisting
either to module scope breaks CI for every stage except `extract`.

**`[tool.pytest]` is correct.** pytest 9 reads that table directly;
`[tool.pytest.ini_options]` is the older workaround name. Not a misconfiguration.

**The `# ty: ignore[unresolved-import]` comments in `sources/bigquery.py` are
conditionally necessary** — required without the `bigquery` group, redundant with
it. That is why `[tool.ty.rules] unused-ignore-comment = "ignore"` exists; without
it `make lint` passes or fails depending on which optional group happens to be
synced.

**The audit oracle aborting a run is working as designed.** Version selection is
pushed into BigQuery SQL, and `versions.audit()` re-checks a 1% sample against
`packaging` on every run. If it raises, the SQL sort key has drifted — do not
suppress it.

**Fixture-driven runs need the floors relaxed.** `build` refuses implausibly
small inputs, so local fixture runs pass `--min-projects 1 --min-audit-sample 1`.
Production passes neither flag and gets the real floors. `artifacts` needs the
same treatment for a different reason: it defaults to `--min-dependents 2`, and
the fixture ranks three projects, two of them with a single dependent — so a
fixture run without `--min-dependents 1` renders one row and looks broken.

**`FixtureSource` is not only for fixtures.** It names the JSONL layout, and
`extract` writes exactly that layout from BigQuery, so it serves production too.
It reports provenance from the `source.txt` that `extract` writes beside the
data; without that file it answers "fixture", which is how the first real run
published a payload and a footer claiming BigQuery data came from a fixture.

**`git diff --quiet -- <untracked path>` exits 0.** That silently made the
workflow never commit its own artifact. The commit check stages first and tests
`--cached`.

## Things that are easy to get wrong

**`sql/winners.sql` version ordering.** Three traps, each producing a plausible
query that is silently wrong. A post-release is a *final* release, so `post` must
never appear in the pre-release ranking. `is_final` must lead the `ORDER BY`, or
`2.0.0rc1` beats `1.9.9`. Prerelease stages need distinct ranks, or `1.0a2` beats
`1.0b1`. A leading `v` is stripped, because `packaging` normalizes `v1.0` to `1.0`
while a digit-anchored regex would not. `tests/test_bigquery_sql.py` guards all
three — if you change that query, those tests are the safety net.

**DuckDB specifics.** A bound parameter cannot be a `COPY ... TO` target, so the
path is interpolated with quotes doubled. `TIMESTAMPTZ` localizes to the session
timezone on fetch, so timestamps are normalized with `.astimezone(UTC)` before
formatting. `executemany` runs about 1,400 rows/sec against a columnar engine;
inserts go through Arrow instead, which measured 66x faster.

**`render_site` takes the payload dict, not a connection.** `artifacts`
overwrites `data/latest.json` to compute rank deltas, so recomputing them from
DuckDB afterwards would compare the new ranking against itself and render every
row as unchanged.

**Names are canonicalized everywhere.** The source table stores `Django`, not
`django`. A lowercase-only comparison returns nothing.

## Commands

```bash
make install          # uv sync
make lint             # ruff format --check, ruff check, ty check
make test             # coverage run -m pytest, then coverage report
prek run --all-files  # the git-hook gate; must pass on a clean clone
```

The suite differs by dependency group, and both arms run in CI:

```bash
uv sync                        # 123 passed, 3 skipped
uv sync --group bigquery       # 126 passed, 0 skipped
```

The three skips are the `fetch_live_names` tests, which need `urllib3` from the
`bigquery` group.

Full pipeline against the fixture, no credentials needed:

```bash
uv run top-pypi-dependents build --input tests/fixtures --database build/dev.duckdb \
    --min-projects 1 --min-audit-sample 1
uv run top-pypi-dependents artifacts --database build/dev.duckdb \
    --output build/latest.json --limit 20 --min-dependents 1
uv run top-pypi-dependents render --payload build/latest.json --output site --tiers 5,20
```

Every stage logs progress and outcomes to stderr; stdout stays the result.
`--log-level debug` (global, before the subcommand) adds row and byte counters
inside the long loops.

`render` also writes `latest.json` and `latest.min.json` into the site output.
That is what the site serves, and `site.yml` republishes it from the committed
`data/latest.json` without touching BigQuery whenever a template changes.

## Conventions

- Commit messages: plain imperative subject, no `feat:` prefix, no scope tag.
  End with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` and nothing
  else — session links were deliberately stripped from this history.
- Do not commit or push unless asked.
- `data/` is deliberately **not** gitignored; `data/latest.json` is the one
  committed artifact.
- `select = ["ALL"]` in ruff. Fix the code rather than adding an ignore; if an
  ignore is genuinely unavoidable, give it a trailing comment saying why.
- Every GitHub Action is pinned to a full commit SHA with a `# vX.Y.Z` comment.
- Tests never touch the network — `pytest-socket` is enabled globally.

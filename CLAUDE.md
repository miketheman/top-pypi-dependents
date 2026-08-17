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

## Status: dry-run verified, never run for real

GCP project `top-pypi-dependents` is wired up, and a local `extract --dry-run`
has reached the live table. No query has ever been billed, no data has been
written, and `data/latest.json` still does not exist. The build, artifacts and
render stages are still only exercised against the checked-in JSONL fixture in
`tests/fixtures/`.

What the local dry run proved, on 2026-08-17:

- `_validate_schema` passes against the real table, so
  `bigquery-public-data.pypi.distribution_metadata.requires_dist` really is
  `ARRAY<STRING>` rather than merely assumed to be.
- Both queries compile server-side.
- One run scans ~8.33 GiB (winners 7.80, audit 0.53). That is ~16% of the
  50 GiB `MAX_BYTES_BILLED` cap, and inside BigQuery's 1 TiB/month free tier,
  so a monthly job costs effectively nothing. Re-check the headroom as the
  upstream table grows.

It proved nothing about the CI path: it used local end-user ADC, not the
workload identity federation the workflow authenticates with.

Remaining to go live, in order:

1. ~~Repository *variables* (not secrets — none is a credential)
   `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, `GCP_PROJECT_ID`.~~
   Done 2026-08-17, per `docs/gcp-setup.md`.
2. `workflow_dispatch` with `dry_run: true`. The local dry run does not cover
   this: what is still unproven is workload identity federation, i.e. whether
   the provider, service account and repo binding actually let the runner mint
   a token.
3. First real run, watched. Three things only a real run can prove: whether the
   audit oracle fires, whether the plausibility floors are calibrated, and
   whether `fetch_live_names` survives the real `/simple/` endpoint — a dry run
   never calls it.
4. Seed `data/latest.json` once, so month two has something to compute rank
   movement against.
5. Enable GitHub Pages — the deploy job assumes a `github-pages` environment.
   Pages on a private repo needs a paid plan; this one is private and
   personally owned, so this step may force a plan or visibility decision.

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
Production passes neither flag and gets the real floors.

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
uv sync                        # 103 passed, 3 skipped
uv sync --group bigquery       # 106 passed, 0 skipped
```

The three skips are the `fetch_live_names` tests, which need `urllib3` from the
`bigquery` group.

Full pipeline against the fixture, no credentials needed:

```bash
uv run top-pypi-dependents build --input tests/fixtures --database build/dev.duckdb \
    --min-projects 1 --min-audit-sample 1
uv run top-pypi-dependents artifacts --database build/dev.duckdb \
    --output build/latest.json --limit 20
uv run top-pypi-dependents render --payload build/latest.json --output site --tiers 5,20
```

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

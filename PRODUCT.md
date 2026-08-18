# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary: PyPI and Python-ecosystem stewards.** People who carry some
responsibility for the index or the ecosystem around it, and who arrive with a
question of consequence rather than curiosity — what is the blast radius if this
project breaks, which projects deserve security or infrastructure attention
first, what does the ecosystem actually rest on. They are technically fluent,
skeptical of a number without a stated method, and likely to check the counting
rules before they quote the ranking.

**Secondary: people who take the data elsewhere.** Analysts, researchers, and
tool builders who treat the site as a doorway and leave with `latest.json`, the
Parquet edge list, or the DuckDB database. Their session may never involve the
rankings table at all.

Both audiences read the method before they trust the number. Neither needs
persuading that dependency counts matter.

## Product Purpose

Answer, with a defensible method, which PyPI projects the rest of PyPI depends
on — and publish both the answer and the graph it came from.

Success is other people building on the data: the Parquet and DuckDB releases
pulled into analyses and tools that this project never anticipated. A ranking
nobody can reproduce or extend has failed even if it is correct.

## Positioning

Most "top PyPI packages" lists rank by downloads. This ranks by declared
dependency, and the mechanism is the claim:

- **The extras split.** The headline count includes only unconditional runtime
  dependencies. Roughly 37% of all declared edges are gated behind an `extra`,
  and counting them puts pytest, ruff, black, and mypy above numpy and requests.
  `dependents_all` is reported beside the headline but never ranked on.
- **Liveness on both endpoints.** The source table never removes a deleted
  project's rows, so a project gone from PyPI since 2019 would otherwise keep
  voting for its dependencies forever.
- **One release per project**, chosen by PEP 440 ordering, with prerelease
  fallback — audited on every run against `packaging` on a 1% resample.
- **The graph ships too.** The ranking is a view of a published edge list, not a
  number to take on faith.

A neighboring product could copy the ranking. It could not truthfully copy the
method statement without doing the same work.

## Operating Context

- Unattended monthly GitHub Actions run (`17 4 2 * *`) reads PyPI's BigQuery
  metadata table and republishes everything. No human is in the loop on a
  normal month.
- Three published outputs: a static site on GitHub Pages, `latest.json` /
  `latest.min.json` at stable URLs, and a dated GitHub Release carrying a
  DuckDB database plus a Parquet edge export.
- Readers arrive by link or from GitHub, usually already holding a question.
  Data consumers may hit only the JSON URL or the release assets.
- The graph is queried in a DuckDB CLI, not in the browser. The site teaches
  that query rather than trying to be the query tool.

## Capabilities and Constraints

- Four independently runnable stages: `extract` (the only one touching
  BigQuery), `build`, `artifacts`, `render`. Everything but `extract` runs from
  a plain checkout with no credentials.
- The site is fully static and **self-contained**: no external font, script, or
  asset fetches. It is rendered from Jinja templates at publish time and served
  as flat files.
- The rankings page lists a slice of the ranking, all of it visible. Search
  looks past the page, answering from a published index of every ranked project —
  a project you can name should be findable whether or not it was rendered.
- Known limits that must stay stated, not smoothed over: yanked releases cannot
  be excluded (the source table carries no yank status); only declared, direct
  edges count, with no transitive resolution.
- Terminology to keep consistent: *dependents* (unconditional runtime, the
  ranked number), *incl. extras* / `dependents_all`, *edge*, *snapshot*,
  *live project*.
- Not published to PyPI. Repo-only tooling.
- Undecided: whether the ranking ever gains per-project detail pages, and
  whether graph visualization belongs on the site at all.

## Brand Commitments

- The visual world is inherited from miketheman.dev — the "herbarium sheet"
  system of warm paper and ink, a single green accent, hairline rules, and no
  shadows. **Inherited, not locked:** coherence with the personal site matters,
  and this site may diverge where its own needs differ. It is not required to
  track changes upstream.
- One serif on the page, tracked caps for anything secondary, tabular numerals
  in every numeric column.
- Voice, as shipped: plain, exact, and willing to state a limitation in the
  same breath as a result. No marketing register.
- Fonts are declared as the .dev stacks but never fetched; self-containment
  outranks typographic fidelity.

## Evidence on Hand

Real, and unusually specific — this project's proof is its own output.

- `data/latest.json`, committed each month; the same payload at the published
  URLs.
- First production run (2026-08-17, snapshot 1): 1,003,087 projects,
  3,721,326 edges, 374 unparsed requirements (0.01%), ~8.33 GiB scanned.
- Release `data-2026-08`: DuckDB 208 MB, Parquet 36.5 MB.
- The rejection of the ClickHouse mirror is documented with a comparison table
  in `docs/superpowers/specs/2026-08-16-top-pypi-dependents-design.md`.
- The JSON shape shown on the site is generated from the payload the page was
  built from, so it cannot drift from what is served.

**Absent, and not to be invented:** testimonials, named users, adoption or
traffic figures, citations by other projects, funding or partnership claims.
Nobody has been quoted about this and no downstream use is known yet.

## Product Principles

1. **State the method beside the number.** Every headline figure carries its
   counting rule within reach; a limitation is published, never buried.
2. **The graph is the product; the ranking is a view of it.** Anything that
   makes the underlying data easier to leave with outranks anything that only
   decorates the table.
3. **Self-contained and static.** No runtime dependency on a service that can
   go down, expire, or start tracking readers.
4. **Reproducible or it does not ship.** Published numbers must be
   regenerable from a documented pipeline by someone who is not the author.
5. **Unattended by design.** The monthly run assumes nobody is watching;
   features that need a human in the loop are the wrong features.

## Accessibility & Inclusion

No external standard has been set by the user. Product-specific requirements
already established in the shipped site, which future work must preserve:

- Search results must be announced, not only rendered: the page carries a polite
  live region reporting how many projects matched, and a visible hint stating
  that the filter reaches past the rows on the page.
- Full keyboard operability with a visible focus ring in the accent color.
- Light and dark are both first-class, driven by `prefers-color-scheme`;
  `prefers-reduced-motion` zeroes all transition durations.

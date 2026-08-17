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
--
-- PEP 440 permits an optional leading `v`/`V` (`v1.0`, which `packaging` normalizes
-- to `1.0`); it is stripped before epoch/release/suffix extraction below, otherwise
-- `^\d+`-anchored regexes never match and every v-prefixed version silently falls
-- back to release 0 / is_final 1, colliding and resolving by lexicographic order.
WITH parsed AS (
  SELECT
    name,
    version,
    upload_time,
    filename,
    requires_dist,
    summary,
    requires_python,
    LOWER(REGEXP_REPLACE(name, r'[-_.]+', '-')) AS canonical_name,
    REGEXP_REPLACE(version, r'^[vV]', '') AS version_no_v
  FROM `bigquery-public-data.pypi.distribution_metadata`
),
epoched AS (
  SELECT
    *,
    IFNULL(SAFE_CAST(REGEXP_EXTRACT(version_no_v, r'^(\d+)!') AS INT64), 0) AS epoch,
    IFNULL(
      REGEXP_EXTRACT(REGEXP_REPLACE(version_no_v, r'^\d+!', ''), r'^(\d+(?:\.\d+)*)'), '0'
    ) AS release_seg,
    IFNULL(
      REGEXP_EXTRACT(REGEXP_REPLACE(version_no_v, r'^\d+!', ''), r'^\d+(?:\.\d+)*(.*)$'), ''
    ) AS suffix
  FROM parsed
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
  FROM epoched
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
-- `filename DESC` is a deterministic secondary key -- an sdist and a wheel of the
-- same release can share an `upload_time`, and without a tiebreak the pick (and its
-- requires_dist) would flap between runs with no upstream change.
per_version AS (
  SELECT * EXCEPT (rn) FROM (
    SELECT
      *,
      ROW_NUMBER() OVER (
        PARTITION BY canonical_name, version ORDER BY upload_time DESC, filename DESC
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

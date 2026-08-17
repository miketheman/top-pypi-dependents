-- Every version published by a deterministic 1% sample of projects.
-- The build stage re-selects a winner from these with packaging.version and
-- fails the run if it disagrees with winners.sql.
SELECT DISTINCT
  LOWER(REGEXP_REPLACE(name, r'[-_.]+', '-')) AS canonical_name,
  name,
  version
FROM `bigquery-public-data.pypi.distribution_metadata`
WHERE MOD(ABS(FARM_FINGERPRINT(LOWER(REGEXP_REPLACE(name, r'[-_.]+', '-')))), 100) = 0

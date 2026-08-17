import importlib.resources
from typing import TYPE_CHECKING

from top_pypi_dependents.sources import bigquery

if TYPE_CHECKING:
    from pathlib import Path


def test_winners_sql_targets_the_public_table() -> None:
    assert "`bigquery-public-data.pypi.distribution_metadata`" in bigquery.WINNERS_SQL


def test_winners_sql_canonicalizes_the_same_way_packaging_does() -> None:
    assert "LOWER(REGEXP_REPLACE(name, r'[-_.]+', '-'))" in bigquery.WINNERS_SQL


def test_post_releases_are_not_classified_as_prereleases() -> None:
    """PEP 440 sorts 1.0.post1 ABOVE 1.0. Lumping `post` in with a/b/rc/dev is the
    most tempting way to get this wrong, and it makes every post-release lose to
    its own base version."""
    head, _, tail = bigquery.WINNERS_SQL.partition("END AS pre_rank")
    pre_rank_block = head[head.rindex("CASE") :]
    assert tail, "expected an 'END AS pre_rank' marker in winners.sql"
    assert "post" not in pre_rank_block.lower()
    assert "AS post_rank" in bigquery.WINNERS_SQL
    assert "IF(pre_rank = 4 AND dev_rank = 1, 1, 0) AS is_final" in bigquery.WINNERS_SQL


def test_is_final_is_the_most_significant_sort_component() -> None:
    """Warehouse prefers any final release over any prerelease, so a 2.0.0rc1 must
    lose to a 1.9.9. That only holds if is_final leads the ORDER BY, and leads it
    in the DESCENDING direction -- checking positions alone would still pass with
    every component flipped to ASC, which silently picks the worst release for
    every project on PyPI."""
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
    assert "ASC" not in order_by, "every sort component must be descending"
    # is_final, epoch, rel1..rel6, pre_rank, pre_num, post_rank, post_num,
    # dev_rank, dev_num, version: one DESC per column, none silently dropped.
    sort_columns = [
        "is_final",
        "epoch",
        "rel1",
        "rel2",
        "rel3",
        "rel4",
        "rel5",
        "rel6",
        "pre_rank",
        "pre_num",
        "post_rank",
        "post_num",
        "dev_rank",
        "dev_num",
        "version",
    ]
    assert order_by.count("DESC") == len(sort_columns)


def test_prerelease_stages_are_ordered_not_collapsed() -> None:
    """1.0b1 must beat 1.0a2, which requires distinct ranks per stage rather than
    a single is_prerelease flag with a shared numeric tiebreak. The stage token and
    its rank must appear on the SAME line -- checking them as independent facts
    would still pass with the alpha/beta ranks swapped."""
    head, _, tail = bigquery.WINNERS_SQL.partition("END AS pre_rank")
    assert tail, "expected an 'END AS pre_rank' marker in winners.sql"
    pre_rank_lines = head[head.rindex("CASE") :].splitlines()
    for stage, rank in (("alpha|a", 1), ("beta|b", 2), ("preview|pre|rc|c", 3)):
        matches = [
            line for line in pre_rank_lines if stage in line and f"THEN {rank}" in line
        ]
        assert matches, f"expected a line pairing {stage!r} with 'THEN {rank}'"


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


def test_writes_jsonl_in_fixture_shape(tmp_path: Path) -> None:
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


def test_write_audit_sample_groups_by_project(tmp_path: Path) -> None:
    bigquery.write_audit_sample(
        tmp_path,
        [
            {"canonical_name": "requests", "name": "Requests", "version": "2.0"},
            {"canonical_name": "requests", "name": "Requests", "version": "3.0rc1"},
        ],
    )
    lines = (tmp_path / "audit_sample.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

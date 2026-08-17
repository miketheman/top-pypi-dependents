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


def test_audit_treats_pep440_equal_picks_as_agreement() -> None:
    sample = {"alpha": ["1.2", "1.2.0"]}
    assert audit(sample, {"alpha": "1.2"}) == []
    assert audit(sample, {"alpha": "1.2.0"}) == []


def test_audit_reports_a_disagreement_when_sql_pick_is_unparseable() -> None:
    sample = {"a": ["1.0"]}
    assert audit(sample, {"a": "not-a-version"}) == [
        Disagreement(project="a", sql_pick="not-a-version", packaging_pick="1.0")
    ]

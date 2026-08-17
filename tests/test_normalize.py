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

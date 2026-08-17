import top_pypi_dependents


def test_package_exposes_a_version() -> None:
    assert isinstance(top_pypi_dependents.__version__, str)
    assert top_pypi_dependents.__version__

"""Rank PyPI projects by how many other projects depend on them."""

from importlib.metadata import version

__version__ = version("top-pypi-dependents")

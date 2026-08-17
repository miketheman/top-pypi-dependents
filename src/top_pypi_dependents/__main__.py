"""Allow ``python -m top_pypi_dependents``."""

import sys

from top_pypi_dependents.cli import main

if __name__ == "__main__":
    sys.exit(main())

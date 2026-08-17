.PHONY: all clean install lint format test hooks

INSTALL_STAMP := .install.stamp
UV := $(shell command -v uv 2> /dev/null)

all: lint test

clean:
	@rm -rf $(INSTALL_STAMP) .coverage .pytest_cache/ .ruff_cache/ build/ site/ .venv/

install: $(INSTALL_STAMP)
$(INSTALL_STAMP): pyproject.toml uv.lock
ifndef UV
	$(error "uv is not available, please install it first.")
endif
	@uv sync
	@touch $(INSTALL_STAMP)

lint: $(INSTALL_STAMP)
	@uv run ruff format --check
	@uv run ruff check
	@uv run ty check src tests

format: $(INSTALL_STAMP)
	@uv run ruff format
	@uv run ruff check --fix

test: $(INSTALL_STAMP)
	@uv run coverage run -m pytest ; uv run coverage report

hooks:
	@prek run --all-files

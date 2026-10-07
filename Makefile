.PHONY: all clean install lint format test hooks site serve

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
	@uv run ty check src tests scripts
	@biome ci

format: $(INSTALL_STAMP)
	@uv run ruff format
	@uv run ruff check --fix
	@biome check --write

test: $(INSTALL_STAMP)
	@uv run coverage run -m pytest ; status=$$? ; uv run coverage report ; exit $$status
	@npm test --silent

hooks:
	@prek run --all-files

# The graph is optional, as it is in the workflows: a month whose graph step
# failed publishes the site without the cascade page.
SITE_OUT ?= site
site: $(INSTALL_STAMP)
	@uv run top-pypi-dependents render --payload data/latest.json \
		$(if $(wildcard data/graph.json),--graph data/graph.json) --output $(SITE_OUT)

serve: $(INSTALL_STAMP)
	@uv run python scripts/serve.py

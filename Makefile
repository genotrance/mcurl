export TMP ?= /tmp

# After install, LD_LIBRARY_PATH must include the jbb LibCURL lib directory
# for local test runs. Run: eval $(make env) to set it in your shell.
.PHONY: install
install: ## Install the virtual environment, build extension, and install pre-commit hooks
	@TMP=$(TMP) uv sync
	@TMP=$(TMP) uv pip install -e .
	@uv run python -m pre_commit install

.PHONY: env
env: ## Print LD_LIBRARY_PATH export for local development
	@python3 -c "import jbb; print('export LD_LIBRARY_PATH=' + ':'.join(jbb.jbb('LibCURL', outdir='$(TMP)/mcurl/'+jbb.get_key(), quiet=True)))"

.PHONY: check
check: ## Run code quality tools
	@uv run pre-commit run -a
	@uv run mypy

.PHONY: test
test: ## Run the test suite with coverage
	@uv run python -m pytest tests --cov --cov-config=pyproject.toml --cov-report=xml

.PHONY: build
build: clean ## Build sdist and wheel
	@TMP=$(TMP) uv build

.PHONY: clean
clean: ## Remove build artifacts
	@rm -rf dist/ build/ *.egg-info pymcurl.egg-info wheelhouse/
	@rm -f .coverage coverage.xml
	@find . -name '*.so' -not -path './.venv/*' -delete
	@find . -name '*.o' -not -path './.venv/*' -delete

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help

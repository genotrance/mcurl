export TMP ?= /tmp

# After install, LD_LIBRARY_PATH must include the jbb LibCURL lib directory
# for local test runs. Run: eval $(make env) to set it in your shell.
.PHONY: install
install: ## Install the virtual environment, build extension, and install pre-commit hooks
	@TMP=$(TMP) uv sync --locked
	@TMP=$(TMP) uv pip install -e .
	@uv run python -m pre_commit install

.PHONY: env
env: ## Print LD_LIBRARY_PATH export for local development
	@uv run --with 'jbb>=0.1.6' python -c "import jbb; version = next(line.split('=', 1)[1].strip().strip(chr(34)) for line in open('pyproject.toml') if line.startswith('version')); print('export LD_LIBRARY_PATH=' + ':'.join(jbb.jbb(f'LibCURL-v{version.rsplit(chr(46), 1)[0]}', outdir='$(TMP)/mcurl/'+jbb.get_key(), project='genotrance', quiet=True)))"

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
clean: ## Remove build artifacts and jbb cache
	@rm -rf dist/ build/ *.egg-info pymcurl.egg-info wheelhouse/
	@rm -f .coverage coverage.xml
	@find . -name '*.so' -not -path './.venv/*' -delete
	@find . -name '*.o' -not -path './.venv/*' -delete
	@rm -rf $(TMP)/mcurl/

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help

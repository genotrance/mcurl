# Testing

---

## Test suite layout

Tests are split across multiple files in `tests/`:

| File | Scope |
|------|-------|
| `conftest.py` | Shared fixtures (`method`, `is_multi`, `is_debug`, `query` helper) |
| `test_easy.py` | `Curl` class tests: HTTP methods, headers, redirects, user-agent, reset, binary, etc. |
| `test_multi.py` | `MCurl` class tests: concurrent handles, threaded do/stop/close, add/remove |
| `test_auth.py` | Auth-related: `getauth()`, proxy auth, failure threshold |
| `test_utils.py` | Utility functions: `py2cstr`, `sanitized`, version, features, dependency checks |
| `test_raw.py` | Raw `_libcurl_cffi` API tests |

---

## Running tests

### Quick run

```bash
make test
```

This runs `pytest` with coverage via `uv run`.

### Manual run

```bash
# Install dev dependencies
uv sync
uv pip install -e .

# Run all tests
uv run python -m pytest tests -q

# Run a specific file
uv run python -m pytest tests/test_easy.py -q

# Run with coverage
uv run python -m pytest tests --cov --cov-config=pyproject.toml --cov-report=xml
```

### With a specific Python version

```bash
uv run -p 3.14 python -m pytest tests -q
```

---

## Fixtures

Defined in `conftest.py`:

- **`method`** — parametrized: `GET`, `POST`, `PUT`, `DELETE`, `PATCH`
- **`is_multi`** — parametrized: `False`, `True`
- **`is_debug`** — parametrized: `False`, `True`
- **`httpbin_both`** — provided by `pytest-httpbin`, serves both HTTP and HTTPS

The `query()` helper function creates a `Curl` instance, performs a request,
and asserts success. Used by many tests to reduce boilerplate.

---

## Test dependencies

Test dependencies (`pytest`, `pytest-httpbin`, `pytest-cov`) are declared in
the `dev` dependency group in `pyproject.toml` alongside linting and type
checking tools (`pre-commit`, `ruff`, `mypy`). `uv sync` installs them all.

---

## Coverage

Coverage is configured in `pyproject.toml` under `[tool.coverage.*]`. Branch
coverage is enabled and scoped to the `mcurl` package. Empty files are skipped
in reports.

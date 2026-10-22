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
| `test_raw.py` | Raw libcurl cffi API tests (uses `mcurl.ffi` / `mcurl.libcurl`) |
| `test_gen.py` | Build helper `find_curlh` in `mcurl/gen.py`: header lookup from jbb directories and fallback (skipped if jbb is not installed) |
| `test_api.py` | New API surface: `setopt`/`getinfo`, `unsetopt`, timeout, auth, cookies, redirects, encoding, `dup`, pause/unpause, xferinfo/seek callbacks, context managers, convenience getters, `Curl.strerror`/`MCurl.strerror` |
| `test_auth_retry.py` | Local fake-proxy tests for proxies blocked after 407s: default latch, single probe per interval under a concurrent burst, recovery, re-block and backoff, probe slot release, `reset_proxy_failures()` |
| `test_auth_retry_burst.py` | Burst load on the same fake proxy: 10/50/200 threads through `MCurl.do()` (multi and easy interface) while the proxy rejects, recovers, is down or stalls, checking that exactly one authentication request reaches the proxy per interval, the probe slot is never left stuck, and `auth_retry=0` still latches. `AUTH_RETRY_ROUNDS=<n>` repeats every scenario n times |
| `test_dead_connection.py` | Local fake-upstream tests for stalled bodies, slow transfers, CONNECT tunnels, keepalive socket settings, and bounded multi waits |

The dead-connection tests use only loopback sockets. They verify that keepalive
is enabled and configured on the actual libcurl socket, but cannot reproduce a
network black hole: a userspace peer that stops reading still has its kernel
acknowledge TCP probes, and process suspension has the same limitation.
End-to-end keepalive expiry therefore requires a packet-dropping firewall or a
real failed network path and is not asserted by the unprivileged suite.

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

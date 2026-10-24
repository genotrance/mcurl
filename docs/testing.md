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
| `test_auth_retry_burst.py` | Burst load on the same fake proxy: 10/50/200 threads through `MCurl.do()` (multi and easy interface) while the proxy rejects, recovers, is down or stalls, checking that exactly one authentication request reaches the proxy per interval, the probe slot is never left stuck, and `auth_retry=0` still latches. `AUTH_RETRY_ROUNDS=<n>` repeats every scenario n times. The burst client threads and the fake proxy's per-connection handler threads are started with 256 KiB stacks (see `_small_stacks()` in `test_auth_retry.py`), because 200 threads at the default 8 MB can exhaust a 32-bit address space. Burst sizes are the same on every platform, so 32-bit runs check the same behaviour as 64-bit |
| `test_dead_connection.py` | Local fake-upstream tests for stalled bodies, slow transfers, CONNECT tunnels, keepalive socket settings, and bounded multi waits. Also guards the v8.23.0.1 regression where `do()` slept the full select cap with no sockets to wait on: unit checks that `_perform()` never sleeps without a libcurl timer and never waits for a handle another thread already finished, plus a stress test that runs rounds of concurrent CONNECTs through a fake proxy answering 407 then 200 on a kept-alive connection (multi-round proxy auth, as px does) and fails if any `do()` loop sleeps for `MULTI_SELECT_TIMEOUT`. `MULTI_ROUND_ROUNDS` and `MULTI_ROUND_WIDTH` tune its rounds and concurrency |
| `test_smoke.py` | Wheel smoke selection (`smoke` marker): libcurl loads, TLS and HTTP(S) are present, the libcurl version matches `pyproject.toml`, real HTTP and HTTPS requests succeed through `Curl` and `MCurl`, `cacert.pem` ships in the package, an untrusted certificate is rejected as a verification failure, and an installed wheel contains a bundled libcurl (skipped when not running from an installed wheel) |
| `test_selection.py` | Checks that the `slow`, `not slow` and `smoke` selections partition the suite as described below, by running `pytest --collect-only` in a subprocess |

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

This runs the full suite with coverage via `uv run`.

### Test selections

Three selections are defined with two pytest markers, both registered in
`pyproject.toml`. An unfiltered `pytest` run never skips or deselects anything,
so the full suite always includes every test.

| Selection | Command | Purpose |
|-----------|---------|---------|
| Full | `uv run python -m pytest tests -q` (or `make test` for coverage) | Everything, including slow tests. This is what CI test jobs and release validation run |
| Fast | `uv run python -m pytest tests -m "not slow" -q` (or `make test-fast`) | Local iteration; everything except the slow tests |
| Wheel smoke | `uv run python -m pytest tests -m smoke -q` (or `make test-smoke`) | Short check of an installed wheel; what cibuildwheel runs |

**`slow`** marks tests that spend most of their time waiting on real time: the
whole auth-retry burst suite (`test_auth_retry_burst.py`, applied with a module
level `pytestmark`), `test_concurrent_burst_sends_one_probe` in
`test_auth_retry.py`, the stalled-body, slow-download, CONNECT tunnel,
silent-proxy and concurrent multi-round tests in `test_dead_connection.py`, and
the idle-timeout test in `test_select.py`. They were chosen from measured
durations (`pytest --durations`), not by guesswork: on a Linux x86_64 host the
full suite took about 125 s, of which the burst file alone took about 70 s. With
the marked tests deselected (26 of 282) the fast selection takes about 33 s. Fast unit checks that live in
the same files (for example the `_perform()` no-sleep checks and the keepalive
socket options test) and the one-second `set_timeout` test in `test_api.py`
stay unmarked so the fast selection still covers the timeout, keepalive and
multi-wait code paths. A newly added test that waits several seconds on a
timer, a timeout or a burst of threads should be marked `@pytest.mark.slow`.

**`smoke`** marks the tests in `test_smoke.py` (module level `pytestmark`).
Each fails if the installed wheel cannot load libcurl or perform a real
request, and the packaging checks cover what auditwheel, delocate and delvewheel
are expected to produce. The slow marker is never combined with smoke.

The wheel smoke selection is run by cibuildwheel through `test-command` in
`pyproject.toml`. `test-skip` limits it to the oldest supported CPython (3.9),
the newest (3.15), the free-threaded 3.15 build (`cp315t`) and one PyPy build
(`pp311`) on each platform where those identifiers exist. All wheels are still
built, including the intermediate CPython versions and `pp312`; only their test
step is skipped, because the CPython wheel is a single abi3 build. When the
supported CPython window changes (a new release, or an end of life), update the
`test-skip` list so the oldest and newest CPython stay tested.

The CI jobs that call cibuildwheel only to test (`tests-windows` and
`tests-i686` in `main.yml`) override both `test-command` and `test-skip` so they
keep running the full suite, including the 200-thread burst tests that exist to
catch 32-bit address space exhaustion, on every interpreter in their matrix.

### Manual run

```bash
# Install dev dependencies
uv sync
uv pip install -e .

# Run all tests (full selection)
uv run python -m pytest tests -q

# Run everything except slow tests (fast selection)
uv run python -m pytest tests -m "not slow" -q

# Run the wheel smoke selection
uv run python -m pytest tests -m smoke -q

# Run a specific file
uv run python -m pytest tests/test_easy.py -q

# Run with coverage
uv run python -m pytest tests --cov --cov-config=pyproject.toml --cov-report=xml
```

### With a specific Python version

```bash
uv run -p 3.15 python -m pytest tests -q
```

CI runs the full suite on CPython 3.9 through 3.15. Run the command above
before adding support for a newly released CPython version so the local build,
cffi extension, and test dependencies are exercised together.

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

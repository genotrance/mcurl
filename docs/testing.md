# Testing

---

## Test suite layout

Tests are split across multiple files in `tests/`:

| File | Scope |
|------|-------|
| `conftest.py` | Shared fixtures (`method`, `is_multi`, `is_debug`, `query` helper) |
| `test_easy.py` | `Curl` class tests: HTTP methods, headers, redirects, user-agent, reset, binary, `get_os_errno`/`get_errorbuffer` (including that the errno survives a later successful connect), etc. |
| `test_multi.py` | `MCurl` class tests: concurrent handles, threaded do/stop/close, add/remove |
| `test_auth.py` | Auth-related: `getauth()`, proxy auth, failure threshold |
| `test_utils.py` | Utility functions: `py2cstr`, `sanitized`, version, features, dependency checks |
| `test_raw.py` | Raw libcurl cffi API tests (uses `mcurl.ffi` / `mcurl.libcurl`) |
| `test_gen.py` | Build helper `find_curlh` in `mcurl/gen.py`: header lookup from jbb directories and fallback (skipped if jbb is not installed) |
| `test_api.py` | New API surface: `setopt`/`getinfo`, `unsetopt`, timeout, auth, cookies, redirects, encoding, `dup`, pause/unpause, xferinfo/seek callbacks, context managers, convenience getters, `Curl.strerror`/`MCurl.strerror` |
| `test_auth_retry.py` | Local fake-proxy tests for proxies blocked after 407s: default latch, single probe per interval under a concurrent burst, recovery, re-block and backoff, probe slot release, `reset_proxy_failures()` |
| `test_auth_retry_burst.py` | Burst load on the same fake proxy: 10/50/200 threads through `MCurl.do()` (multi and easy interface) while the proxy rejects, recovers, is down or stalls, checking that exactly one authentication request reaches the proxy per interval, the probe slot is never left stuck, and `auth_retry=0` still latches. `AUTH_RETRY_ROUNDS=<n>` repeats every scenario n times. The burst client threads and the fake proxy's per-connection handler threads are started with 256 KiB stacks (see `_small_stacks()` in `test_auth_retry.py`), because 200 threads at the default 8 MB can exhaust a 32-bit address space. Burst sizes are the same on every platform, so 32-bit runs check the same behaviour as 64-bit |
| `test_ntlm_proxy.py` | Local fake connection-based NTLM proxy (challenge 407 on every new connection, then type1/type2/type3 on one connection; keep-alive and close-after-first-407 variants, optionally also offering Negotiate) driven with 15 concurrent CONNECT and plain HTTP requests through one `MCurl` with auth `ANY`, as `px --test` does. Against a proxy that also offers Negotiate, `ANY` makes libcurl pick Negotiate; since the test has no Kerberos ticket, GSS-API fails locally and `do()` retries with NTLM (the fallback this module exists to cover), so the same `ANY` assertions exercise both the plain-NTLM and the Negotiate-then-NTLM-fallback paths, keep-alive and close, HTTP and CONNECT. Asserts intermediate challenge legs never count towards `MCurl.failed`, a successful tunnel or request resets it (and, for the Negotiate+NTLM proxies, that the cached method is `CURLAUTH_NTLM`, confirming the fallback rather than a completed Negotiate), handshakes interrupted by the proxy closing before type2 are inconclusive (px#268: v8.23.0.2 counted these CONNECT errors and blocked the proxy), and wrong credentials still block after `failure_threshold` with exactly one failure counted per request even though the Negotiate-then-NTLM fallback makes two attempts internally. It also covers the classification fixes layered on top: a final CONNECT 407 still counts when the proxy host resolved first to a refused address (via `CURLOPT_RESOLVE`; the OS errno alone must not decide), a 407 leg with `Connection: close` followed by a refused reconnect and a 407 whose headers are cut off stay inconclusive, and a complete keep-alive type2 response that is cut immediately afterwards does not latch the proxy. The concurrent HTTP case completes and the in-process CONNECT_ONLY variant reports the cut as a 502 before proving the proxy remains available to its following concurrent burst. It also checks that a failed `ANY` attempt with a wrong password against a Negotiate+NTLM proxy does not poison the auth-method cache for a later explicit NTLM request, a final 407 or `reset_proxy_failures()` clears the cached method, a 200 response carrying headers that merely contain "407" reaches the client intact, and POST/PUT bodies of several KB supplied through the read callback are resent across the auth legs and the Negotiate-then-NTLM retry (the fake proxy checks the exact body on the authenticated leg; keep-alive and close, multi and easy, explicit NTLM, wrong password counted once, and a non-seekable body giving an inconclusive result). The module skips only when `curl_version_info` lacks `CURL_VERSION_NTLM`: Homebrew curl 8.21+ intentionally omits NTLM on macOS, while the jbb libcurl builds used on Linux and Windows retain it |
| `test_stream_upload.py` | Streamed `bridge()` uploads through a local fake NTLM proxy that honours `Expect: 100-continue` like Squid (answers an unauthenticated request before its body and closes), optionally also offering Negotiate, with a body source that cannot seek, returns short reads and generates its data on the fly. Covers unknown auth (`ANY`) for POST, PUT and PATCH, multi and easy, with no curl error 65 and 0 body bytes on the probe leg; known NTLM keeping libcurl's `Content-Length: 0` type1 leg without Expect; empty, small and multi-MB known-length bodies and chunked unknown-length bodies arriving byte-identical (SHA-256 at the proxy); caching of the picked method so the next upload starts with type1 or reuses the authenticated connection; an 8 MiB upload with a bounded Python peak (tracemalloc) and strictly sequential, single reads; a proxy that ignores Expect giving the documented `REWIND_ERROR` without counting an auth failure; and the seek callback refusing to rewind a consumed non-seekable source. These tests failed on devel 005eee0 (error 65, empty chunked bodies, PATCH truncated by a short read) |
| `test_dead_connection.py` | Local fake-upstream tests for stalled bodies, slow transfers, CONNECT tunnels, keepalive socket settings, and bounded multi waits. Also guards the v8.23.0.1 regression where `do()` slept the full select cap with no sockets to wait on: unit checks that `_perform()` never sleeps without a libcurl timer and never waits for a handle another thread already finished, plus a stress test that runs rounds of concurrent CONNECTs through a fake proxy answering 407 then 200 on a kept-alive connection (multi-round proxy auth, as px does) and fails if any `do()` loop sleeps for `MULTI_SELECT_TIMEOUT`. `MULTI_ROUND_ROUNDS` and `MULTI_ROUND_WIDTH` tune its rounds and concurrency |
| `test_smoke.py` | Wheel smoke selection (`smoke` marker): libcurl loads, TLS and HTTP(S) are present, the libcurl version matches `pyproject.toml`, real HTTP and HTTPS requests succeed through `Curl` and `MCurl`, `cacert.pem` ships in the package, an untrusted certificate is rejected as a verification failure, and an installed wheel contains a bundled libcurl (skipped when not running from an installed wheel; fails instead under cibuildwheel, with paths normalised via realpath and normcase), and, under `CIBUILDWHEEL`, `mcurl` and `_libcurl_cffi` are imported from site-packages rather than the checkout |
| `test_select.py` | `MCurl` multi-wait (`select`) behaviour, including the slow idle-timeout test |
| `test_release_decision.py` | `tools/release_decision.py`, the target-version and release-needed logic used by `check-upstream.yml`; loaded by file path (see below) |
| `test_selection.py` | Checks the marker selections by running `pytest --collect-only` in a subprocess: `slow` and `not slow` together make up the full suite, and `smoke` has no slow tests and is under a tenth of the full run. `smoke` overlaps `not slow`, so the selections are not a partition |

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
| Full | `uv run python -m pytest tests -q` (or `make test` for coverage) | Everything, including slow tests. This is what the weekly and `full=true` CI lanes run, and what the `tests-i686` and `tests-musl` jobs run |
| Fast | `uv run python -m pytest tests -m "not slow" -q` (or `make test-fast`) | Local iteration and the push/PR fast CI lane; everything except the slow tests |
| Wheel smoke | `uv run python -m pytest tests -m smoke -q` (or `make test-smoke`) | Short check of an installed wheel; what cibuildwheel runs |

**`slow`** marks tests that spend most of their time waiting on real time: the
whole auth-retry burst suite (`test_auth_retry_burst.py`, applied with a module
level `pytestmark`), `test_concurrent_burst_sends_one_probe` in
`test_auth_retry.py`, the stalled-body, slow-download, CONNECT tunnel,
silent-proxy and concurrent multi-round tests in `test_dead_connection.py`, and
the idle-timeout test in `test_select.py`. They were chosen from measured
durations (`pytest --durations`), not by guesswork: on a Linux x86_64 host the
full suite took about 128 s, of which the burst file alone took about 70 s. With
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

#### Wheel smoke selection and test-skip

This is the authoritative explanation; [docs/ci.md](ci.md) and
[docs/build.md](build.md) link here. The smoke selection is run by cibuildwheel
through `test-command` in `pyproject.toml`, and `test-skip` limits it to the
oldest supported CPython (3.9), the newest (3.15), the free-threaded 3.15 build
(`cp315t`) and PyPy 3.11 (`pp311`), on each platform that builds those
identifiers. PyPy wheels are Linux-only and cover the glibc platforms (x86_64,
i686 and aarch64); there are none for musllinux, macOS or Windows. The `pypy`
enable group builds PyPy 3.11 and 3.12 (`pp312`) on those platforms, and the
PyPy-EOL versions (3.9 and 3.10) are not enabled. PyPy 3.11 is therefore the
only smoke-tested PyPy build, and PyPy 3.12 is build-only. All wheels are still
built; the skipped identifiers are build-only, because the CPython wheel is a
single abi3 build and one smoke run per distinct build is enough to prove that
libcurl bundles and loads. When the supported CPython window changes (a new
release or an end of life), update `test-skip` so the oldest and newest CPython
stay tested.

The CI jobs that call cibuildwheel only to test (`tests-windows`, `tests-i686`
and `tests-musl` in `main.yml`) override both `test-command` and `test-skip`.
`tests-i686` always runs the full suite, including the 200-thread burst tests,
because the slow burst tests are what exposed 32-bit address-space exhaustion
in the first 8.23.0.1 attempt; `tests-windows` runs the fast selection on
pushes and the full suite in the weekly full lane (see [docs/ci.md](ci.md)).

The cibuildwheel test commands pass `-rs` so skips show in the logs and
`--import-mode=importlib` so pytest does not put the checkout on `sys.path`;
otherwise the tests would import `mcurl` from the source tree rather than the
installed wheel. `tests/test_release_decision.py` therefore loads
`tools/release_decision.py` by file path.

### Contributor testing strategy

Routine CI and release coverage differ on purpose. This section explains the
intent; the job lists, workflow triggers and release checklist are in
[docs/ci.md](ci.md).

**Every push and pull request** runs the fast lane: lint and type checks, then
the `not slow` selection on a small set of jobs. Each job covers a code path
that differs by platform or interpreter: Linux x86_64 CPython 3.9 and 3.15,
PyPy 3.11, Linux aarch64 CPython 3.15, macOS 15 arm64 CPython 3.15 (Homebrew
libcurl and the dynamic-library path), Windows AMD64 cp315 (cibuildwheel and
delvewheel repair), and the i686 job. The i686 job is the exception: it runs
the full suite, including slow tests, because the 32-bit address-space limit
was found by the slow burst tests.

**The weekly run** (Saturday 03:00 UTC) and a manual dispatch with `full=true`
run the full lane: the complete suite, including slow tests, across the
interpreter and platform matrix in `docs/ci.md`, plus musllinux.

**The matrix is split because the project is small and GitHub macOS runners
are slow, expensive and allow little concurrency.** A small project does not
need every interpreter on every push, and each macOS job queues behind few
parallel slots and spends most of its time in the Homebrew update step. Before the split, each push ran 29 jobs, seven of them macOS, and took
about 15 minutes; much of each macOS job went to the Homebrew update step. The
fast lane keeps one job per platform-specific path and moves the rest to the
weekly lane. Locally the fast selection took about 33 s against about 128 s
for the full suite on Linux x86_64 with CPython 3.15 (the same measurement as
above).

**abi3 wheels need only the oldest and newest CPython.** The release wheels
are one `cp39-abi3` build, so the same binary serves every CPython from 3.9
up. The wheel smoke selection therefore runs only on the oldest CPython (3.9)
and the newest (3.15), which bracket the supported range. Free-threaded
`cp315t` and PyPy `pp311` are separate builds with their own code paths, so
each gets one smoke run per platform that builds it. The other wheels are
build-only (see [Wheel smoke selection and test-skip](#wheel-smoke-selection-and-test-skip)).

**Before a release, every supported OS, architecture and libc is tested.**
The weekly or `full=true` run covers the full suite on the interpreters and
platforms in the full lane. The `build.yml` dry run builds every wheel and
smoke-tests only the subset above, so some wheels are build-only. Not every
Python, OS and architecture combination runs the full suite; the exact tested
versus build-only table is in [docs/ci.md](ci.md). Both runs, on the exact
candidate SHA, are the release gate, not the push lane.

**Targeted Homebrew curl check (macOS).** The macOS wheel is built from
Homebrew's curl, and the sdist build needs Homebrew curl at the libcurl base
version in `pyproject.toml`. `tools/brew_curl.sh` checks the installed curl
against that version, upgrades only on a mismatch, and fails the job if the
final version is wrong. Contributors building from source on macOS can run it
(it changes the local Homebrew curl) so the local libcurl matches what CI builds
against. Release mode in `build.yml` also refreshes curl's dependencies because
the wheel bundles them.

**Testing px's corporate proxy scenarios against the exact candidate.** mcurl's
own suite does not catch everything a consumer depends on. px's HTTPS
proxy-auth tests caught a regression in 8.23.0.1 that mcurl's suite did not.
`build.yml` now makes px's Docker-backed corporate proxy scenario suite a
release gate: it installs the Linux candidate wheel into px's `devel` checkout
and runs the suite in every release and dry run. Ordinary pull requests do not
need this run, but changes to proxy authentication, auth retry, or timeouts
should be called out in the PR.

### Local reproduction

| CI selection | Local command |
|--------------|---------------|
| Fast push/PR lane | `make test-fast` (run `eval $(make env)` first on Linux so the test-time `LD_LIBRARY_PATH` points at the jbb libcurl) |
| Full lane | `make test`, or `uv run python -m pytest tests -q` |
| Wheel smoke (on an installed wheel) | Build with `uv build`, install the wheel into a fresh virtual environment with `pytest` and `pytest-httpbin`, then run `python -m pytest <checkout>/tests -m smoke -q -rs --import-mode=importlib` from a directory outside the checkout |
| px corporate proxy scenarios (release gate) | In a px `devel` checkout with Docker available: `uv venv --python 3.13 .venv && uv sync --locked && uv pip install --python .venv/bin/python --reinstall /path/to/mcurl/dist/pymcurl-*.whl && PX_REQUIRE_MCURL_FIX=1 PX_SCENARIO_ENV=start uv run --locked --no-sync python -m pytest tests/scenarios -m scenario -v` |

`make test-smoke` runs the smoke selection against the editable development
environment. It does not test an installed wheel, which is what cibuildwheel
checks, so use the wheel command above to reproduce the release test.
The px command uses a locally built candidate wheel, not an editable mcurl
checkout, and its fixture tears down the Docker environment when the run ends.

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

The weekly full CI lane runs the full suite on CPython 3.9 through 3.15 on Linux x86_64 and macOS. Run the command above
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

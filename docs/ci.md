# CI & GitHub Actions

---

## Overview

Three workflows power the CI/CD pipeline:

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| CI | `main.yml` | push to `main`/`devel`, PRs | Code quality + test matrix |
| Build wheels | `build.yml` | push to `main`/`devel`, manual dispatch | Build + publish to PyPI |
| Check upstream | `check-upstream.yml` | monthly cron, manual | Detect new LibCURL_jll.jl releases |

---

## CI (`main.yml`)

Runs code quality checks and the test suite across multiple Python versions and
operating systems.

### Jobs

**`quality`** (ubuntu-latest):
- Checks out the repo
- Runs `make check` (pre-commit hooks + mypy)

**`tests`** (matrix):
- OS: `{ubuntu-latest, macos-latest}`
- Python: `{3.9, 3.10, 3.11, 3.12, 3.13, 3.14, pypy3.10, pypy3.11}`
- **Excludes:** macOS × PyPy — installing cffi 2.x from PyPI overwrites PyPy's
  built-in `_cffi_backend`, causing `CURLE_URL_MALFORMED` failures at runtime.
  PyPy on Linux works correctly because the built-in cffi is preserved.
- Builds the cffi extension and runs `pytest` with coverage

**`tests-windows`** (windows-latest):
- Builds and tests a single wheel (`cp312-win_amd64`) via cibuildwheel.
- Uses the same `pyproject.toml` `[tool.cibuildwheel.windows]` config as the
  release build, providing confidence that Windows wheels are correct without
  running the full matrix on Windows.

The composite action `.github/actions/setup-python-env`:
- Sets `allow-prereleases: true` so that Python 3.14 and PyPy can be installed.
- Installs `uv`, syncs dependencies, and builds the extension.
- For PyPy: skips installing `cffi` from PyPI (PyPy ships its own built-in cffi).
- On Linux: a separate step sets `LD_LIBRARY_PATH` to point at the jbb-
  downloaded libcurl so the extension can load at test time.
- On macOS: a separate step sets `DYLD_LIBRARY_PATH` to point at the Homebrew
  libcurl directories for test time. At build time, rpaths are embedded via
  `-Wl,-rpath` so installed wheels do not need `DYLD_LIBRARY_PATH` at runtime.

**Notes:**
- PyPy wheels are not abi3 — each PyPy version gets its own wheel.
- PyPy on macOS is excluded because cffi 2.x from PyPI conflicts with PyPy's
  built-in `_cffi_backend`, producing silent ABI mismatches that cause libcurl
  to return `CURLE_URL_MALFORMED` (error 3) on every HTTP request. This does
  not affect Linux PyPy where the built-in cffi is preserved.
- **Free-threaded Python (3.14t) is not currently supported.** `brotlicffi`
  (a transitive dependency of `pytest-httpbin` via `httpbin`) does not have a
  release that supports free-threaded builds as of v1.2.0.0. Support was added
  to its `main` branch but has not been tagged/released. Re-enable once
  `brotlicffi` publishes a compatible release.
- `jsonschema<4.18` is pinned to avoid pulling in `rpds-py` (Rust, no PyPy
  wheel) via the `httpbin → flasgger → jsonschema` chain.
- Windows is tested via a single `cp312-win_amd64` cibuildwheel job in CI
  and fully built across all versions in `build.yml`. The CI job overrides
  `CIBW_REPAIR_WHEEL_COMMAND_WINDOWS` to use `uv pip install` instead of
  `pip install` because pip is not available in cibuildwheel's build venv.

---

## Build wheels (`build.yml`)

Builds wheels for all platforms using cibuildwheel and publishes to PyPI.

### Triggers
- **Push to `main`**: auto-build + publish
- **Push to `devel`**: build only (publish is gated to `main`)
- **`workflow_dispatch`**: manual trigger

### Jobs

1. **`setup`** — resolves the version from the latest
   [LibCURL_jll.jl](https://github.com/genotrance/LibCURL_jll.jl) release tag
   and the current `pyproject.toml` version.
2. **`build-sdist`** — builds the source distribution.
3. **`build-wheels`** — cibuildwheel matrix across platforms/architectures.
4. **`publish`** — uploads to PyPI via
   [trusted publisher](https://docs.pypi.org/trusted-publishers/) and creates
   a git tag.

Publishing only happens on pushes to `main`.

cibuildwheel skip patterns (`cp314t-*`, `pp*-macosx_*`, `pp*-win_*`,
`*-musllinux_i686`) ensure unsupported configurations are excluded from the
build matrix.

---

## Check upstream (`check-upstream.yml`)

- Runs monthly (1st of month, 06:00 UTC) and on manual dispatch.
- Fetches the latest release tag from `genotrance/LibCURL_jll.jl` via the
  GitHub API.
- Compares the upstream libcurl version with the current version in
  `pyproject.toml`.
- If a newer version is found, triggers `build.yml` via `workflow_dispatch`.

---

## Dependabot

Dependabot is configured to check for GitHub Actions version updates on a
monthly schedule. This keeps action pinned versions (e.g. `actions/checkout`)
current without manual tracking.

---

## Deployment flow

1. Push to `devel` → CI runs (quality + test matrix) + `build.yml` builds wheels (no publish)
2. If CI and wheels green → merge `devel` → `main`
3. Push to `main` → CI runs + `build.yml` auto-publishes to PyPI + tags release

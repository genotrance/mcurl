# CI & GitHub Actions

---

## Overview

Three workflows power the CI/CD pipeline:

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| CI | `main.yml` | push to `main`/`devel`/`working`, PRs (fast lane); weekly cron Sat 03:00 UTC and manual dispatch with `full` (full lane) | Code quality + test matrix |
| Build wheels | `build.yml` | push to `main`, manual dispatch | Build + publish to PyPI |
| Check upstream | `check-upstream.yml` | weekly cron (Mon 18:00 UTC), manual | Commit version bump and release new libcurl |

---

## CI (`main.yml`)

Runs code quality checks and the test suite. A small `plan` job selects one of
two lanes and emits the job matrices, so both lanes share the same job
definitions.

### Lanes

**Fast lane** — every push to `main`/`devel`/`working` and every pull request
(and a manual dispatch without `full`). Tests run with `-m "not slow"` except
where noted:

| Job | Configuration | Why it is in the fast lane |
|-----|---------------|----------------------------|
| `tests` | Linux x86_64 CPython 3.9 | oldest supported CPython |
| `tests` | Linux x86_64 CPython 3.15 | newest supported CPython |
| `tests` | Linux x86_64 PyPy 3.11 | PyPy-only path (built-in cffi, no cffi install) |
| `tests` | Linux aarch64 CPython 3.15 | only arm64 Linux code path outside the release build |
| `tests` | macOS 15 arm64 CPython 3.15 | only Homebrew-libcurl path and `DYLD_LIBRARY_PATH`/rpath setup |
| `tests-windows` | Windows AMD64 cp315 | newest CPython on Windows; mingw32 build and delvewheel repair |
| `tests-i686` | manylinux i686 cp315, **full suite** | 32-bit address-space limit; the slow burst tests are the ones that broke i686, so this job never skips them |

**Full lane** — the weekly schedule (Saturday 03:00 UTC, runs from the default
branch `devel`) and `workflow_dispatch` with `full` enabled. No marker filter,
so slow tests run everywhere:

- Linux x86_64: CPython 3.9–3.15 and PyPy 3.11.
- Linux aarch64: CPython 3.9, 3.13, 3.14, 3.15 and PyPy 3.11 (native
  `ubuntu-24.04-arm` runners, no QEMU).
- macOS 15 arm64: CPython 3.9–3.15.
- Windows AMD64 (cibuildwheel): cp39, cp312, cp313, cp314, cp315 and
  free-threaded cp315t.
- Linux wheels (cibuildwheel): i686 cp39, cp314, cp315 and free-threaded
  cp315t on manylinux x86_64.

musllinux and the remaining architecture/interpreter wheel combinations are
covered by `build.yml` (see below), whose dry run is the pre-release gate.
The full lane keeps every exclusion the previous single matrix had: no PyPy on
macOS or Windows, no `cp314t`, no musllinux i686.

### Rationale and baseline

Baseline (before the split): devel CI run 40019820063 (2026-10-23) ran 29 jobs
for every push and took 15 min 15 s from creation to completion. Linux jobs
took 2–3 min, Windows and i686 cibuildwheel jobs 3–4 min, and each of the
seven macOS jobs 5–6 min, of which 61–124 s was the Homebrew
`brew update`/`brew upgrade curl` step (earlier runs such as 39668753800
spent 15–20 min there). macOS runners are the most expensive, so seven macOS
jobs per push dominated cost. The fast selection (`-m "not slow"`) measured
32.9 s against 127.6 s for the full suite locally on Linux x86_64 3.15
(`docs/testing.md`).

The fast lane cuts routine CI to 7 test jobs with one macOS job, while each
platform- or architecture-specific code path still runs on every push. The
weekly full lane and the `build.yml` dry run before a release retain complete
interpreter, OS, architecture and slow-test coverage.

### Homebrew curl on macOS

`tools/brew_curl.sh` (used by `main.yml` and `build.yml`) replaces the
previous unconditional `brew update && brew upgrade curl`. It disables
Homebrew's automatic update, cleanup and dependents checks, and compares the
preinstalled curl with the libcurl base version in `pyproject.toml`. Only on a
mismatch does it read the current curl version from the Homebrew formula API
(`formulae.brew.sh`); if that is not the expected version it fails
immediately, otherwise it refreshes only the API metadata and upgrades (or
installs) curl alone. A final hard assertion that installed curl equals the
pyproject base version is kept, because a dependency-link warning can make a
successful upgrade exit nonzero and because a stale curl must never be used
to build or test. `check-upstream.yml` keeps its own Homebrew check.

### Collecting after numbers

These require GitHub and must be run by the operator:

1. Fast lane: push the change to `working` (or `devel`), then
   `gh run list -R genotrance/mcurl -w CI -b working -L 1` and
   `gh run view <id> -R genotrance/mcurl --json jobs --jq '.jobs[] | [.name, .startedAt, .completedAt] | @tsv'`.
   Expect 1 plan + 1 quality + 7 test jobs, and check the macOS
   "Ensure Homebrew libcurl" step duration in `gh run view <id> --log` output.
2. Full lane on demand: `gh workflow run main.yml -R genotrance/mcurl --ref devel -f full=true`,
   then the same `gh run view` query; expect 30 test jobs (including
   `test windows 3.15t` and `test linux x86_64 3.15t`).
3. Schedule: after the first Saturday 03:00 UTC run,
   `gh run list -R genotrance/mcurl -w CI -e schedule -L 1`.
4. Release matrix: `gh workflow run build.yml -R genotrance/mcurl --ref devel -f dry_run=true`
   and confirm every `Build <os> <arch>` job passed and the artifacts include
   i686, musllinux, aarch64, macOS arm64, Windows and cp315t wheels.

Record the measured totals here next to the baseline.

### Jobs

**`plan`** (ubuntu-latest): selects the lane and outputs the matrices and the
pytest marker arguments.

**`quality`** (ubuntu-latest):
- Checks out the repo
- Runs `make check` (pre-commit hooks + mypy)

**`tests`** (matrix from `plan`):
- macOS tests use `macos-15`, matching the macOS release-wheel runner and its
  Homebrew libcurl version, and run `tools/brew_curl.sh` before setup.
- The macOS arm64 release wheel requires macOS 15 or later. Since 8.23.0.1 the
  wheel is built on `macos-15` and bundles Homebrew's curl dependency closure;
  current Homebrew bottles for curl and most of its dependencies target macOS
  15, and rebuilding that closure from source for macOS 14 was rejected as too
  costly. This also avoids depending on the macOS 14 GitHub-hosted runner,
  which is scheduled for retirement on 2026-11-02. On macOS 14, pip falls back
  to the sdist, which requires Homebrew curl matching the libcurl version.
- **Excludes:** macOS × PyPy — installing cffi 2.x from PyPI overwrites PyPy's
  built-in `_cffi_backend`, causing `CURLE_URL_MALFORMED` failures at runtime.
  PyPy on Linux works correctly because the built-in cffi is preserved.
- Builds the cffi extension and runs `pytest` with coverage

**`tests-windows`** (matrix from `plan`):
- Builds and tests via cibuildwheel with the same `pyproject.toml`
  `[tool.cibuildwheel.windows]` config as the release build.
- Overrides `test-skip` with an empty value and `test-command` with the lane's
  selection instead of the wheel smoke selection.

**`tests-i686`** (matrix from `plan`; named `test linux <arch> <python>`):
- Builds and tests Linux wheels via cibuildwheel: 32-bit `manylinux` i686 in
  both lanes, plus free-threaded cp315t on x86_64 in the full lane.
- Added because `main.yml` otherwise never runs 32-bit, yet `build.yml` ships
  i686 wheels. The release build hit a 32-bit-only failure: the auth_retry
  burst tests start 200 threads, which can exhaust the address space at the
  default 8 MB stack size.
- Overrides the `pyproject.toml` cibuildwheel `test-command` and `test-skip` so
  it always runs the full suite, including slow tests. The default there is
  the short wheel smoke selection; see `docs/testing.md`.

The composite action `.github/actions/setup-python-env`:
- Sets `allow-prereleases: true` so that current CPython releases and PyPy can
  be installed before GitHub runner images provide them directly.
- Installs `uv`, syncs dependencies with `uv sync --locked` (fails if `uv.lock` is stale), and builds the extension.
- For PyPy: skips installing `cffi` from PyPI (PyPy ships its own built-in cffi).
- On Linux: a separate step reads the base libcurl version from
  `pyproject.toml`, then sets `LD_LIBRARY_PATH` to the matching
  `genotrance` jbb download so the extension can load at test time. This
  avoids relying on a prior build having populated the same jbb cache.
- On macOS: a separate step sets `DYLD_LIBRARY_PATH` to point at the Homebrew
  libcurl directories for test time. At build time, rpaths are embedded via
  `-Wl,-rpath` so installed wheels do not need `DYLD_LIBRARY_PATH` at runtime.

**Notes:**
- PyPy wheels are not abi3 — each PyPy version gets its own wheel.
- PyPy on macOS is excluded because cffi 2.x from PyPI conflicts with PyPy's
  built-in `_cffi_backend`, producing silent ABI mismatches that cause libcurl
  to return `CURLE_URL_MALFORMED` (error 3) on every HTTP request. This does
  not affect Linux PyPy where the built-in cffi is preserved.
- **Free-threaded CPython 3.15 is shipped.** cibuildwheel builds and runs the
  configured test command for each `cp315-cp315t` wheel. These wheels are
  separate from the `cp39-abi3` wheels because the free-threaded ABI is not
  covered by the stable ABI.
- `jsonschema<4.18` is pinned to avoid pulling in `rpds-py` (Rust, no PyPy
  wheel) via the `httpbin → flasgger → jsonschema` chain.
- **musllinux** is tested via cibuildwheel in `build.yml`, which builds
  musllinux wheels and runs the test suite inside the manylinux/musllinux
  containers. A separate musl job in `main.yml` is not needed because
  jbb's bundled OpenSSL conflicts with the newer system OpenSSL in musl
  containers, breaking Python's `ssl` module.
- Windows CI tests cp315 in the fast lane and cp39, cp312–cp315 and cp315t in
  the full lane via cibuildwheel, and is fully built across all versions in
  `build.yml`. The CI
  job overrides `CIBW_REPAIR_WHEEL_COMMAND_WINDOWS` to use `uv pip install`
  instead of `pip install` because pip is not available in cibuildwheel's
  build venv.

---

## Build wheels (`build.yml`)

Builds wheels for all platforms using cibuildwheel and publishes to PyPI.

### Triggers
- **Push to `main`**: auto-build + publish
- **`workflow_dispatch`**: manual trigger (can be used on any branch including
  `devel` for ad-hoc builds without publishing). An optional `sha` input
  builds that exact commit and releases the version committed in its
  `pyproject.toml` instead of deriving one; `check-upstream.yml` uses it so the
  build cannot drift to a moving branch head.

Actual `main` releases share the `release-main` concurrency group with the
weekly upstream check. This prevents a manually pushed `main` release and a
scheduled release decision from acting on stale state at the same time. A
queued release re-checks the `main` SHA before it commits anything.

### Jobs

1. **`setup`** — resolves the version from the latest
   [LibCURL_jll.jl](https://github.com/genotrance/LibCURL_jll.jl) release tag
   and the current `pyproject.toml` version.
2. **`build-sdist`** — builds the source distribution.
3. **`build-wheels`** — cibuildwheel matrix across platforms/architectures.
   Linux aarch64 builds use native `ubuntu-24.04-arm` runners (no QEMU).
   macOS runs `tools/brew_curl.sh` before compilation so its wheel uses the
   release's declared libcurl version (upgrading curl only when needed and
   failing on any mismatch).
4. **`publish`** — first verifies that the resolved release version matches
   `pyproject.toml` at the release commit (the `sha` input when given,
   otherwise the triggering commit) and that every artifact carries that
   version, so a tag can never point at a commit declaring a different
   version. It then checks PyPI: if the version is already published the
   upload is skipped (never uploading twice) and only the missing tag is
   created; otherwise it uploads via
   [trusted publisher](https://docs.pypi.org/trusted-publishers/) and tags
   the release commit only after the upload succeeded.

Publishing only happens for `main`. `workflow_dispatch` accepts `dry_run` in
addition to `sha`. A dry build still exercises the complete wheel matrix and
validates artifacts and PyPI state, but never uploads a distribution or
creates a tag. Its own concurrency group means it cannot delay a real release.

The release build tests each wheel with the short smoke selection only, on the
oldest and newest CPython, free-threaded 3.15 and one PyPy build per platform
(configured in `pyproject.toml`; see `docs/testing.md`). All wheels are still
built.

cibuildwheel skip patterns (`cp314t-*`, `pp*-macosx_*`, `pp*-win_*`,
`*-musllinux_i686`) ensure unsupported configurations are excluded from the
build matrix. CI uses cibuildwheel 4.3.4 because it recognizes the CPython
3.15 and free-threaded CPython 3.15 identifiers used by the release build and
focused wheel tests.

---

## Release checklist

Run this checklist before merging a release from `devel` into `main`. It
combines the checks that protect the release build with an integration check
against px, which consumes pymcurl in production.

1. Confirm that CI is green on `devel`, including the 32-bit Linux job, and
   that the latest full-lane run (weekly or dispatched with `full`) passed. The
   release build includes an i686 wheel, so the regular test matrix alone does
   not cover every shipped configuration.
2. Build the release candidate locally with the Makefile, using the libcurl
   base version declared in `pyproject.toml`, and run mcurl's full test suite
   against that candidate.
3. Install the candidate into px's current working branch and run px's full
   suite at least twice with `PX_EVENT_LOOP` unset and at least twice with it
   set to `asyncio`. Run these tests from a directory outside the developer's
   home directory: a `~/.env` containing `PX_` variables can alter px's
   authentication-chain tests. Repeat the same runs with the previous pymcurl
   release for comparison. Any failure that is new with the candidate blocks
   the release. This check would have caught the HTTPS proxy-auth regression
   shipped in 8.23.0.1, which was released after step 3 was skipped.
4. Compare the wheel platform tags produced by the candidate with the
   previous release's files on PyPI. A runner change moved the macOS wheel
   from `macosx_14_0` to `macosx_15_0` in 8.23.0.1; tag changes must be
   understood before release.
5. Confirm that Homebrew's curl matches the libcurl base version in
   `pyproject.toml`. The macOS wheel is built from Homebrew, so a mismatch
   can produce a wheel for a different libcurl than the declared release.
6. Merge `devel` into `main` with the commit message
   `Merge devel for libcurl <version> release`. The push runs `build.yml`,
   which builds and publishes the distributions and then creates the
   matching tag. Afterward, verify the expected files are present on PyPI and
   that the tag points to the intended release commit.

The version, wheel, and publication details are described in the
[build-system release notes](build.md#version-scheme); this checklist is the
operator gate before the merge.

---

## Check upstream (`check-upstream.yml`)

- Runs weekly on Monday at 18:00 UTC (after the LibCURL_jll.jl weekly release
  at Monday 06:00 UTC, leaving Homebrew time to ship the same curl) and on
  manual dispatch. It was monthly before; weekly releases follow libcurl
  within a week without manual action.
- Fetches the latest release tag from `genotrance/LibCURL_jll.jl` via the
  GitHub API.
- Fails without dispatching a build if the API response does not contain a
  valid release tag, preventing a transient lookup failure from rebuilding an
  already-published version.
- Computes the target version with `tools/release_decision.py` (the
  `pyproject.toml` version on `main` if its base already matches upstream,
  otherwise `<upstream>.1`) and decides whether a release is needed by
  checking for the `v<target>` tag. The tag is the completion marker because
  `build.yml` creates it only after PyPI upload succeeds. A missing tag
  therefore retries an interrupted build safely: an existing PyPI version
  skips upload and creates only its missing tag.
- Before changing `main`, a macOS runner refreshes Homebrew and verifies that
  Homebrew curl has reached the required base version. If Homebrew is behind
  (or unavailable), the workflow logs a warning and makes no commit or
  dispatch; the following weekly run tries again. This prevents a release
  from starting and later failing after the non-macOS wheels have built.
- If `pyproject.toml` needs bumping it updates it, adds a
  "Updated to libcurl" entry to `docs/changelog.md`, commits and pushes to
  `main` *before* anything is built. It fast-forwards `devel` only when
  `devel` is already an ancestor of that commit. A diverged `devel` is left
  untouched and reported as a warning, avoiding a force-push or automatic
  merge commit.
- Dispatches `build.yml` on `main` with the exact commit SHA. Pushes made with
  `GITHUB_TOKEN` do not trigger the push-based `build.yml`, so the explicit
  dispatch is the only build and there is no double release.

### Safe dry run

Use **Actions → Check upstream LibCURL → Run workflow**, enable `dry_run`, and
run it on `main`. The workflow obtains the upstream tag, calculates the target
and tag state, checks the macOS Homebrew prerequisite when a release is due,
and logs the files it would commit plus the exact existing SHA (or the parent
of the new bump commit) it would build. It does not commit, push, dispatch a
workflow, upload to PyPI, or tag. To exercise the wheel build separately,
dispatch **Build wheels** on `main` with `dry_run` enabled and, optionally,
the SHA reported by the upstream check. That run builds and verifies all
artifacts but logs instead of uploading or tagging.

### Operator recovery

- If the upstream check reports that `main` changed while it was queued, run
  the dry run again and then retry the normal workflow; never reuse a stale
  plan.
- If Homebrew is behind, wait for the next weekly run (or retry after
  Homebrew has published the matching curl). Do not bypass the macOS check.
- If the version bump reaches `main` but `devel` is reported as diverged,
  merge `main` into `devel` through the normal review process; the release
  itself can continue because its SHA is already fixed.
- If a build fails before PyPI upload, rerun `build.yml` with the reported
  SHA. If upload succeeds but tagging fails, rerun the same SHA: the PyPI
  check skips the duplicate upload and creates the missing tag.
- If a tag exists while PyPI does not, stop and investigate before releasing:
  that state should not be produced by this flow because tagging is last.

### Required repository settings
- Settings → Actions → Workflow permissions must allow read and write (the
  job requests `contents: write` and `actions: write`).
- If `main` (or `devel`) is branch-protected, `github-actions[bot]` must be
  allowed to push directly, otherwise the bump push fails; use a ruleset
  bypass for GitHub Actions or a deploy key/PAT secret.
- The PyPI trusted publisher stays configured for `build.yml` (the build is
  dispatched, not called as a reusable workflow, so no PyPI change is needed).

---

## Dependabot

Dependabot (`.github/dependabot.yml`) checks GitHub Actions versions and the
tracked `uv.lock` (`uv` ecosystem) monthly, grouping minor and patch lock
updates. This keeps pinned action versions (e.g. `actions/checkout`) and
locked dependencies current without manual tracking.

The `uv` ecosystem has ignore rules because mcurl still supports Python 3.9,
which newer releases of some dependencies dropped. Dependabot's uv updater
upgrades one package at a time to a forced version, and uv treats that as a
hard requirement for every supported Python, so such a bump cannot resolve on
3.9. Ignored updates:

- `cffi`: it appears in `[build-system] requires`, which Dependabot cannot
  update, and its runtime release needs Python 3.10+. Bump it by hand when
  support for 3.9 is dropped.
- `pytest` 9.x and later: requires Python 3.10+; 8.x stays on 3.9.
- `jsonschema` 4.18 and later: `jsonschema<4.18` is pinned on purpose for PyPy
  (see `docs/build.md`).

`uv lock` itself still resolves a separate version per Python version, so the
lock keeps working on 3.9-3.15 without these ignore rules.

---

## Deployment flow

1. Push to `devel` or `working` → CI runs the fast lane (quality + fast matrix)
2. Before a release, dispatch `main.yml` with `full` and `build.yml` with
   `dry_run` on `devel` to run the full matrix and every release wheel
3. If CI green → merge `devel` → `main`
4. Push to `main` → CI runs + `build.yml` auto-publishes to PyPI + tags release
5. New libcurl releases need no manual step: `check-upstream.yml` commits the
   bump to `main` (and `devel`) and dispatches the release build weekly.

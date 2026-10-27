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
(and a manual dispatch without `full`). Tests run with `-m "not slow"` (see
`docs/testing.md` for marker definitions) except where noted:

| Job | Configuration | Why it is in the fast lane |
|-----|---------------|----------------------------|
| `tests` | Linux x86_64 CPython 3.9 | oldest supported CPython |
| `tests` | Linux x86_64 CPython 3.15 | newest supported CPython |
| `tests` | Linux x86_64 PyPy 3.11 | PyPy-only path (built-in cffi, no cffi install) |
| `tests` | Linux aarch64 CPython 3.15 | only arm64 Linux code path outside the release build |
| `tests` | macOS 15 arm64 CPython 3.15 | only Homebrew-libcurl path and `DYLD_LIBRARY_PATH`/rpath setup |
| `tests-windows` | Windows AMD64 cp315 | newest CPython on Windows; mingw32 build and delvewheel repair |
| `tests-i686` | manylinux i686 cp315, **full suite** | 32-bit address-space limit; never skips slow tests (see [docs/testing.md](testing.md#wheel-smoke-selection-and-test-skip)) |

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
- musllinux wheels (cibuildwheel, `tests-musl`): x86_64 on `ubuntu-latest` and
  aarch64 on `ubuntu-24.04-arm`, each cp315 with the full suite including slow
  tests. The release build only smoke-tests musllinux wheels, so these two
  jobs are the only place the full suite runs on musl. They are not in the
  fast lane.

**What is tested before a release.** Three kinds of coverage exist, and no
single run gives the full suite on every shipped wheel:

| Coverage | Where | What it covers |
|----------|-------|----------------|
| Full suite (slow tests included) | Full lane above | The interpreters and platforms listed there. Not covered: aarch64 CPython 3.10-3.12, i686 CPython 3.10-3.13, Windows CPython 3.10 and 3.11, PyPy 3.12, PyPy 3.11 on i686, free-threaded `cp315t` on macOS and aarch64 |
| Wheel smoke selection | `build.yml` (release build and dry run) | Installed wheels of the oldest CPython, the newest CPython and free-threaded 3.15 on each platform that builds them (including musllinux and macOS), and PyPy 3.11 on the glibc Linux platforms (x86_64, i686 and aarch64) |
| Build only | `build.yml` | Every other wheel identifier, including PyPy 3.12 on Linux. Build-only wheels are built and repaired, but their test step is skipped; they are uploaded to PyPI only by a non-dry-run release build on `main`. A dry run builds and repairs them without uploading or tagging |

The release gate relies on the combination: every supported OS,
architecture and libc is built and smoke-tested by the dry run, and the
interpreters outside the full suite rely on the abi3 wheel being one binary
across CPython versions. The rationale for the smoke
subset and the i686 exception is in
[docs/testing.md](testing.md#wheel-smoke-selection-and-test-skip). The weekly
full lane runs from the default branch, so the gate does not depend on
push-lane timing.

Job totals: the fast lane runs 9 jobs (`plan`, `quality` and 7 test jobs); the
full lane runs 34 (`plan`, `quality` and 32 test jobs: 20 `tests`, 6
`tests-windows`, 4 `tests-i686`, 2 `tests-musl`).

### Rationale and baseline

GitHub's macOS runners are significantly more expensive and slower than Linux
or Windows runners, with restricted parallelism: each macOS job takes 5–6 min
compared to 2–3 min on Linux, and earlier CI runs spent 15–20 min in
the Homebrew update step alone. mcurl is a small project, so the full matrix
on every push costs more than it finds; see
[docs/testing.md](testing.md#contributor-testing-strategy).

Before the split, devel CI run 40019820063 (2026-10-23) ran 29 jobs for every
push (seven macOS jobs among them) and took 15 min 15 s total. Running a
separate MacOS job per Python version (3.9–3.15) on every push was wasteful:
each Python version duplicates the same library-bundling and test-framework
code paths, yet a single macOS job per push exercises the entire Homebrew
libcurl build chain and the same OS-specific linker rpath setup used in the
release wheel.

The fast lane reduces routine CI from 29 jobs to 9 (7 test jobs, one per
platform/architecture and Python configuration that exercises a distinct code
path, including one macOS job), while `main.yml` and `build.yml` still test all
supported Python versions and confirm that slow tests pass on a full matrix
before every release. The weekly full lane (34 jobs) and the `build.yml` dry
run before a release together cover every shipped OS, architecture and libc
(see the coverage table above for what is tested versus build-only), and the
full lane runs the slow tests that time-bound MCurl multi-handle concurrency
and auth-retry recovery. This separation ensures every platform- and architecture-specific code path
runs on every push, while full coverage is verified before release rather than
spent on every commit.

### Homebrew curl on macOS

`tools/brew_curl.sh` (used by `main.yml` and `build.yml`) replaces the
previous unconditional `brew update && brew upgrade curl`. It disables
Homebrew's automatic update, cleanup and dependents checks, and compares the
preinstalled curl with the libcurl base version in `pyproject.toml`. Only on a
mismatch does it read the current curl version from the Homebrew formula API
(`formulae.brew.sh`); if the API cannot be read or the version is not the
expected one it prints a GitHub `::error` line and fails immediately,
otherwise it runs `brew update --auto-update` (falling back to a plain
`brew update`) and upgrades (or installs) curl alone. This is a real Homebrew
update, not an API-metadata-only refresh: it still git-fetches Homebrew/brew
itself, which is the slow step, and it skips the core tap git update only
because Homebrew runs in API mode. A final hard assertion that installed curl equals the
pyproject base version is kept, because a dependency-link warning can make a
successful upgrade exit nonzero and because a stale curl must never be used
to build or test. `check-upstream.yml` keeps its own Homebrew check.

The fast path leaves curl's dependency bottles (openssl, nghttp2, ...) as the
runner image ships them when curl already has the expected version, which is
fine for tests but not for a release wheel that bundles them. `build.yml`
therefore passes `--release` (or `BREW_CURL_RELEASE=1`): release mode always
runs the same Homebrew update and upgrades any outdated formula among
`brew deps --installed curl`, then re-reads curl and applies the same exact
version assertion. `main.yml` keeps the fast path.

### Collecting after numbers

These require GitHub and must be run by the operator:

1. Fast lane: push the change to `working` (or `devel`), then
   `gh run list -R genotrance/mcurl -w CI -b working -L 1` and
   `gh run view <id> -R genotrance/mcurl --json jobs --jq '.jobs[] | [.name, .startedAt, .completedAt] | @tsv'`.
   Expect 1 plan + 1 quality + 7 test jobs (9 total), and check the macOS
   "Ensure Homebrew libcurl" step duration in `gh run view <id> --log` output.
2. Full lane on demand: `gh workflow run main.yml -R genotrance/mcurl --ref devel -f full=true`,
   then the same `gh run view` query; expect 32 test jobs (including
   `test windows 3.15t`, `test linux x86_64 3.15t` and the two
   `test musllinux <arch> 3.15` jobs).
3. Schedule: after the first Saturday 03:00 UTC run,
   `gh run list -R genotrance/mcurl -w CI -e schedule -L 1`.
4. Release matrix: `gh workflow run build.yml -R genotrance/mcurl --ref main -f dry_run=true -f sha=<candidate>`
   (the release checklist below explains why `main`) and confirm every `Build <os> <arch>` job passed and the artifacts include
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
  i686 wheels; the rationale is in
  [docs/testing.md](testing.md#wheel-smoke-selection-and-test-skip).
- Overrides the `pyproject.toml` cibuildwheel `test-command` and `test-skip` so
  it always runs the full test suite instead of the wheel smoke selection.

**`tests-musl`** (full lane only; named `test musllinux <arch> <python>`):
- Builds and tests cp315 musllinux wheels via cibuildwheel on x86_64
  (`ubuntu-latest`) and aarch64 (`ubuntu-24.04-arm`), following the i686 job
  pattern: runs the full test suite, not the `smoke` selection.
- Added because the release build (`build.yml`) only smoke-tests wheels; the
  full suite on musl runs only in this job (see `docs/testing.md` for test
  markers).

### Wheel smoke selection rationale

`build.yml` runs only the `smoke` selection, on a subset of wheel identifiers.
The subset, why it is enough, and why i686 is the exception are explained once
in [docs/testing.md](testing.md#wheel-smoke-selection-and-test-skip); the
`test-skip` list itself is in `pyproject.toml`. The coverage table in the
`main.yml` section above shows which combinations are tested versus build-only.

### Test command and import mode

All cibuildwheel test commands report skips (`-rs`) and use pytest's
`importlib` import mode. In the default prepend mode `tests/__init__.py` puts
the checkout on `sys.path`, so the tests would import `mcurl` from the source
tree instead of the installed wheel. `tests/test_smoke.py` additionally fails
under `CIBUILDWHEEL` unless `mcurl` and `_libcurl_cffi` come from
site-packages, and the bundled-libcurl check fails instead of skipping.
Source-tree runs in `tests` keep the default import mode.

The composite action `.github/actions/setup-python-env`:
- Sets `allow-prereleases: true` so that current CPython releases and PyPy can
  be installed before GitHub runner images provide them directly.
- Installs `uv`, syncs dependencies with `uv sync --locked` (fails if `uv.lock` is stale), and builds the extension.
- For PyPy: skips installing `cffi` from PyPI (PyPy ships its own built-in cffi).

The library search path for test time is set by steps in the `tests` job of
`main.yml`, after the action runs, not by the action:
- On Linux: reads the base libcurl version from `pyproject.toml`, then sets
  `LD_LIBRARY_PATH` to the matching `genotrance` jbb download so the extension
  can load. This avoids relying on a prior build having populated the same jbb
  cache.
- On macOS: sets `DYLD_LIBRARY_PATH` to point at the Homebrew libcurl
  directories. At build time, rpaths are embedded via `-Wl,-rpath` so installed
  wheels do not need `DYLD_LIBRARY_PATH` at runtime.

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
- **musllinux** wheels get only the smoke selection in `build.yml` (and only
  the identifiers not listed in `test-skip`). The full suite, including slow
  tests, runs on musl only in the `main.yml` full lane (`tests-musl`: x86_64
  and aarch64, cp315), via cibuildwheel inside the musllinux containers.
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
4. **`px-scenarios`** — downloads the x86_64 Linux candidate wheel, installs
   it into px's `devel` checkout, and runs px's Docker-backed corporate proxy
   scenario suite with `PX_REQUIRE_MCURL_FIX=1`. It runs for every release and
   dry run; a failure prevents publication.
5. **`publish`** — first verifies that the resolved release version matches
   `pyproject.toml` at the release commit (the `sha` input when given,
   otherwise the triggering commit) and that every artifact carries that
   version, so a tag can never point at a commit declaring a different
   version. It then checks PyPI: if the version is already published the
   upload is skipped (never uploading twice) and only the missing tag is
   created; otherwise it uploads via
   [trusted publisher](https://docs.pypi.org/trusted-publishers/) and tags
   the release commit only after the upload succeeded.

Publishing only happens for `main`. `workflow_dispatch` accepts `dry_run` in
addition to `sha`. A dry build never uploads a distribution or creates a tag,
and its own concurrency group means it cannot delay a real release. What it
validates depends on the ref:

- On any ref (for example `devel`): the version resolution in `setup`, the
  sdist build and the complete wheel matrix including macOS `--release`
  Homebrew refresh, repair and the smoke tests. The px corporate-proxy scenario
  gate also runs. The `publish` job is skipped entirely because it runs only
  on `main`.
- On `main`: additionally the `publish` job's committed-version check, the
  artifact-version check, the PyPI state check and the dry-run publish plan
  notice, still without uploading or tagging.

The publish-job validations are therefore not exercised by a `devel` dry run;
do a `main` dry run when they matter (for example after changing that job).

The release build runs the short smoke selection only, on a subset of wheel
identifiers configured in `pyproject.toml` and explained in
[docs/testing.md](testing.md#wheel-smoke-selection-and-test-skip). All wheels
are still built; the rest are build-only.

The `skip` list in `pyproject.toml` excludes unsupported configurations
(free-threaded 3.14, musllinux i686, and PyPy on macOS and Windows, so PyPy
wheels are Linux-only; see [docs/build.md](build.md#cibuildwheel-configuration)).
CI uses cibuildwheel 4.3.4 because it recognizes the CPython
3.15 and free-threaded CPython 3.15 identifiers used by the release build and
focused wheel tests.

---

## Release checklist

Run this checklist before merging a release from `devel` into `main`. It
combines the checks that protect the release build with an integration check
against px, which consumes pymcurl in production.

1. Pick the exact release-candidate SHA (`git rev-parse devel`) and require
   both gates below to pass on that SHA. Release wheels are only
   smoke-tested, so these runs are the real test coverage, and the fast push
   lane is not a substitute.
   - **Full `main.yml` run**: the complete test matrix with slow tests enabled
     (see the coverage table above for what it includes):
     `gh workflow run main.yml -R genotrance/mcurl --ref devel -f full=true`,
     then find it with `gh run list -R genotrance/mcurl -w CI -b devel -e workflow_dispatch -L 1`
     and confirm with `gh run view <id> -R genotrance/mcurl --json headSha,conclusion`
     that `headSha` equals the candidate and every job passed. A weekly
     scheduled run only counts if its `headSha` is the candidate.
   - **`build.yml` dry run on `main`** with the candidate as `sha`:
     `gh workflow run build.yml -R genotrance/mcurl --ref main -f dry_run=true -f sha=<candidate>`,
     then `gh run list -R genotrance/mcurl -w "Build wheels" -L 1` and
     `gh run view <id> -R genotrance/mcurl` to confirm the sdist, all
     `Build <os> <arch>` jobs, the `px-scenarios` gate, and the `publish`
     job's dry-run checks passed.
     The ref must be `main` because only a `main` run executes the `publish`
     job (committed-version, artifact-version and PyPI-state checks); `sha`
     makes it build the candidate, not main's head. `main`'s copy of
     `build.yml` must already support `dry_run` and `sha`; if it does not,
     stop and resolve that before releasing.
2. Build the release candidate (the exact SHA from step 1) locally with the
   Makefile, using the exact libcurl base version from `pyproject.toml`, and
   run mcurl's full test suite against it. Ensure that the mcurl version built matches
   `pyproject.toml` exactly.
3. Confirm the `px-scenarios` job passed in the `build.yml` dry run. It
   installs the candidate Linux wheel into px's `devel` checkout and runs
   px's Docker-backed corporate proxy scenarios, including HTTP and HTTPS
   CONNECT proxy authentication. This automated gate replaced the manual px
   suite run: any scenario failure blocks the release. The HTTPS proxy-auth
   regression shipped in 8.23.0.1 was caught in px testing post-release; this
   gate prevents that class of regression from reaching publication.
4. Download the dry-run artifacts
   (`gh run download <id> -R genotrance/mcurl -D dry-run`) and compare the
   wheel platform tags with the previous release on PyPI. Verify that `i686`,
   `aarch64`, `arm64`, `x86_64`, `musl*` wheels are present for each
   architecture. Runner or libcurl version changes can shift platform tags
   (e.g., the macOS arm64 runner moved to `macos-15` in 8.23.0.1, changing
   the tag from `macosx_14_0_arm64` to `macosx_15_0_arm64`); unexpected tag
   changes must be understood before release.
5. Confirm that the macOS dry-run job's Homebrew step passed: the installed
   curl version equals the libcurl base version in `pyproject.toml` (confirmed
   by the step's final assertion), and release mode (`--release` flag) refreshed
   curl's dependencies. The macOS arm64 wheel bundles Homebrew's curl and its
   dependency closure, so a mismatch in the Homebrew curl version can produce a
   wheel for a different libcurl than the declared release version.
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
2. Before a release, complete the [release checklist](#release-checklist): a
   full `main.yml` run, a `build.yml` dry run on `main` with the candidate
   SHA, and px tested against that candidate
3. If the checklist passes → merge `devel` → `main`
4. Push to `main` → CI runs + `build.yml` auto-publishes to PyPI + tags release
5. New libcurl releases need no manual step: `check-upstream.yml` commits the
   bump to `main`, fast-forwards `devel` only when `devel` is an ancestor of
   that commit (otherwise it warns and leaves `devel` alone), and dispatches
   the release build weekly.

# Changelog

---

## v8.19.0.1 — TBD

- Updated to libcurl v8.19.0.
- Fixed cffi build failure on macOS caused by curl 8.19.0 changing
  `CURLAUTH_ANY` / `CURLAUTH_ANYSAFE` macros to use an explicit
  `((unsigned long)0xffffffff)` mask.
- Removed compatibility code for libcurl < v7.45 since mcurl bundles its own
  libcurl binary.
- Added `mcurl.libcurl` and `mcurl.ffi` as public module-level exports for
  direct access to the cffi bindings.
- Added `Curl.strerror()` and `MCurl.strerror()` static methods for
  human-readable error messages.
- Added generic `setopt()` and `getinfo()` methods on `Curl` with automatic
  type conversion — Python str, int, and bool values are converted to the
  correct cffi type based on the CURLOPT/CURLINFO constant.
- Added `unsetopt()` to reset a curl option to its default.
- Added `set_timeout()` for overall transfer timeout.
- Added `set_httpauth()` and `set_bearer_token()` for HTTP authentication.
- Added cookie management methods: `set_cookie()`, `load_cookies()`,
  `save_cookies()`, `add_cookie()`, `clear_cookies()`, `remove_cookie()`,
  and `get_cookies()`.
- Added `set_maxredirs()` and `set_postredir()` for redirect control.
- Added `set_encoding()` for content encoding negotiation.
- Added `dup()` to duplicate a curl handle.
- Added `pause()` and `unpause()` for transfer flow control.
- Added `set_xferinfo()` for transfer progress callbacks.
- Added `set_seek()` for seek callbacks; automatically registered by
  `buffer()` when upload data is provided.
- Added context manager support (`with` statement) for both `Curl` and
  `MCurl`.
- Added `get_effective_url()`, `get_response_code()`, `get_content_type()`,
  and `get_total_time()` convenience getters.

---

## v8.18.0.1 — 2026-02-28

### Breaking changes
- Dropped Python 3.8 support; minimum is now Python 3.9.

### New features
- Added configurable proxy authentication failure threshold with default of 3
  attempts before blocking a proxy. Use `MCurl.set_failure_threshold()` to
  configure.
- Added automatic version detection from
  [LibCURL_jll.jl](https://github.com/genotrance/LibCURL_jll.jl) releases.
- Added Python 3.14 support on all platforms.
- Added PyPy 3.10/3.11 support on Linux.
- Added Windows wheel builds via cibuildwheel with mingw32 toolchain.
- Wheels now use `cp39-abi3` tag (stable ABI) for CPython — one wheel per
  platform instead of one per CPython version.
- Modernised project tooling: GitHub Actions CI/CD, pre-commit, ruff, mypy,
  Makefile, `docs/` folder, and split test suite.

### Bug fixes
- Fixed [genotrance/Px#250](https://github.com/genotrance/Px/issues/250):
  only check proxy auth status when libcurl itself succeeded, as a non-OK
  `cerr` means the HTTP response code is unreliable.
- Fixed incorrect value for `CURLAUTH_ANY` and `CURLAUTH_ANYSAFE` on i686
  platforms (32-bit integer overflow).
- Fixed PyPy GC bug: cffi string pointers could be collected prematurely
  under PyPy's moving GC. Added `_keepalive` list to hold strong references
  for the lifetime of each `Curl` handle.

### Improvements
- Refined locking for better concurrency and thread safety: snapshot
  socket lists and timer under lock, perform `select()`/`sleep()` outside
  the lock so other threads can `add()`/`remove()` concurrently.

---

## v8.12.1.1 — 2025-03-08

- Fixed [genotrance/Px#243](https://github.com/genotrance/Px/issues/243):
  libcurl was incorrectly picking up `http_proxy` and `no_proxy` env variables.
- Fixed [genotrance/Px#223](https://github.com/genotrance/Px/issues/223):
  index error when accessing write data in select loop.
- Replaced debug-output-based proxy auth detection with
  `CURLINFO_PROXYAUTH_USED` (introduced in libcurl v8.12.0).
- Added `feature_names` method to check for libcurl features; added
  `get_curl_vinfo()` and `get_curl_features()`; included Python version in
  debug output.

---

## v8.11.0.0 — 2025-01-10

- Updated to libcurl v8.11.0.
- Fixed bug causing an error when downloading binary data.
- Fixed [genotrance/Px#214](https://github.com/genotrance/Px/issues/214):
  accessing uninitialized headers.
- Fixed [genotrance/Px#224](https://github.com/genotrance/Px/issues/224):
  allow custom certs in Windows by not using bundled CA certs.
- Replaced debug-output-based proxy detection with `CURLINFO_USED_PROXY`
  (introduced in libcurl v8.7.0).

---

## v8.6.0.1 — 2024-02-19

- Initial release.

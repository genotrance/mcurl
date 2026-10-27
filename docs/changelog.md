# Changelog

---

## v8.23.0.3 — TBD

### Bug fixes
- Proxy auth failures now count only the final CONNECT 407 response, not
  CONNECT handshakes cut short by network errors. A working NTLM/Negotiate
  proxy is no longer reported as failing (px#268). Transfers with such
  errors report the network error (502) instead of 401.
- A complete NTLM type2 challenge followed by a network cut no longer blocks
  a working proxy when libcurl resumes the connection-bound handshake by
  sending type3 on a new connection. mcurl retries once on a fresh connection
  and counts only that outcome; non-rewindable request bodies remain
  inconclusive (503) instead of being counted as bad credentials.
- A genuine final CONNECT 407 is now counted (401) even when an earlier
  socket failure occurred in the same transfer, such as a refused first
  address. The decision uses libcurl's error message instead of the OS errno.
- A failed proxy auth method is no longer cached: only a successful
  non-407 response caches the method, and a final 407 or
  `reset_proxy_failures()` clears the cached entry. Previously a failed
  `ANY` attempt could override the auth later requested and keep the proxy
  blocked even with `auth_retry`.
- A plain-HTTP 407 whose headers are cut off mid-response is inconclusive
  (502) rather than counted as an auth failure.
- Only an HTTP status line with code 407 starts upstream 407 header
  suppression; other headers merely containing "407" are passed to the client.
- With auth `ANY` on Linux/macOS, a proxy offering both Negotiate and NTLM no
  longer blocks after 3 requests when there is no Kerberos ticket. libcurl
  picks Negotiate and GSS-API fails locally without ever trying NTLM, which
  the proxy also offered; `MCurl.do()` now retries once with Negotiate
  removed from the auth mask so the next attempt uses NTLM, and only counts
  an auth failure if that attempt also fails. Windows SSPI already falls
  back to NTLM internally and is unaffected.
- POST/PUT request bodies sent through the read callback are now resent
  across proxy auth legs and the Negotiate-to-NTLM retry. With auth `ANY`
  libcurl sends the body on the first unauthenticated leg and rewinds to
  resend it; the read budget was not restored on rewind, so such requests
  failed with Curl error 26 (resp 503). A body source that cannot be
  rewound makes the NTLM retry inconclusive (503) rather than an auth failure.

### New features
- Added `Curl.get_os_errno()` and `Curl.get_errorbuffer()`.

### Internal
- NTLM proxy tests gate on `CURL_VERSION_NTLM` rather than platform or
  Python version, since Homebrew libcurl on macOS can omit NTLM.

---

## v8.23.0.2 — TBD

- macOS arm64 wheels now require macOS 15 or later (changed in 8.23.0.1).
  macOS 14 users receive the source distribution instead.
- Fixed a regression in v8.23.0.1 where `MCurl.do()` could stall for a full
  second after its transfer had already finished (when another thread
  completed it and libcurl had no sockets or timers pending). Concurrent
  HTTPS requests through authenticating proxies were delayed long enough for
  TLS servers with short handshake timeouts to drop the tunnel, causing curl
  errors 35/55.
- Added CPython 3.15 support on every supported wheel platform.
- `MCurl.do()` waits at most 1 second only while it is waiting on sockets;
  it no longer applies that cap when libcurl reports nothing to wait for.
- `auth_retry` treats stopped or callback-aborted probes, zero response
  codes, and non-407 CONNECT failures as inconclusive instead of counting
  them as authentication failures.

---

## v8.23.0.1 — TBD

- This release was yanked from PyPI: its HTTPS proxy authentication handling
  regressed under concurrent load (see v8.23.0.2). Use 8.23.0.2 or later.
- Updated to libcurl v8.23.0.
- Enabled TCP keepalive on all connections and a low-speed guard on non-CONNECT
  transfers, so a stalled HTTP response no longer occupies a worker
  indefinitely while CONNECT tunnels and long transfers keep no overall
  deadline. A plain-HTTP transfer that receives less than 1 byte/s for 120
  seconds (for example a silent long-poll or server-sent events stream) is now
  aborted; use `Curl.set_low_speed()` to change or disable this and
  `Curl.set_keepalive()` to tune keepalive.
- Added `MCurl.set_auth_retry(seconds, max_seconds=None)` so a proxy blocked after
  repeated 407s is retried after a seconds-based interval with exactly one
  probe request, even under concurrent load, and unblocked if it succeeds.
  Fractional seconds are supported; an optional max-seconds cap enables
  exponential backoff, while the default of 0 seconds disables retry.
  It also recognizes 407 responses to HTTPS proxy CONNECT requests. Added
  `MCurl.reset_proxy_failures()` to clear the block.

---

## v8.22.0.1 — 2026-10-04

- Updated to libcurl v8.22.0.

---

## v8.21.0.1 — 2026-07-21

- Fixed `MCurl.select()` busy-loop at 100% CPU after a partial `send()` on a
  CONNECT tunnel.  A typo (`not data` instead of `not wdata`) prevented sockets
  from being removed from `wlist` after their write queue drained, causing the
  loop to spin indefinitely and defeating the idle timeout (#10).

---

## v8.19.0.1 — 2026-04-07

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

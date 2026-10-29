# API Reference

---

## Module-level exports

`mcurl.libcurl` and `mcurl.ffi` expose the underlying cffi bindings for direct
access to the libcurl C API. These are the preferred way to access raw libcurl -
they work on both CPython and PyPy, and they are part of `__all__`.

## Module-level functions

### `curl_version()`
Get curl version as numeric representation (e.g. `0x081300` for 8.19.0).

### `get_curl_vinfo()`
Get the raw `curl_version_info_data` struct from libcurl.

### `get_curl_features()`
Get all supported feature names from version info data. Returns a set of
strings (e.g. `{"SSL", "SPNEGO", "Kerberos", ...}`).

### `print_curl_version()`
Display curl version information via `dprint()`.

### `getauth(auth)`
Return auth value for specified authentication string.

Supported values: see https://curl.se/libcurl/c/CURLOPT_HTTPAUTH.html

Skip the `CURLAUTH_` portion in input - e.g. `getauth("ANY")`.

Prefixes for proxy detection control:
- `NO` - avoid method: `NONTLM` -> `ANY & ~NTLM`
- `SAFENO` - avoid method from safe set: `SAFENONTLM` -> `ANYSAFE & ~NTLM`
- `ONLY` - support only that method: `ONLYNTLM` -> `ONLY | NTLM`

### Utility functions

These helpers convert between Python and cffi C types. They are mainly needed
when calling raw `mcurl.libcurl` functions directly. The `Curl.setopt()` and
`Curl.getinfo()` methods handle type conversion automatically.

| Function | Description |
|----------|-------------|
| `py2cstr(pstr)` | Convert Python string to `char *` |
| `py2custr(pstr)` | Convert Python bytes to `char *` |
| `py2clong(plong)` | Convert Python int to `long` |
| `py2cbool(pbool)` | Convert Python bool to `long` (0 or 1) |
| `cvp2pystr(cvoidp)` | Convert `void *` to Python string |
| `gethash(easy)` | Return hash value for easy handle (dict key) |
| `sanitized(msg)` | Hide user-sensitive data (auth headers, passwords) from debug output |
| `yield_msgs(data, size)` | Generator for curl debug messages |

Reverse conversion functions (C-to-Python) are not needed - `Curl.getinfo()`
infers the return type from the CURLINFO constant and converts automatically.

---

## `Curl` class

Helper class to manage a curl easy instance.

### Constructor

```python
Curl(url, method="GET", request_version="HTTP/1.1", connect_timeout=60)
```

- **method** - `GET`, `POST`, `PUT`, `HEAD`, `CONNECT`, `PATCH`, `DELETE`, etc.
- **request_version** - `HTTP/1.0`, `HTTP/1.1`, etc.
- **connect_timeout** - connection timeout in seconds.

### Static methods

| Method | Description |
|--------|-------------|
| `Curl.strerror(code)` | Return human-readable string for a CURLcode error value |

### Generic option / info access

These methods provide direct access to any libcurl option or info constant,
with automatic Python-to-C type conversion.

| Method | Description |
|--------|-------------|
| `setopt(option, value)` | Set any CURLOPT option; auto-converts Python str/int/bool to the correct cffi type |
| `set_keepalive(idle=30, interval=10, count=3)` | Configure TCP keepalive probes: idle and interval are seconds and count is probes; defaults are 30 seconds, 10 seconds, and 3 probes; `idle=0` disables keepalive |
| `set_low_speed(limit=1, seconds=120)` | Abort non-CONNECT transfers below `limit` bytes/second for `seconds`; defaults are 1 byte/second for 120 seconds; 0 for either value disables the guard |
| `getinfo(info)` | Get any CURLINFO value; returns `(CURLcode, value)` with auto type inference |
| `unsetopt(option)` | Reset a CURLOPT option to its libcurl default (best-effort) |

### Request setup

| Method | Description |
|--------|-------------|
| `set_headers(xheaders)` | Set request headers from a dict of `{name: value}` pairs |
| `set_useragent(useragent)` | Set the User-Agent header string |
| `set_timeout(seconds)` | Set total transfer timeout (0 = no timeout) |
| `set_encoding(encoding="")` | Request compressed responses; empty string = all supported (gzip, deflate, br, zstd) |
| `set_verbose(enable=True)` | Enable libcurl verbose output to stderr |
| `set_debug(enable=True)` | Enable verbose mode with debug callback routed through `dprint()` |

### I/O and data transfer

| Method | Description |
|--------|-------------|
| `buffer(data=None)` | Setup BytesIO buffers for `perform()`; pass `data` for upload (POST/PUT) bodies |
| `bridge(client_rfile, client_wfile, client_hfile)` | Bridge curl reads/writes to file-like objects. The request body is streamed from `client_rfile` once, never buffered; `client_rfile` only needs `read()` (a `seekable()`/`seek()` source can be rewound), and may be non-blocking - see [Non-blocking bridge() sources](#non-blocking-bridge-sources). Call `set_headers()` afterwards with `Content-Length`, or `Transfer-Encoding: chunked` for an unknown length. See [Streamed request bodies](#streamed-request-bodies) |
| `set_transfer_decoding(enable=False)` | Control transfer decoding (chunked, gzip); disable to let the client handle it |
| `get_data(encoding="utf-8")` | Return response body from `buffer()` |
| `get_headers(encoding="utf-8")` | Return response headers from `buffer()` |

### Execution and response

| Method | Description |
|--------|-------------|
| `perform()` | Execute the request using the easy interface (standalone, without multi) |
| `close()` | Release an auth-retry probe reservation held by this handle before it is run or reset |
| `get_response()` | Return `(CURLcode, response_code)` of completed request; uses CONNECTCODE for CONNECT method |
| `get_connect_response()` | Return `(CURLcode, HTTP status)` from a proxy CONNECT request |
| `get_os_errno()` | Return the OS errno of any socket failure during the transfer, 0 if none; it is not cleared when a later connect attempt succeeds, so it does not say how the transfer ended |
| `get_errorbuffer()` | Return libcurl's error message of the last failed operation (`CURLOPT_ERRORBUFFER`), empty if none |
| `get_response_code()` | Return the HTTP response code (convenience, no CURLcode) |
| `get_effective_url()` | Return the final URL after redirects |
| `get_content_type()` | Return the Content-Type header value, or None |
| `get_total_time()` | Return total transfer time in seconds (float) |
| `get_activesocket()` | Return `(CURLcode, socket_fd)` for this handle's active connection |
| `get_primary_ip()` | Return `(CURLcode, ip_string)` of the remote server |
| `get_used_proxy()` | Return `(CURLcode, bool)` indicating whether a proxy was used |
| `get_proxyauth_used()` | Return `(CURLcode, auth_bitmask)` for the proxy auth method that was negotiated |
| `get_num_connects()` | Return `(CURLcode, count)` of successful connections created for the transfer |

### Authentication

| Method | Description |
|--------|-------------|
| `set_httpauth(user, password=None, auth="ANY")` | Set HTTP server auth; auth accepts same strings as `getauth()` (ANY, BASIC, DIGEST, NTLM, NEGOTIATE, etc.) |
| `set_bearer_token(token)` | Set OAuth 2.0 Bearer token (sets HTTPAUTH to BEARER automatically) |

### Proxy and tunneling

| Method | Description |
|--------|-------------|
| `set_proxy(proxy, port=0, noproxy=None)` | Set proxy server; returns `False` if proxy has exceeded auth failure threshold |
| `set_auth(user, password=None, auth="ANY")` | Set proxy authentication credentials; call after `set_proxy()` to enable auth caching |
| `set_tunnel(tunnel=True)` | Enable or disable HTTP proxy tunneling (CONNECT method through proxy) |
| `set_insecure(enable=True)` | Disable SSL certificate and hostname verification |

### Cookies

| Method | Description |
|--------|-------------|
| `set_cookie(cookie)` | Set raw `Cookie:` header string (bypasses cookie engine) |
| `load_cookies(filename)` | Enable cookie engine and load cookies from file (empty string = engine only) |
| `save_cookies(filename)` | Set cookie jar file for saving cookies on cleanup |
| `add_cookie(cookie)` | Add a cookie to the engine (Netscape format, Set-Cookie header, or control command) |
| `remove_cookie(domain, path, name)` | Remove a specific cookie by domain/path/name |
| `clear_cookies()` | Erase all cookies from the in-memory cookie store |
| `get_cookies()` | Return `(CURLcode, cookie_list)` of all cookies in Netscape format |

### Redirect control

| Method | Description |
|--------|-------------|
| `set_follow(enable=True)` | Enable or disable following 3xx redirect responses |
| `set_maxredirs(count)` | Set maximum number of redirects to follow (-1 = unlimited, 0 = refuse all) |
| `set_postredir(bitmask)` | Keep POST method on redirects; use `CURL_REDIR_POST_301/302/303/ALL` constants from `mcurl.libcurl` |

### Callbacks

| Method | Description |
|--------|-------------|
| `set_xferinfo(callback)` | Set transfer progress callback `fn(dltotal, dlnow, ultotal, ulnow) -> int` |
| `set_seek(callback=None)` | Set seek callback `fn(offset, origin) -> int`; returns `CURL_SEEKFUNC_OK`(0)/`FAIL`(1)/`CANTSEEK`(2). A rewind to offset 0 restores the declared Content-Length, so the body is resent across proxy auth legs; without a working seek, the Negotiate-to-NTLM retry of a request with a body is skipped (inconclusive, 503) |

### Handle duplication and lifecycle

| Method | Description |
|--------|-------------|
| `reset(url, method, request_version, connect_timeout)` | Reset and reuse this handle for another request (clears all libcurl options, re-applies mcurl defaults) |
| `dup(url=None)` | Clone this handle into a new `Curl` instance with all libcurl options preserved - useful for template-based workflows |
| `pause(bitmask)` | Pause send/receive on the handle |
| `unpause()` | Resume all paused transfers |
| `resume_upload()` | Thread-safe: resume a request body paused by a `bridge()` read source returning `CURL_READFUNC_PAUSE`. See [Non-blocking bridge() sources](#non-blocking-bridge-sources) |
| `resume_download()` | Thread-safe: resume a response paused by a `bridge()` write sink returning `CURL_WRITEFUNC_PAUSE`. See [Non-blocking bridge() sources](#non-blocking-bridge-sources) |

### Context manager

`Curl` supports use as a context manager. On exit, the handle is cleaned up:

```python
with mcurl.Curl("http://example.com") as c:
    c.buffer()
    c.perform()
    print(c.get_data())
```

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `easy` | cffi handle | Underlying `CURL *` handle |
| `easyhash` | str | Unique hash for dict keying |
| `url` | str | Request URL |
| `method` | str | HTTP method |
| `proxy` | str | Proxy host (if set) |
| `auth` | int | Auth method value (if set) |
| `cerr` | int | Last curl error code |
| `done` | bool | Whether transfer is complete |
| `errstr` | str | Accumulated error string |
| `resp` | int | HTTP response code |
| `is_easy` | bool | If `True`, `MCurl.do()` uses easy perform instead of multi |
| `is_connect` | bool | `True` for CONNECT method |
| `is_tunnel` | bool | `True` if tunneling through proxy |

---

## `MCurl` class

Helper class to manage a curl multi instance.

### Constructor

```python
MCurl(debug_print=None)
```

Pass a callable as `debug_print` to enable debug output (e.g. `MCurl(debug_print=print)`).

### Proxy auth failures

When a request with proxy auth configured receives a final 407, the proxy's
failure count increases; any other response resets it. Once the count reaches
the failure threshold, `Curl.set_proxy()` returns `False` for that proxy. By
default this lasts until `reset_proxy_failures()` is called. With an auth
retry interval set, the first `set_proxy()` call after the interval becomes a
probe while every other caller is still refused, so a burst of requests sends
only one authentication attempt upstream. The probe's outcome in `do()`
decides what happens next: a non-407 response unblocks the proxy, a 407 blocks
it for another (possibly doubled) interval, and a libcurl error blocks it for
the same interval. A probe handle that is reset or discarded before `do()`
completes frees the slot for the next caller. This state is locked within one
`MCurl` instance and process-local, so independent workers can each send one
probe per interval. Proxy CONNECT responses with status 407, including HTTPS
proxy CONNECT failures, count as authentication failures. Stopped or
callback-aborted probes, zero response codes, and non-407 CONNECT failures
are inconclusive and do not count.

NTLM and Negotiate are connection-bound. If libcurl closes after a complete
NTLM type2 response and then resumes by sending type3 on a newly connected
proxy socket, the resulting fresh 407 is not treated as a rejected login.
`do()` retries that request once with a fresh, non-reusable connection and
counts only the retry's final result. Requests with a body are retried only
when its read callback can rewind to offset zero; otherwise the result is
inconclusive (503). The detection combines libcurl's `NTLM handshake
rejected` diagnostic with `CURLINFO_NUM_CONNECTS > 1`; the latter counts only
successful connections, so failed happy-eyeballs addresses cannot conceal a
genuine 407.

With auth `ANY` on Linux/macOS, a proxy offering both Negotiate and NTLM
makes libcurl pick Negotiate; without a Kerberos ticket, GSS-API fails to
produce a token and libcurl surfaces the proxy's original 407 without ever
trying NTLM. `do()` detects this case (`get_proxyauth_used()` returns
`CURLAUTH_NEGOTIATE` on a final 407) and retries the request once with
Negotiate removed from the auth mask, so the retry uses NTLM; only a failure
of that retry counts towards the threshold. Windows SSPI already falls back
to NTLM internally, so this never triggers there.

### Streamed request bodies

`bridge()` reads the request body from `client_rfile` through libcurl's read
callback for every method that carries one (POST, PUT, PATCH, DELETE or any
other verb). With `Content-Length` exactly that many bytes are streamed (short
reads are fine); with `Transfer-Encoding: chunked` and no length the source is
read until EOF and sent chunked. Before 8.23.0.3 a chunked body was silently
dropped and PATCH copied the whole body into memory with
`CURLOPT_COPYPOSTFIELDS`.

A body source like px's client socket cannot rewind, yet proxy
authentication may need several legs, and libcurl resends the body after any
leg that ends in 407. mcurl therefore makes sure the body is only sent on a
leg that can succeed:

- `bridge()` installs a seek callback. A rewind to offset 0 succeeds when the
  source is seekable or nothing has been read yet, otherwise it returns
  `CURL_SEEKFUNC_CANTSEEK`, so libcurl never resends a partial body.
- Proxy auth not known yet (no cached method for the proxy, or a mask with
  several bits such as `ANY`, `NONEGOTIATE` or `ONLY*`): libcurl sends the
  first leg without credentials and, without help, with the whole body. mcurl
  sends `Expect: 100-continue`, so the proxy's 407 arrives before any body
  byte. libcurl then closes that probe connection (one extra TCP connection)
  and retries with the picked method. The picked method is cached even if
  the request later fails, so the next upload starts the handshake upfront.
- Known NTLM or Digest: libcurl sends the first leg with `Content-Length: 0`
  and the body only on the final leg, and Basic is preemptive, so mcurl keeps
  suppressing `Expect` and no extra round trip is paid. Authenticated
  keep-alive connections are reused as before.
- Known Negotiate: libcurl always sends the body with the token, because it
  considers Negotiate done after the first token. A single-leg Kerberos
  exchange is fine, but Windows SSPI can fall back to NTLM inside SPNEGO and
  need another leg, which would consume the body. mcurl sends
  `Expect: 100-continue` for Negotiate too: a Kerberos proxy answers 100 and
  the body follows on the same leg (one round trip), while a continuation
  407 arrives before the body, so the failure is clean and the body is never
  half-sent.
- Seekable sources (`buffer()`, `set_seek()`) keep `Expect` suppressed and are
  rewound by libcurl as needed.

Unsupported cases:

- A non-seekable body, unknown auth, and a proxy that ignores `Expect` (no
  100 or 407 within libcurl's 1 second wait): libcurl sends the body on the
  probe leg and cannot resend it. `do()` reports resp 503 with the error text
  `mcurl.REWIND_ERROR` ("Request body cannot be resent ..."), and the result
  is inconclusive, never counted as an auth failure. The picked method is
  cached, so retrying the request succeeds.
- NTLM through a proxy that closes the connection after each response: NTLM
  is connection-bound and cannot complete, with or without a body.

### Non-blocking bridge() sources

`read_callback` and `write_callback` used to run `client_rfile.read()` and
`client_wfile.write()` inside `_socket_action()` while `MCurl._lock` was held,
so a `client_rfile` whose `read()` blocks waiting for more data from its own
client (for example a streaming request body fed from a socket) froze every
other transfer on the same `MCurl` for as long as that read took. `bridge()`
sources and sinks can now be non-blocking instead:

- `client_rfile.read(n)` returns `None`, or raises `BlockingIOError`, to mean
  "no data yet" without blocking. mcurl returns `CURL_READFUNC_PAUSE` to
  libcurl, which pauses sending on that handle; no bytes are read or buffered.
  EOF is unchanged: an empty `bytes` return (`b""`) ends the body as before.
- `client_wfile.write(buf)` returns `None`, or raises `BlockingIOError`, to
  mean the downstream client cannot accept more right now. mcurl returns
  `CURL_WRITEFUNC_PAUSE`; libcurl buffers that chunk internally (not mcurl)
  and redelivers it to `write_callback` once resumed.
- Resume from any thread with `curl.resume_upload()` (clears the paused read
  direction) or `curl.resume_download()` (clears the paused write direction),
  or the lower-level `MCurl.resume(curl, bitmask=None)` (`bitmask` is
  `CURLPAUSE_SEND` and/or `CURLPAUSE_RECV`; `None` clears both). `curl_easy_pause()`
  itself is only ever called by the thread currently driving the multi handle
  inside `_perform()`, while it holds `MCurl._lock`, since libcurl documents
  it as unsafe to call concurrently with a running transfer; `resume()` from
  any other thread only records the request and wakes that thread (an
  internal `socketpair()`, not `curl_multi_wakeup()`, since mcurl polls
  sockets with `select()` directly rather than through `curl_multi_poll()`),
  so a resume does not wait out the current `select()` timeout.
- A handle that is paused indefinitely still honours `CURLOPT_TIMEOUT` /
  `set_timeout()` (the timer that drives it is independent of pause state) and
  is removed cleanly by `remove()`/`stop()`; neither waits for the source. A
  source that fails while paused never gets another callback until resumed or
  removed, so the caller ends the transfer itself (`stop()`) once it knows the
  source failed, rather than it hanging forever.
- Only meaningful with the multi interface (`MCurl.do()`/`add()`); pausing a
  handle run through the easy interface (`Curl.perform()`, `is_easy=True`)
  blocks the calling thread with no event loop to resume it from, so a
  non-blocking source should not be used that way.
- Remaining limit: a resume is only guaranteed to cut short an in-progress
  `select()` wait on that handle's own sockets. A handle with no socket
  registered yet (the brief window before it connects) and a pending libcurl
  timer falls back to a plain `time.sleep()` that a resume does not
  interrupt; this never applies to an already-connected, paused upload or
  download, which always has a registered socket.

### Static methods

| Method | Description |
|--------|-------------|
| `MCurl.strerror(code)` | Return human-readable string for a CURLMcode error value |

### Methods

| Method | Description |
|--------|-------------|
| `setopt(option, value)` | Configure a multi option (socket/timer callbacks are reserved by mcurl) |
| `set_failure_threshold(threshold)` | Set the number of authentication failures before blocking a proxy (default: 3); 0 is invalid |
| `set_auth_retry(seconds, max_seconds=None)` | Retry a blocked proxy after `seconds` with a single probe request; values are seconds and may be integers or floats, including fractions. Failed probes double the wait up to `max_seconds`; `None` keeps a fixed interval. The default is 0 seconds, which never retries; negative values are invalid. |
| `reset_proxy_failures(proxy=None)` | Clear auth failure state for one proxy, or all proxies if `None` |
| `add(curl)` | Add a `Curl` handle to the multi instance for concurrent execution |
| `do(curl)` | Add a `Curl` handle and perform until completion; returns `True` on success |
| `remove(curl)` | Remove a completed `Curl` handle from the multi instance |
| `stop(curl)` | Abort a running `Curl` handle and remove it from the multi instance |
| `resume(curl, bitmask=None)` | Thread-safe: queue `curl` to resume a paused direction (`CURLPAUSE_SEND` and/or `CURLPAUSE_RECV`; `None` clears both) and wake the thread driving the multi handle. See [Non-blocking bridge() sources](#non-blocking-bridge-sources) |
| `select(curl, client_sock, idle=30)` | Run a bidirectional select loop between a client socket and a CONNECT tunnel |
| `close()` | Stop all running transfers and close the multi handle |

### Context manager

`MCurl` supports use as a context manager. On exit, `close()` is called:

```python
with mcurl.MCurl() as m:
    c = mcurl.Curl("http://example.com")
    c.buffer()
    m.do(c)
    print(c.get_data())
```

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `handles` | dict | Active `{easyhash: Curl}` handles |
| `proxyauth` | dict | Cached `{proxy: auth_method}` values |
| `failed` | dict | `{proxy: failure_count}` for auth failures |
| `failure_threshold` | int | Max auth failures before blocking (default: 3) |
| `auth_retry` | float | Seconds before a blocked proxy gets a probe request (default: 0, never) |
| `auth_retry_max` | float or None | Optional maximum seconds for the doubled retry interval after failed probes (default: None, fixed interval) |

---

## Raw libcurl access

The libcurl C API can be accessed directly via `mcurl.libcurl` and `mcurl.ffi`:

```python
from mcurl import ffi, libcurl

url = "http://httpbin.org/get"
curl = ffi.new("char []", url.encode("utf-8"))

easy = libcurl.curl_easy_init()
libcurl.curl_easy_setopt(easy, libcurl.CURLOPT_URL, curl)
cerr = libcurl.curl_easy_perform(easy)
```

The legacy `from _libcurl_cffi import lib as libcurl` import still works but
`mcurl.libcurl` / `mcurl.ffi` is preferred.

---

## Debug output

Set `mcurl.dprint` to a callable to receive debug messages:

```python
import mcurl
mcurl.dprint = print
```

All debug messages include the easy-handle hash for correlation in
multi-threaded scenarios.

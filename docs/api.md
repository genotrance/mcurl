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
| `bridge(client_rfile, client_wfile, client_hfile)` | Bridge curl reads/writes to file-like objects |
| `set_transfer_decoding(enable=False)` | Control transfer decoding (chunked, gzip); disable to let the client handle it |
| `get_data(encoding="utf-8")` | Return response body from `buffer()` |
| `get_headers(encoding="utf-8")` | Return response headers from `buffer()` |

### Execution and response

| Method | Description |
|--------|-------------|
| `perform()` | Execute the request using the easy interface (standalone, without multi) |
| `get_response()` | Return `(CURLcode, response_code)` of completed request; uses CONNECTCODE for CONNECT method |
| `get_response_code()` | Return the HTTP response code (convenience, no CURLcode) |
| `get_effective_url()` | Return the final URL after redirects |
| `get_content_type()` | Return the Content-Type header value, or None |
| `get_total_time()` | Return total transfer time in seconds (float) |
| `get_activesocket()` | Return `(CURLcode, socket_fd)` for this handle's active connection |
| `get_primary_ip()` | Return `(CURLcode, ip_string)` of the remote server |
| `get_used_proxy()` | Return `(CURLcode, bool)` indicating whether a proxy was used |
| `get_proxyauth_used()` | Return `(CURLcode, auth_bitmask)` for the proxy auth method that was negotiated |

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
| `set_seek(callback=None)` | Set seek callback `fn(offset, origin) -> int`; returns `CURL_SEEKFUNC_OK`(0)/`FAIL`(1)/`CANTSEEK`(2) |

### Handle duplication and lifecycle

| Method | Description |
|--------|-------------|
| `reset(url, method, request_version, connect_timeout)` | Reset and reuse this handle for another request (clears all libcurl options, re-applies mcurl defaults) |
| `dup(url=None)` | Clone this handle into a new `Curl` instance with all libcurl options preserved - useful for template-based workflows |
| `pause(bitmask)` | Pause send/receive on the handle |
| `unpause()` | Resume all paused transfers |

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

### Static methods

| Method | Description |
|--------|-------------|
| `MCurl.strerror(code)` | Return human-readable string for a CURLMcode error value |

### Methods

| Method | Description |
|--------|-------------|
| `setopt(option, value)` | Configure a multi option (socket/timer callbacks are reserved by mcurl) |
| `set_failure_threshold(threshold)` | Set number of auth failures before blocking a proxy (default: 3) |
| `add(curl)` | Add a `Curl` handle to the multi instance for concurrent execution |
| `do(curl)` | Add a `Curl` handle and perform until completion; returns `True` on success |
| `remove(curl)` | Remove a completed `Curl` handle from the multi instance |
| `stop(curl)` | Abort a running `Curl` handle and remove it from the multi instance |
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

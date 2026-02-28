# API Reference

---

## Module-level functions

### `curl_version()`
Get curl version as numeric representation (e.g. `0x081200` for 8.18.0).

### `get_curl_vinfo()`
Get the raw `curl_version_info_data` struct from libcurl.

### `get_curl_features()`
Get all supported feature names from version info data. Returns a list of
strings (e.g. `["SSL", "SPNEGO", "Kerberos", ...]`).

### `print_curl_version()`
Display curl version information via `dprint()`.

### `getauth(auth)`
Return auth value for specified authentication string.

Supported values: see https://curl.se/libcurl/c/CURLOPT_HTTPAUTH.html

Skip the `CURLAUTH_` portion in input — e.g. `getauth("ANY")`.

Prefixes for proxy detection control:
- `NO` — avoid method: `NONTLM` → `ANY & ~NTLM`
- `SAFENO` — avoid method from safe set: `SAFENONTLM` → `ANYSAFE & ~NTLM`
- `ONLY` — support only that method: `ONLYNTLM` → `ONLY | NTLM`

### Utility functions

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

---

## `Curl` class

Helper class to manage a curl easy instance.

### Constructor

```python
Curl(url, method="GET", request_version="HTTP/1.1", connect_timeout=60)
```

- **method** — `GET`, `POST`, `PUT`, `HEAD`, `CONNECT`, `PATCH`, `DELETE`, etc.
- **request_version** — `HTTP/1.0`, `HTTP/1.1`, etc.
- **connect_timeout** — connection timeout in seconds.

### Methods

| Method | Description |
|--------|-------------|
| `reset(url, method, request_version, connect_timeout)` | Reuse existing curl instance for another request |
| `set_tunnel(tunnel=True)` | Set to tunnel through proxy |
| `set_proxy(proxy, port=0, noproxy=None)` | Set proxy options; returns `False` if proxy has auth failures above threshold |
| `set_auth(user, password=None, auth="ANY")` | Set proxy authentication — call after `set_proxy()` to enable auth caching |
| `set_headers(xheaders)` | Set headers to send (dict) |
| `set_insecure(enable=True)` | Ignore SSL certificate errors |
| `set_verbose(enable=True)` | Set verbose mode |
| `set_debug(enable=True)` | Enable debug output (verbose + debug callback) |
| `set_useragent(useragent)` | Set user agent string |
| `set_follow(enable=True)` | Follow 3xx redirects |
| `set_transfer_decoding(enable=False)` | Turn off transfer decoding (let client handle it) |
| `bridge(client_rfile, client_wfile, client_hfile)` | Bridge curl reads/writes to file-like objects |
| `buffer(data=None)` | Setup BytesIO buffers for `bridge()` |
| `perform()` | Perform the easy handle (standalone, not using multi) |
| `get_response()` | Return `(ret, response_code)` of completed request |
| `get_data(encoding="utf-8")` | Return response body from `buffer()` |
| `get_headers(encoding="utf-8")` | Return response headers from `buffer()` |
| `get_activesocket()` | Return `(ret, socket_fd)` for this easy instance |
| `get_primary_ip()` | Return `(ret, ip_string)` for this easy instance |
| `get_used_proxy()` | Return `(ret, bool)` whether proxy was used |
| `get_proxyauth_used()` | Return `(ret, auth_method)` which proxy auth was used |

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

### Methods

| Method | Description |
|--------|-------------|
| `add(curl)` | Add a `Curl` handle to the multi instance |
| `remove(curl)` | Remove a `Curl` handle once done |
| `stop(curl)` | Stop a running curl handle and remove it |
| `do(curl)` | Add a `Curl` handle and perform until completion; returns `True` on success |
| `select(curl, client_sock, idle=30)` | Run select loop between client socket and curl (for CONNECT tunnels) |
| `setopt(option, value)` | Configure multi options (socket/timer callbacks are reserved) |
| `set_failure_threshold(threshold)` | Set number of auth failures before blocking a proxy (default: 3) |
| `close()` | Stop all running transfers and close the multi handle |

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `handles` | dict | Active `{easyhash: Curl}` handles |
| `proxyauth` | dict | Cached `{proxy: auth_method}` values |
| `failed` | dict | `{proxy: failure_count}` for auth failures |
| `failure_threshold` | int | Max auth failures before blocking (default: 3) |

---

## Raw libcurl access

The libcurl C API can be accessed directly:

```python
from _libcurl_cffi import lib as libcurl
from _libcurl_cffi import ffi

url = "http://httpbin.org/get"
curl = ffi.new("char []", url.encode("utf-8"))

easy = libcurl.curl_easy_init()
libcurl.curl_easy_setopt(easy, libcurl.CURLOPT_URL, curl)
cerr = libcurl.curl_easy_perform(easy)
```

---

## Debug output

Set `mcurl.dprint` to a callable to receive debug messages:

```python
import mcurl
mcurl.dprint = print
```

All debug messages include the easy-handle hash for correlation in
multi-threaded scenarios.

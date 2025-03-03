Manage outbound HTTP connections using Curl & CurlMulti

### Description

mcurl is a Python wrapper for [libcurl](https://curl.haxx.se/libcurl/) with a
high-level API that makes it easy to interact with the libcurl easy and multi
interfaces. It was originally created for the [Px](https://github.com/genotrance/px)
proxy server which uses libcurl to handle upstream proxy authentication.

### Usage

mcurl can be installed using pip:

```bash
pip install pymcurl
```

Binary [packages](https://pypi.org/project/pymcurl) are provided the following platforms:
- aarch64-linux-gnu
- aarch64-linux-musl
- arm64-mac
- i686-linux-gnu
- x86_64-linux-gnu
- x86_64-linux-musl
- x86_64-macos
- x86_64-windows

mcurl leverages [cffi](https://pypi.org/project/cffi) to interface with libcurl
and all binary dependencies are sourced from [binarybuilder.org](https://binarybuilder.org/).
auditwheel on Linux, delocate on MacOS and delvewheel on Windows are used to bundle
the shared libraries into the wheels.

Thanks to [cffi](https://cffi.readthedocs.io/en/latest/cdef.html#ffibuilder-compile-etc-compiling-out-of-line-modules)
and [Py_LIMITED_API](https://docs.python.org/3/c-api/stable.html#limited-c-api),
these mcurl binaries should work on any Python from v3.2 onwards.

#### Easy interface

```python
import mcurl

mcurl.dprint = print

c = mcurl.Curl('http://httpbin.org/get')
c.set_debug()
c.buffer()
ret = c.perform()
if ret == 0:
    ret, resp = c.get_response()
    headers = c.get_headers()
    data = c.get_data()
    print(f"Response: {resp}\n\n{headers}{data}")
```

#### Multi interface

```python
import mcurl

mcurl.dprint = print

m = mcurl.MCurl()

c1 = mcurl.Curl('http://httpbin.org/get')
c1.set_debug()
c1.buffer()
m.add(c1)

data = "test8192".encode("utf-8")
c2 = mcurl.Curl('https://httpbin.org/post', 'POST')
c2.set_debug()
c2.buffer(data=data)
c2.set_headers({"Content-Length": len(data)})
m.add(c2)

ret1 = m.do(c1)
ret2 = m.do(c2)

if ret1:
    c1.get_response()
    c1.get_headers()
    c1.get_data()
    print(f"Response: {c1.get_response()}\n\n" +
          f"{c1.get_headers()}{c1.get_data()}")
else:
    print(f"Failed with error: {c1.errstr}")

if ret2:
    c2.get_response()
    c2.get_headers()
    c2.get_data()
    print(f"Response: {c2.get_response()}\n\n" +
          f"{c2.get_headers()}{c2.get_data()}")
else:
    print(f"Failed with error: {c2.errstr}")

m.close()
```

#### libcurl API

The [libcurl API](https://curl.se/libcurl/c/) can be directly accessed as is done
in [mcurl](mcurl/__init__.py) if preferred.

```python
from _libcurl_cffi import lib as libcurl
from _libcurl_cffi import ffi

url = "http://httpbin.org/get"
curl = ffi.new("char []", url.encode("utf-8"))

easy = libcurl.curl_easy_init()
libcurl.curl_easy_setopt(easy, libcurl.CURLOPT_URL, curl)
cerr = libcurl.curl_easy_perform(easy)
```

#### API reference

```
NAME
    mcurl - Manage outbound HTTP connections using Curl & CurlMulti

CLASSES
    builtins.object
        Curl
        MCurl

    class Curl(builtins.object)
     |  Curl(url, method='GET', request_version='HTTP/1.1', connect_timeout=60)
     |
     |  Helper class to manage a curl easy instance
     |
     |  Methods defined here:
     |
     |  __del__(self)
     |      Destructor - clean up resources
     |
     |  __init__(self, url, method='GET', request_version='HTTP/1.1', connect_timeout=60)
     |      Initialize curl instance
     |
     |      method = GET, POST, PUT, CONNECT, etc.
     |      request_version = HTTP/1.0, HTTP/1.1, etc.
     |
     |  bridge(self, client_rfile=None, client_wfile=None, client_hfile=None)
     |      Bridge curl reads/writes to sockets specified
     |
     |      Reads POST/PATCH data from client_rfile
     |      Writes data back to client_wfile
     |      Writes headers back to client_hfile
     |
     |  buffer(self, data=None)
     |      Setup buffers to bridge curl perform
     |
     |  get_activesocket(self)
     |      Return active socket for this easy instance
     |
     |  get_data(self, encoding='utf-8')
     |      Return data written by curl perform to buffer()
     |
     |      encoding = "utf-8" by default, change or set to None if bytes preferred
     |
     |  get_headers(self, encoding='utf-8')
     |      Return headers written by curl perform to buffer()
     |
     |      encoding = "utf-8" by default, change or set to None if bytes preferred
     |
     |  get_primary_ip(self)
     |      Return primary IP address of this easy instance
     |
     |  get_proxyauth_used(self)
     |      Return which proxy auth method was used for this easy instance
     |
     |  get_response(self)
     |      Return response code of completed request
     |
     |  get_used_proxy(self)
     |      Return whether proxy was used for this easy instance
     |
     |  perform(self)
     |      Perform the easy handle
     |
     |  reset(self, url, method='GET', request_version='HTTP/1.1', connect_timeout=60)
     |      Reuse existing curl instance for another request
     |
     |  set_auth(self, user, password=None, auth='ANY')
     |      Set proxy authentication info - call after set_proxy() to enable auth caching
     |
     |  set_debug(self, enable=True)
     |      Enable debug output
     |
     |  set_follow(self, enable=True)
     |      Set curl to follow 3xx responses
     |
     |  set_headers(self, xheaders)
     |      Set headers to send
     |
     |  set_insecure(self, enable=True)
     |      Set curl to ignore SSL errors
     |
     |  set_proxy(self, proxy, port=0, noproxy=None)
     |      Set proxy options - returns False if this proxy server has auth failures
     |
     |  set_transfer_decoding(self, enable=False)
     |      Set curl to turn off transfer decoding - let client do it
     |
     |  set_tunnel(self, tunnel=True)
     |      Set to tunnel through proxy if no proxy or proxy + auth
     |
     |  set_useragent(self, useragent)
     |      Set user agent to send
     |
     |  set_verbose(self, enable=True)
     |      Set verbose mode
     |

    class MCurl(builtins.object)
     |  MCurl(debug_print=None)
     |
     |  Helper class to manage a curl multi instance
     |
     |  Methods defined here:
     |
     |  __init__(self, debug_print=None)
     |      Initialize multi interface
     |
     |  add(self, curl: mcurl.Curl)
     |      Add a Curl handle to perform
     |
     |  close(self)
     |      Stop any running transfers and close this multi handle
     |
     |  do(self, curl: mcurl.Curl)
     |      Add a Curl handle and peform until completion
     |
     |  remove(self, curl: mcurl.Curl)
     |      Remove a Curl handle once done
     |
     |  select(self, curl: mcurl.Curl, client_sock, idle=30)
     |      Run select loop between client and curl
     |
     |  setopt(self, option, value)
     |      Configure multi options
     |
     |  stop(self, curl: mcurl.Curl)
     |      Stop a running curl handle and remove

FUNCTIONS
    curl_version()
        Get curl version as numeric representation

    cvp2pystr(cvoidp)
        Convert void * to Python string

    debug_callback(easy, infotype, data, size, userp)
        Prints out curl debug info and headers sent/received

    dprint(_)

    get_curl_features()
        Get all supported feature names from version info data

    get_curl_vinfo()
        Get curl version info data

    getauth(auth)
        Return auth value for specified authentication string

        Supported values can be found here: https://curl.se/libcurl/c/CURLOPT_HTTPAUTH.html

        Skip the CURLAUTH_ portion in input - e.g. getauth("ANY")

        To control which methods are available during proxy detection:
          Prefix NO to avoid method - e.g. NONTLM => ANY - NTLM
          Prefix SAFENO to avoid method - e.g. SAFENONTLM => ANYSAFE - NTLM
          Prefix ONLY to support only that method - e.g ONLYNTLM => ONLY + NTLM

    gethash(easy)
        Return hash value for easy to allow usage as a dict key

    header_callback(buffer, size, nitems, userdata)

    multi_timer_callback(multi, timeout_ms, userp)

    print_curl_version()
        Display curl version information

    py2cbool(pbool)
        Convert Python bool to long

    py2clong(plong)
        Convert Python int to long

    py2cstr(pstr)
        Convert Python string to char *

    py2custr(pstr)
        Convert Python string to char *

    read_callback(buffer, size, nitems, userdata)

    sanitized(msg)
        Hide user sensitive data from debug output

    socket_callback(easy, sock_fd, ev_bitmask, userp, socketp)

    sockopt_callback(clientp, sock_fd, purpose)

    write_callback(buffer, size, nitems, userdata)

    yield_msgs(data, size)
        Generator for curl debug messages

```

### Building mcurl

mcurl is built using gcc on Linux, clang on MacOS and mingw-x64 on Windows. The
shared libraries are downloaded from [binarybuilder.org](https://binarybuilder.org/)
using [jbb](https://pypi.org/projects/jbb) for Linux and Windows. Custom libcurl
binaries that include `kerberos` support on Linux and remove the `libssh2` dependency
are [included](https://github.com/genotrance/libcurl_jll.jl). MacOS includes the
libcurl binaries and dependencies installed via brew.

[cibuildwheel](https://cibuildwheel.pypa.io/) is used to build all the artifacts.

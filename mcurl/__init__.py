"""Manage outbound HTTP connections using Curl & CurlMulti"""

import collections
import io
import os.path
import platform
import select
import socket
import sys
import threading
import time

try:
    import _cffi_backend
except ImportError:
    print("Requires cffi")
    sys.exit(1)

try:
    from _libcurl_cffi import ffi
    from _libcurl_cffi import lib as libcurl
except OSError:
    print("Requires libcurl")
    sys.exit(1)

# Debug shortcut


def dprint(_):
    pass


MCURL = None

__all__ = [
    "Curl",
    "MCurl",
    "curl_version",
    "cvp2pystr",
    "ffi",
    "get_curl_features",
    "get_curl_vinfo",
    "getauth",
    "gethash",
    "libcurl",
    "print_curl_version",
    "py2cbool",
    "py2clong",
    "py2cstr",
    "py2custr",
    "sanitized",
    "yield_msgs",
]

# Merging ideas from:
#   https://github.com/pycurl/pycurl/blob/master/examples/multi-socket_action-select.py
#   https://github.com/fsbs/aiocurl
#   https://github.com/yifeikong/curl_cffi


def py2cstr(pstr):
    "Convert Python string to char *"
    return ffi.new("char []", pstr.encode("utf-8"))


def py2custr(pstr):
    "Convert Python string to char *"
    return ffi.new("char []", pstr)


def py2clong(plong):
    "Convert Python int to long"
    return ffi.cast("long", plong)


def py2cbool(pbool):
    "Convert Python bool to long"
    return ffi.cast("long", 1 if pbool else 0)


def cvp2pystr(cvoidp):
    "Convert void * to Python string"
    return ffi.string(ffi.cast("char *", cvoidp)).decode("utf-8")


def sanitized(msg):
    "Hide user sensitive data from debug output"
    lower = msg.lower()
    # Hide auth responses and username
    if "authorization: " in lower or "authenticate: " in lower:
        fspace = lower.find(" ")
        if fspace != -1:
            sspace = lower.find(" ", fspace + 1)
            if sspace != -1:
                return msg[0:sspace] + " sanitized len(%d)" % len(msg[sspace:])
    elif lower.startswith("proxy auth using"):
        fspace = lower.find(" ", len("proxy auth using "))
        if fspace != -1:
            return msg[0:fspace] + " sanitized len(%d)" % len(msg[fspace:])

    return msg


def gethash(easy):
    "Return hash value for easy to allow usage as a dict key"
    return str(int(ffi.cast("uintptr_t", easy)))


def getauth(auth):
    """
    Return auth value for specified authentication string

    Supported values can be found here: https://curl.se/libcurl/c/CURLOPT_HTTPAUTH.html

    Skip the CURLAUTH_ portion in input - e.g. getauth("ANY")

    To control which methods are available during proxy detection:
      Prefix NO to avoid method - e.g. NONTLM => ANY - NTLM
      Prefix SAFENO to avoid method - e.g. SAFENONTLM => ANYSAFE - NTLM
      Prefix ONLY to support only that method - e.g ONLYNTLM => ONLY + NTLM
    """
    authval = libcurl.CURLAUTH_NONE
    if auth == "NONE":
        return authval

    if auth.startswith("NO"):
        auth = auth[len("NO") :]
        authval = libcurl.CURLAUTH_ANY & ~(getattr(libcurl, "CURLAUTH_" + auth))
    elif auth.startswith("SAFENO"):
        auth = auth[len("SAFENO") :]
        authval = libcurl.CURLAUTH_ANYSAFE & ~(getattr(libcurl, "CURLAUTH_" + auth))
    elif auth.startswith("ONLY"):
        auth = auth[len("ONLY") :]
        authval = libcurl.CURLAUTH_ONLY | getattr(libcurl, "CURLAUTH_" + auth)
    else:
        authval = getattr(libcurl, "CURLAUTH_" + auth)

    return authval


# Active thread running callbacks can print debug output for any other
# thread's easy - cannot assume it is for this thread. All dprint()s
# include easyhash to correlate instead


def yield_msgs(data, size):
    "Generator for curl debug messages"
    raw = ffi.string(data)[:size]
    for line in raw.split(b"\r\n"):
        if line and not line.isspace():
            yield line.decode("utf-8")


@ffi.def_extern()
def debug_callback(easy, infotype, data, size, userp):
    "Prints out curl debug info and headers sent/received"

    del userp
    easyhash = gethash(easy)
    curl = MCURL.handles[easyhash]
    if infotype == libcurl.CURLINFO_TEXT:
        prefix = easyhash + ": Curl info: "
    elif infotype == libcurl.CURLINFO_HEADER_IN:
        prefix = easyhash + ": Received header <= "
    elif infotype == libcurl.CURLINFO_HEADER_OUT:
        prefix = easyhash + ": Sent header => "
    else:
        return libcurl.CURLE_OK

    for msg in yield_msgs(data, size):
        dprint(prefix + sanitized(msg))

    return libcurl.CURLE_OK


@ffi.def_extern()
def read_callback(buffer, size, nitems, userdata):
    tsize = size * nitems
    curl = MCURL.handles[cvp2pystr(userdata)]
    if curl.size is not None:
        if curl.size > tsize:
            curl.size -= tsize
        else:
            tsize = curl.size
            curl.size = None
        if curl.client_rfile is not None:
            try:
                data = curl.client_rfile.read(tsize)
                ffi.memmove(buffer, data, tsize)
            except ConnectionError as exc:
                dprint(curl.easyhash + ": Error reading from client: " + str(exc))
                tsize = 0
        else:
            dprint(curl.easyhash + ": Read expected but no client")
            tsize = 0
    else:
        tsize = 0

    dprint(curl.easyhash + ": Read %d bytes" % tsize)
    return tsize


@ffi.def_extern()
def write_callback(buffer, size, nitems, userdata):
    tsize = size * nitems
    curl = MCURL.handles[cvp2pystr(userdata)]
    if tsize > 0:
        if curl.sentheaders:
            if curl.client_wfile is not None:
                try:
                    tsize = curl.client_wfile.write(ffi.buffer(buffer, tsize))
                except ConnectionError as exc:
                    dprint(curl.easyhash + ": Error writing to client: " + str(exc))
                    return 0
            else:
                dprint(curl.easyhash + ": Ignored %d bytes" % tsize)
                return tsize
        else:
            dprint(curl.easyhash + ": Skipped %d bytes" % tsize)
            return tsize

    # dprint(curl.easyhash + ": Wrote %d bytes" % tsize)
    return tsize


@ffi.def_extern()
def header_callback(buffer, size, nitems, userdata):
    tsize = size * nitems
    curl = MCURL.handles[cvp2pystr(userdata)]
    if tsize > 0:
        data = bytes(ffi.string(buffer)[:tsize])
        if curl.suppress:
            if data == b"\r\n":
                # Stop suppressing headers since done
                dprint(curl.easyhash + ": Resuming headers")
                curl.suppress = False
            return tsize
        else:
            if data == b"\r\n":
                # Done sending headers
                dprint(curl.easyhash + ": Done sending headers")
                curl.sentheaders = True
            elif curl.auth is not None and data[0] == 72 and b"407" in data:
                # Header starts with H and has 407 - HTTP/x.x 407 (issue #148)
                # Px is configured to authenticate so don't send auth related
                # headers from upstream proxy to client
                dprint(curl.easyhash + ": Suppressing headers")
                curl.suppress = True
                return tsize
        if curl.client_hfile is not None:
            try:
                return curl.client_hfile.write(data)
            except ConnectionError as exc:
                dprint(curl.easyhash + ": Error writing header to client: " + str(exc))
                return 0
        else:
            dprint(curl.easyhash + ": Ignored %d bytes" % tsize)
            return tsize

    return 0


@ffi.def_extern()
def xferinfo_callback(clientp, dltotal, dlnow, ultotal, ulnow):
    curl = MCURL.handles[cvp2pystr(clientp)]
    if curl._xferinfo_fn is not None:
        return curl._xferinfo_fn(dltotal, dlnow, ultotal, ulnow)
    return 0


@ffi.def_extern()
def seek_callback(clientp, offset, origin):
    curl = MCURL.handles[cvp2pystr(clientp)]
    if curl._seek_fn is not None:
        return curl._seek_fn(offset, origin)
    # Default: seek on client_rfile if it supports seeking
    if curl.client_rfile is not None and hasattr(curl.client_rfile, "seek"):
        try:
            curl.client_rfile.seek(offset, origin)
        except (OSError, ValueError):
            return libcurl.CURL_SEEKFUNC_FAIL
        else:
            return libcurl.CURL_SEEKFUNC_OK
    return libcurl.CURL_SEEKFUNC_CANTSEEK


class Curl:
    "Helper class to manage a curl easy instance"

    # Data
    easy = None
    easyhash = None
    ceasyhash = ffi.NULL
    sock_fd = None

    # For plain HTTP
    client_rfile = None
    client_wfile = None
    client_hfile = None

    # Request info
    auth = None
    headers = ffi.NULL
    method = None
    proxy = None
    request_version = None
    size = None
    url = None
    user = None
    xheaders = None

    # cffi string pointers - must be kept alive for the lifetime of the handle.
    # PyPy's GC is more aggressive than CPython refcounting; a list is a
    # reliable strong root that prevents collection of cffi char[] objects.
    # Initialized per-instance in __init__ and reset() to avoid class-level sharing.

    # Status
    cerr = libcurl.CURLE_OK
    done = False
    errstr = ""
    resp = 503
    sentheaders = False
    suppress = False

    # Flags
    is_connect = False
    is_easy = False
    is_patch = False
    is_post = False
    is_tunnel = False
    is_upload = False

    @staticmethod
    def strerror(code):
        "Return human-readable string for a CURLcode error"
        return ffi.string(libcurl.curl_easy_strerror(code)).decode("utf-8")

    def __init__(self, url, method="GET", request_version="HTTP/1.1", connect_timeout=60):
        """
        Initialize curl instance

        method = GET, POST, PUT, CONNECT, etc.
        request_version = HTTP/1.0, HTTP/1.1, etc.
        """
        global MCURL
        if MCURL is None:
            MCURL = MCurl()

        self.easy = libcurl.curl_easy_init()
        self.easyhash = gethash(self.easy)
        self._keepalive = []
        self._xferinfo_fn = None
        self._seek_fn = None
        self.ceasyhash = self._keepstr(self.easyhash)
        dprint(self.easyhash + ": New curl instance")

        self._setup(url, method, request_version, connect_timeout)

    def __del__(self):
        "Destructor - clean up resources"
        if libcurl is not None and self.easy is not None:
            if self.headers is not None:
                # Free curl headers if any
                libcurl.curl_slist_free_all(self.headers)
            libcurl.curl_easy_cleanup(self.easy)

    def _keepstr(self, pstr):
        "Allocate C-heap char[] for pstr and keep cdata alive in _keepalive (PyPy moving-GC safe)"
        cobj = ffi.new("char[]", pstr.encode("utf-8"))
        self._keepalive.append(cobj)
        return cobj

    def _setup(self, url, method, request_version, connect_timeout):
        "Setup curl instance based on request info"
        dprint(self.easyhash + ": %s %s using %s" % (method, url, request_version))

        # Ignore proxy environment variables
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_PROXY, self._keepstr(""))
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_NOPROXY, self._keepstr(""))

        # Timeouts
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_CONNECTTIMEOUT, py2clong(connect_timeout))
        # libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_TIMEOUT, py2clong(60))

        # Disable signal-based timeouts - required for thread safety
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_NOSIGNAL, py2cbool(True))

        # SSL CAINFO
        if sys.platform != "win32":
            # libcurl uses schannel on Windows which uses system CA certs
            cainfo = os.path.join(os.path.dirname(__file__), "cacert.pem")
            if os.path.exists(cainfo):
                dprint(self.easyhash + ": Using CAINFO from " + cainfo)
                libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_CAINFO, self._keepstr(cainfo))

        # Set HTTP method
        self.method = method
        if method == "CONNECT":
            self.is_connect = True
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_CONNECT_ONLY, py2cbool(True))

            # No proxy yet so setup tunnel for direct CONNECT
            self.set_tunnel()

            # We want libcurl to make a simple HTTP connection to auth
            # with the upstream proxy and let client establish SSL
            if "://" not in url:
                url = "http://" + url
        elif method == "GET":
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_HTTPGET, py2cbool(True))
        elif method == "HEAD":
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_NOBODY, py2cbool(True))
        elif method == "POST":
            self.is_post = True
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_POST, py2cbool(True))
        elif method == "PUT":
            self.is_upload = True
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_UPLOAD, py2cbool(True))
        elif method in ["PATCH", "DELETE"]:
            if method == "PATCH":
                self.is_patch = True
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_CUSTOMREQUEST, self._keepstr(method))
        else:
            dprint(self.easyhash + ": Unknown method: " + method)
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_CUSTOMREQUEST, self._keepstr(method))

        self.url = url
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_URL, self._keepstr(url))

        # Set HTTP version to use
        self.request_version = request_version
        version = request_version.split("/")[1].replace(".", "_")
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_HTTP_VERSION, py2clong(getattr(libcurl, "CURL_HTTP_VERSION_" + version))
        )

    def reset(self, url, method="GET", request_version="HTTP/1.1", connect_timeout=60):
        """Reuse existing curl instance for another request.

        Calls curl_easy_reset() (which clears all libcurl options) and then
        re-applies the standard mcurl setup via _setup(). Options set via
        setopt() or other libcurl-level calls are NOT preserved - only
        mcurl-managed state (URL, method, connect timeout, CA bundle, etc.)
        is re-established.
        """
        dprint(self.easyhash + ": Resetting curl")
        libcurl.curl_easy_reset(self.easy)
        self.sock_fd = None

        self.client_rfile = None
        self.client_wfile = None
        self.client_hfile = None

        self.auth = None
        self.proxy = None
        self.size = None
        self.user = None
        self.xheaders = None
        self._keepalive = []
        self._xferinfo_fn = None
        self._seek_fn = None

        self.cerr = libcurl.CURLE_OK
        self.done = False
        self.errstr = ""
        self.resp = 503
        self.sentheaders = False
        self.suppress = False

        self.is_connect = False
        self.is_easy = False
        self.is_patch = False
        self.is_post = False
        self.is_tunnel = False
        self.is_upload = False

        if self.headers is not None:
            # Free curl headers if any
            libcurl.curl_slist_free_all(self.headers)
            self.headers = None

        self._setup(url, method, request_version, connect_timeout)

    def set_tunnel(self, tunnel=True):
        "Enable or disable HTTP proxy tunneling (CONNECT method through proxy)"
        dprint(self.easyhash + ": HTTP proxy tunneling = " + str(tunnel))
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_HTTPPROXYTUNNEL, py2cbool(tunnel))
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_SUPPRESS_CONNECT_HEADERS, py2cbool(tunnel))
        self.is_tunnel = tunnel

    def set_proxy(self, proxy, port=0, noproxy=None):
        "Set proxy server; returns False if this proxy has exceeded the auth failure threshold"
        if proxy in MCURL.failed and MCURL.failed[proxy] >= MCURL.failure_threshold:
            dprint(
                self.easyhash
                + f": Authentication issues with this proxy server (failed {MCURL.failure_threshold} times)"
            )
            return False

        self.proxy = proxy
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_PROXY, self._keepstr(proxy))
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_PROXYPORT, py2clong(port))
        if noproxy is not None:
            dprint(self.easyhash + ": Set noproxy to " + noproxy)
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_NOPROXY, self._keepstr(noproxy))

        if self.is_connect:
            # Proxy but no auth (yet) so just connect and let client tunnel and authenticate
            self.set_tunnel(tunnel=False)

        return True

    def set_auth(self, user, password=None, auth="ANY"):
        "Set proxy authentication credentials; call after set_proxy() to enable auth caching"
        if user == ":":
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_PROXYUSERPWD, self._keepstr(user))
        else:
            self.user = user
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_PROXYUSERNAME, self._keepstr(user))
            if password is not None:
                libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_PROXYPASSWORD, self._keepstr(password))
            else:
                dprint(self.easyhash + ": Blank password for user")
        if auth is not None:
            if self.proxy in MCURL.proxyauth:
                # Use cached value
                self.auth = MCURL.proxyauth[self.proxy]
                dprint(self.easyhash + f": Using cached proxy auth method: {self.auth}")
            else:
                # Use specified value
                self.auth = getauth(auth)
                dprint(self.easyhash + f": Setting proxy auth method: {self.auth}")

            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_PROXYAUTH, py2clong(self.auth))

            if self.is_connect:
                # Proxy + auth so tunnel and authenticate
                self.set_tunnel()

    def set_headers(self, xheaders):
        "Set request headers from a dict of {name: value} pairs"
        self.headers = ffi.NULL
        skip_proxy_headers = True if self.proxy is not None and self.auth is not None else False
        for header in xheaders:
            lcheader = header.lower()
            if skip_proxy_headers and lcheader.startswith("proxy-"):
                # Don't forward proxy headers from client if no upstream proxy
                # or no auth specified (client will authenticate directly)
                dprint(self.easyhash + ": Skipping header =!> %s: %s" % (header, xheaders[header]))
                continue
            elif lcheader == "content-length":
                size = int(xheaders[header])
                if self.is_upload or self.is_post:
                    # Save content-length for PUT/POST later
                    # Turn off Transfer-Encoding since size is known
                    self.size = size
                    self.headers = libcurl.curl_slist_append(self.headers, py2cstr("Transfer-Encoding:"))
                    self.headers = libcurl.curl_slist_append(self.headers, py2cstr("Expect:"))
                    if self.is_post:
                        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_POSTFIELDSIZE, py2clong(size))
                    else:
                        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_INFILESIZE, py2clong(size))
                elif self.is_patch:
                    # Get data from client - libcurl doesn't seem to use READFUNCTION
                    try:
                        data = self.client_rfile.read(size)
                    except AttributeError as exc:
                        dprint("set_headers() called before buffer()/bridge()?")
                        raise exc
                    libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_COPYPOSTFIELDS, py2custr(data))
            elif lcheader == "user-agent":
                # Forward user agent via setopt
                self.set_useragent(xheaders[header])
                continue
            dprint(self.easyhash + ": Adding header => " + sanitized("%s: %s" % (header, xheaders[header])))
            self.headers = libcurl.curl_slist_append(self.headers, py2cstr("%s: %s" % (header, xheaders[header])))

        if len(xheaders) != 0:
            if self.is_connect and not self.is_tunnel:
                # Send client headers later in select() - just connect to proxy
                # and let client tunnel and authenticate directly
                dprint(self.easyhash + ": Delaying headers")
                self.xheaders = xheaders
            else:
                dprint(self.easyhash + ": Setting headers")
                libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_HTTPHEADER, self.headers)

    def set_insecure(self, enable=True):
        "Disable SSL certificate and hostname verification"
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_SSL_VERIFYPEER, py2cbool(not enable))
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_SSL_VERIFYHOST, py2cbool(not enable))

    def set_verbose(self, enable=True):
        "Enable libcurl verbose output to stderr"
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_VERBOSE, py2cbool(enable))

    def set_debug(self, enable=True):
        "Enable verbose mode with debug callback routed through dprint()"
        if enable:
            self.set_verbose()
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_DEBUGFUNCTION, libcurl.debug_callback)

    def bridge(self, client_rfile=None, client_wfile=None, client_hfile=None):
        """
        Bridge curl reads/writes to sockets specified

        Reads POST/PATCH data from client_rfile
        Writes data back to client_wfile
        Writes headers back to client_hfile
        """
        dprint(self.easyhash + ": Setting up bridge")

        # Setup read/write callbacks
        if client_rfile is not None:
            self.client_rfile = client_rfile
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_READFUNCTION, libcurl.read_callback)
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_READDATA, self.ceasyhash)

        if client_wfile is not None:
            self.client_wfile = client_wfile
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_WRITEFUNCTION, libcurl.write_callback)
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_WRITEDATA, self.ceasyhash)

        if client_hfile is not None:
            self.client_hfile = client_hfile
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_HEADERFUNCTION, libcurl.header_callback)
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_HEADERDATA, self.ceasyhash)
        else:
            self.sentheaders = True

    def buffer(self, data=None):
        "Setup BytesIO buffers for perform(); pass data for upload (POST/PUT) bodies"
        dprint(self.easyhash + ": Setting up buffers for bridge")
        rfile = None
        if data is not None:
            rfile = io.BytesIO()
            rfile.write(data)
            rfile.seek(0)

        wfile = io.BytesIO()
        hfile = io.BytesIO()

        self.bridge(rfile, wfile, hfile)

        # Auto-register seek callback when data is provided (BytesIO supports seek)
        if data is not None:
            self.set_seek()

    def set_transfer_decoding(self, enable=False):
        "Control transfer decoding (chunked, gzip); disable to let the client handle it"
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_HTTP_TRANSFER_DECODING, py2cbool(enable))

    def set_useragent(self, useragent):
        "Set the User-Agent header string"
        if len(useragent) != 0:
            dprint(self.easyhash + ": Setting user agent to " + useragent)
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_USERAGENT, py2cstr(useragent))

    def set_follow(self, enable=True):
        "Enable or disable following 3xx redirect responses"
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_FOLLOWLOCATION, py2cbool(enable))

    # Generic setopt/getinfo (section 3)

    def setopt(self, option, value):
        """Set any CURLOPT option on this easy handle.

        Automatically converts Python values to the correct cffi type based on
        the CURLOPT type range:
        - LONG options (int/bool): Python int/bool -> C long
        - STRING options (str): Python str -> C char* (kept alive)
        - OFF_T options (large int): Python int -> curl_off_t
        - FUNCTIONPOINT/BLOB/other: passed through as-is

        For OBJECTPOINT options that are not strings (e.g. slist pointers),
        pass the cffi object directly.
        """
        if option >= libcurl.CURLOPTTYPE_BLOB:
            libcurl.curl_easy_setopt(self.easy, option, value)
        elif option >= libcurl.CURLOPTTYPE_OFF_T:
            if isinstance(value, int):
                value = ffi.cast("curl_off_t", value)
            libcurl.curl_easy_setopt(self.easy, option, value)
        elif option >= libcurl.CURLOPTTYPE_FUNCTIONPOINT:
            libcurl.curl_easy_setopt(self.easy, option, value)
        elif option >= libcurl.CURLOPTTYPE_OBJECTPOINT:
            if isinstance(value, str):
                value = self._keepstr(value)
            elif isinstance(value, bytes):
                value = ffi.new("char[]", value)
                self._keepalive.append(value)
            libcurl.curl_easy_setopt(self.easy, option, value)
        else:
            # LONG range (includes bools)
            if isinstance(value, (int, bool)):
                value = ffi.cast("long", int(value))
            libcurl.curl_easy_setopt(self.easy, option, value)

    def getinfo(self, info):
        """Get any CURLINFO value from this easy handle.

        Returns (CURLcode, value). Infers result type from the CURLINFO type mask.
        """
        info_type = info & 0xF00000
        if info_type == libcurl.CURLINFO_STRING:
            p = ffi.new("char **")
            ret = libcurl.curl_easy_getinfo(self.easy, info, p)
            val = ffi.string(p[0]).decode("utf-8") if p[0] != ffi.NULL else ""
        elif info_type == libcurl.CURLINFO_LONG:
            p = ffi.new("long *")
            ret = libcurl.curl_easy_getinfo(self.easy, info, p)
            val = p[0]
        elif info_type == libcurl.CURLINFO_DOUBLE:
            p = ffi.new("double *")
            ret = libcurl.curl_easy_getinfo(self.easy, info, p)
            val = p[0]
        elif info_type == libcurl.CURLINFO_SLIST:
            p = ffi.new("struct curl_slist **")
            ret = libcurl.curl_easy_getinfo(self.easy, info, p)
            val = []
            node = p[0]
            while node != ffi.NULL:
                val.append(ffi.string(node.data).decode("utf-8"))
                node = node.next
            libcurl.curl_slist_free_all(p[0])
        elif info_type == libcurl.CURLINFO_OFF_T:
            p = ffi.new("curl_off_t *")
            ret = libcurl.curl_easy_getinfo(self.easy, info, p)
            val = p[0]
        elif info_type == libcurl.CURLINFO_SOCKET:
            if sys.platform == "win32":
                p = ffi.new("unsigned int *")
            else:
                p = ffi.new("int *")
            ret = libcurl.curl_easy_getinfo(self.easy, info, p)
            val = p[0]
        else:
            raise ValueError(f"Unknown CURLINFO type: {info_type:#x}")
        return ret, val

    def unsetopt(self, option):
        """Unset a CURLOPT option, restoring it to libcurl's default.

        Best-effort: sets pointer options to NULL and integer options to 0.
        This works correctly for most options but has caveats:
        - Callback options: only clears the function, not the data pointer.
          Prefer reset() for callbacks, or clear both function and data.
        - Some string options use empty string as the "off" value, not NULL
          (e.g. CURLOPT_PROXY). Use setopt() with "" for those.
        - Some options cannot be unset (e.g. CURLOPT_COOKIEFILE).

        For a complete reset of all options, use reset() instead.
        """
        if option >= libcurl.CURLOPTTYPE_OFF_T:
            libcurl.curl_easy_setopt(self.easy, option, ffi.cast("curl_off_t", 0))
        elif option >= libcurl.CURLOPTTYPE_FUNCTIONPOINT or option >= libcurl.CURLOPTTYPE_OBJECTPOINT:
            libcurl.curl_easy_setopt(self.easy, option, ffi.NULL)
        else:
            libcurl.curl_easy_setopt(self.easy, option, py2clong(0))

    # Timeout (section 5)

    def set_timeout(self, seconds):
        "Set total transfer timeout in seconds (0 = no timeout)"
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_TIMEOUT, py2clong(seconds))

    # HTTP server auth (section 6)

    def set_httpauth(self, user, password=None, auth="ANY"):
        """Set HTTP server authentication (not proxy auth - see set_auth()).

        user:     username string
        password: password string (optional)
        auth:     authentication method string passed to getauth(), e.g.
                  "ANY", "BASIC", "DIGEST", "NTLM", "NEGOTIATE", "BEARER",
                  or a combo like "BASIC|DIGEST". See getauth() for the full
                  prefix logic (NO, SAFENO, ONLY).
        """
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_USERNAME, self._keepstr(user))
        if password is not None:
            libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_PASSWORD, self._keepstr(password))
        authval = getauth(auth)
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_HTTPAUTH, py2clong(authval))

    def set_bearer_token(self, token):
        """Set OAuth 2.0 Bearer token for server authentication.

        Automatically sets HTTPAUTH to BEARER. The token is the raw OAuth 2.0
        access token string (without the 'Bearer ' prefix).
        """
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_XOAUTH2_BEARER, self._keepstr(token))
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_HTTPAUTH, py2clong(libcurl.CURLAUTH_BEARER))

    # Cookie methods (section 7)

    def set_cookie(self, cookie):
        """Set a cookie header string for this request.

        This sets the Cookie: header directly, bypassing the cookie engine.
        e.g. 'name=value; name2=value2'
        """
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_COOKIE, self._keepstr(cookie))

    def load_cookies(self, filename):
        """Enable the cookie engine and load cookies from a file.

        Pass empty string to enable the engine without loading a file.
        File format: Netscape cookie format or HTTP Set-Cookie header lines.
        """
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_COOKIEFILE, self._keepstr(filename))

    def save_cookies(self, filename):
        "Save all known cookies to a file when the handle is cleaned up"
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_COOKIEJAR, self._keepstr(filename))

    def add_cookie(self, cookie):
        """Add or manipulate a single cookie in the in-memory cookie store.

        Accepts one of:
        - A cookie string in Netscape format (tab-delimited fields)
        - A Set-Cookie: header line
        - A control command: ALL (erase all), SESS (erase session cookies),
          FLUSH (write jar to disk), RELOAD (re-read cookie file)

        To add multiple cookies, call this method once per cookie.
        """
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_COOKIELIST, self._keepstr(cookie))

    def clear_cookies(self):
        "Erase all cookies from the in-memory cookie store"
        self.add_cookie("ALL")

    def remove_cookie(self, domain, path, name):
        """Remove a specific cookie from the in-memory cookie store.

        Works by replacing the cookie with an expired version (expiry=1).
        Requires the cookie engine to be enabled (call load_cookies("") first).
        """
        subdomain = "TRUE" if domain.startswith(".") else "FALSE"
        self.add_cookie(f"{domain}\t{subdomain}\t{path}\tFALSE\t1\t{name}\t")

    def get_cookies(self):
        "Return all known cookies as a list of Netscape-format strings"
        slist = ffi.new("struct curl_slist **")
        ret = libcurl.curl_easy_getinfo(self.easy, libcurl.CURLINFO_COOKIELIST, slist)
        cookies = []
        node = slist[0]
        while node != ffi.NULL:
            cookies.append(ffi.string(node.data).decode("utf-8"))
            node = node.next
        libcurl.curl_slist_free_all(slist[0])
        return ret, cookies

    # Redirect control (section 8)

    def set_maxredirs(self, count):
        "Set maximum number of redirects to follow (-1 = unlimited, 0 = refuse all)"
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_MAXREDIRS, py2clong(count))

    def set_postredir(self, bitmask):
        """Control whether POST method is kept on redirects.

        By default libcurl converts POST to GET on 301/302/303 redirects.
        Use this to maintain POST on specific redirect codes:
        - CURL_REDIR_POST_301 - keep POST on 301 Moved Permanently
        - CURL_REDIR_POST_302 - keep POST on 302 Found
        - CURL_REDIR_POST_303 - keep POST on 303 See Other
        - CURL_REDIR_POST_ALL - keep POST on all three

        These constants are available on mcurl.libcurl. Combine with bitwise OR.
        """
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_POSTREDIR, py2clong(bitmask))

    # Content encoding (section 9)

    def set_encoding(self, encoding=""):
        """Request compressed responses.

        Pass empty string (default) to accept all encodings supported by the
        bundled libcurl (typically gzip, deflate, br, zstd). Pass a specific
        encoding name to request only that one, or a comma-separated list.
        libcurl handles decompression transparently.
        """
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_ACCEPT_ENCODING, self._keepstr(encoding))

    # Duplicate handle (section 10)

    def dup(self, url=None):
        """Clone this handle with all libcurl options into a new Curl instance.

        Returns a new Curl object wrapping a duplicated libcurl easy handle.
        Transient state (buffers, headers slist, status flags) is reset on
        the clone. If url is provided, the clone's URL is updated.

        Useful for template-based workflows: configure a handle once (SSL,
        auth, proxy, timeouts, etc.) then dup() it for each request instead
        of repeating the setup. All options set via setopt() are preserved
        in the clone, unlike reset() which clears them.
        """
        new = Curl.__new__(Curl)
        new.easy = libcurl.curl_easy_duphandle(self.easy)
        new.easyhash = gethash(new.easy)
        new._keepalive = []
        new._xferinfo_fn = None
        new._seek_fn = None
        new.ceasyhash = new._keepstr(new.easyhash)

        # Copy Python-level state
        new.method = self.method
        new.request_version = self.request_version
        new.proxy = self.proxy
        new.auth = self.auth
        new.user = self.user
        new.is_easy = self.is_easy

        if url is not None:
            new.url = url
            libcurl.curl_easy_setopt(new.easy, libcurl.CURLOPT_URL, new._keepstr(url))
        else:
            new.url = self.url

        # Reset transient state
        new.headers = ffi.NULL
        new.xheaders = None
        new.sock_fd = None
        new.client_rfile = None
        new.client_wfile = None
        new.client_hfile = None
        new.size = None
        new.cerr = libcurl.CURLE_OK
        new.done = False
        new.errstr = ""
        new.resp = 503
        new.sentheaders = False
        new.suppress = False
        new.is_connect = False
        new.is_patch = False
        new.is_post = False
        new.is_tunnel = False
        new.is_upload = False

        return new

    # Pause / unpause (section 11)

    def pause(self, bitmask=None):
        """Pause receive and/or send on this handle.

        bitmask: combination of CURLPAUSE_RECV, CURLPAUSE_SEND.
        Default (None) pauses both (CURLPAUSE_ALL).
        """
        if bitmask is None:
            bitmask = libcurl.CURLPAUSE_ALL
        return libcurl.curl_easy_pause(self.easy, bitmask)

    def unpause(self):
        "Resume all paused transfers on this handle"
        return libcurl.curl_easy_pause(self.easy, libcurl.CURLPAUSE_CONT)

    # Transfer progress callback (section 12)

    def set_xferinfo(self, callback):
        """Set a progress callback.

        callback(dltotal, dlnow, ultotal, ulnow) -> int
        Return 0 to continue, non-zero to abort the transfer.
        All values are in bytes.
        """
        self._xferinfo_fn = callback
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_NOPROGRESS, py2cbool(False))
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_XFERINFOFUNCTION, libcurl.xferinfo_callback)
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_XFERINFODATA, self.ceasyhash)

    # Seek callback (section 13)

    def set_seek(self, callback=None):
        """Set a seek callback for request body rewinding.

        callback(offset, origin) -> int
        Return CURL_SEEKFUNC_OK (0) on success, CURL_SEEKFUNC_FAIL (1) on
        error, or CURL_SEEKFUNC_CANTSEEK (2) if seeking is not possible.

        If callback is None, uses a default that seeks on client_rfile.
        Called automatically by buffer() when upload data is provided.
        """
        self._seek_fn = callback
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_SEEKFUNCTION, libcurl.seek_callback)
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_SEEKDATA, self.ceasyhash)

    # Context manager support (section 14)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.headers is not None:
            libcurl.curl_slist_free_all(self.headers)
            self.headers = None
        libcurl.curl_easy_cleanup(self.easy)
        self.easy = None

    # Getinfo convenience methods (section 16)

    def get_effective_url(self):
        "Return the effective (final) URL after redirects"
        url = ffi.new("char **")
        libcurl.curl_easy_getinfo(self.easy, libcurl.CURLINFO_EFFECTIVE_URL, url)
        return ffi.string(url[0]).decode("utf-8") if url[0] != ffi.NULL else ""

    def get_response_code(self):
        "Return the HTTP response code"
        code = ffi.new("long *")
        libcurl.curl_easy_getinfo(self.easy, libcurl.CURLINFO_RESPONSE_CODE, code)
        return code[0]

    def get_content_type(self):
        "Return the Content-Type header value, or None"
        ct = ffi.new("char **")
        libcurl.curl_easy_getinfo(self.easy, libcurl.CURLINFO_CONTENT_TYPE, ct)
        return ffi.string(ct[0]).decode("utf-8") if ct[0] != ffi.NULL else None

    def get_total_time(self):
        "Return total transfer time in seconds (float)"
        t = ffi.new("double *")
        libcurl.curl_easy_getinfo(self.easy, libcurl.CURLINFO_TOTAL_TIME, t)
        return t[0]

    def perform(self):
        "Execute the request using the easy interface (standalone, without multi)"

        # Perform as a standalone easy handle, not using multi
        # However, add easyhash to MCURL.handles since it is used in curl callbacks
        with MCURL._lock:
            MCURL.handles[self.easyhash] = self
        self.cerr = libcurl.curl_easy_perform(self.easy)
        if self.cerr != libcurl.CURLE_OK:
            dprint(self.easyhash + ": Connection failed: " + Curl.strerror(self.cerr) + "; " + self.errstr)
        with MCURL._lock:
            MCURL.handles.pop(self.easyhash)
        self._save_auth()
        return self.cerr

    # Get status and info after running curl handle

    def get_response(self):
        "Return (CURLcode, response_code) of completed request; uses CONNECTCODE for CONNECT method"
        codep = ffi.new("long *")
        if self.method == "CONNECT":
            ret = libcurl.curl_easy_getinfo(self.easy, libcurl.CURLINFO_HTTP_CONNECTCODE, codep)
        else:
            ret = libcurl.curl_easy_getinfo(self.easy, libcurl.CURLINFO_RESPONSE_CODE, codep)
        return ret, codep[0]

    def get_activesocket(self):
        "Return (CURLcode, socket_fd) for this handle's active connection"
        if sys.platform == "win32":
            sock_fd = ffi.new("unsigned int *")
        else:
            sock_fd = ffi.new("int *")
        ret = libcurl.curl_easy_getinfo(self.easy, libcurl.CURLINFO_ACTIVESOCKET, sock_fd)
        return ret, sock_fd[0]

    def get_primary_ip(self):
        "Return (CURLcode, ip_string) of the remote server"
        ip = ffi.new("char *[]")
        ret = libcurl.curl_easy_getinfo(self.easy, libcurl.CURLINFO_PRIMARY_IP, ip)
        return ret, ffi.string(ip).decode("utf-8")

    def get_used_proxy(self):
        "Return (CURLcode, bool) indicating whether a proxy was used"
        used_proxy = ffi.new("long *")
        ret = libcurl.curl_easy_getinfo(self.easy, libcurl.CURLINFO_USED_PROXY, used_proxy)
        return ret, used_proxy[0] != 0

    def get_proxyauth_used(self):
        "Return (CURLcode, auth_bitmask) for the proxy auth method that was negotiated"
        proxyauth_used = ffi.new("long *")
        ret = libcurl.curl_easy_getinfo(self.easy, libcurl.CURLINFO_PROXYAUTH_USED, proxyauth_used)
        return ret, proxyauth_used[0]

    def get_data(self, encoding="utf-8"):
        """
        Return data written by curl perform to buffer()

        encoding = "utf-8" by default, change or set to None if bytes preferred
        """
        val = b""
        if isinstance(self.client_wfile, io.BytesIO):
            val = self.client_wfile.getvalue()
        if encoding is not None:
            val = val.decode(encoding)
        return val

    def get_headers(self, encoding="utf-8"):
        """
        Return headers written by curl perform to buffer()

        encoding = "utf-8" by default, change or set to None if bytes preferred
        """
        val = b""
        if isinstance(self.client_hfile, io.BytesIO):
            val = self.client_hfile.getvalue()
        if encoding is not None:
            val = val.decode(encoding)
        return val

    def _save_auth(self):
        "Find and cache proxy auth method used by libcurl"
        if self.auth is None or self.cerr != libcurl.CURLE_OK:
            # No need to check auth
            # - No auth requested - client will authenticate directly
            # - libcurl errror encountered
            return

        if self.proxy in MCURL.proxyauth:
            # Already cached
            dprint(f"{self.easyhash}: Proxy auth method already cached")
            return

        ret, proxyauth_used = self.get_proxyauth_used()
        if ret == libcurl.CURLE_OK:
            if proxyauth_used != 0:
                # Cache auth mthod used by libcurl
                MCURL.proxyauth[self.proxy] = proxyauth_used
                self.auth = proxyauth_used
                dprint(f"{self.easyhash}: Caching proxy auth method: {self.proxy} {proxyauth_used}")
            else:
                dprint(f"Proxy auth method not yet used: {self.auth}")
        else:
            dprint(f"Failed to get proxy auth method: {ret}")


@ffi.def_extern()
def socket_callback(easy, sock_fd, ev_bitmask, userp, socketp):
    # libcurl socket callback: add/remove actions for socket events
    del easy, userp, socketp
    if ev_bitmask & libcurl.CURL_POLL_IN or ev_bitmask & libcurl.CURL_POLL_INOUT:
        # dprint("Read sock_fd %d" % sock_fd)
        MCURL.rlist.add(sock_fd)

    if ev_bitmask & libcurl.CURL_POLL_OUT or ev_bitmask & libcurl.CURL_POLL_INOUT:
        # dprint("Write sock_fd %d" % sock_fd)
        MCURL.wlist.add(sock_fd)

    if ev_bitmask & libcurl.CURL_POLL_REMOVE:
        # dprint("Remove sock_fd %d" % sock_fd)
        MCURL.rlist.discard(sock_fd)
        MCURL.wlist.discard(sock_fd)

    return libcurl.CURLE_OK


@ffi.def_extern()
def multi_timer_callback(multi, timeout_ms, userp):
    # libcurl timer callback: schedule/cancel a timeout action
    # dprint("timeout = %d" % timeout_ms)
    del multi, userp
    if timeout_ms == -1:
        MCURL.timer = None
    else:
        MCURL.timer = timeout_ms / 1000.0

    return libcurl.CURLE_OK


def get_curl_vinfo():
    "Get curl version info data"
    return libcurl.curl_version_info(libcurl.CURLVERSION_LAST - 1)


def get_curl_features():
    "Get all supported feature names from version info data"
    vinfo = get_curl_vinfo()
    features = set()
    i = 0
    while vinfo.feature_names[i] != ffi.NULL:
        features.add(ffi.string(vinfo.feature_names[i]).decode("utf-8"))
        i += 1
    return features


def print_curl_version():
    "Display curl version information"
    vinfo = libcurl.curl_version_info(libcurl.CURLVERSION_LAST - 1)
    dprint(f"Host: {ffi.string(vinfo.host).decode('utf-8')} Python: v{platform.python_version()}")
    dprint(ffi.string(libcurl.curl_version()).decode("utf-8"))
    features = get_curl_features()
    relevant = ""
    for feature in ["GSS-API", "Kerberos", "NTLM", "SPNEGO", "SSL", "SSPI"]:
        if feature in features:
            relevant += feature + " "
    if len(relevant) != 0:
        dprint("Features: " + relevant)


def curl_version():
    "Get curl version as numeric representation"
    return get_curl_vinfo().version_num


class MCurl:
    "Helper class to manage a curl multi instance"

    _multi = None
    _lock = None

    handles = None
    proxyauth = None
    failed = None  # Proxy servers with auth failures
    timer = None
    rlist = None
    wlist = None

    @staticmethod
    def strerror(code):
        "Return human-readable string for a CURLMcode error"
        return ffi.string(libcurl.curl_multi_strerror(code)).decode("utf-8")

    def __init__(self, debug_print=None):
        "Initialize multi interface"
        global dprint
        if debug_print is not None:
            dprint = debug_print
        else:
            # No need to sanitize since no debug
            def no_sanitized(msg):
                return msg

            sanitized = no_sanitized

        # Save as global to enable access via callbacks
        global MCURL
        MCURL = self

        print_curl_version()
        self._multi = libcurl.curl_multi_init()

        # Set a callback for registering or unregistering socket events.
        libcurl.curl_multi_setopt(self._multi, libcurl.CURLMOPT_SOCKETFUNCTION, libcurl.socket_callback)

        # Set a callback for scheduling or cancelling timeout actions.
        libcurl.curl_multi_setopt(self._multi, libcurl.CURLMOPT_TIMERFUNCTION, libcurl.multi_timer_callback)

        # Init
        self.handles = {}
        self.proxyauth = {}
        self.failed = {}
        self.failure_threshold = 3
        self.rlist = set()
        self.wlist = set()
        self._lock = threading.Lock()

    def setopt(self, option, value):
        "Configure a multi option (socket/timer callbacks are reserved by mcurl)"
        if option in (libcurl.CURLMOPT_SOCKETFUNCTION, libcurl.CURLMOPT_TIMERFUNCTION):
            raise Exception("Callback options reserved for the event loop")
        libcurl.curl_multi_setopt(self._multi, option, value)

    def set_failure_threshold(self, threshold):
        "Set the number of authentication failures before blocking a proxy"
        if threshold < 1:
            raise ValueError("Threshold must be at least 1")
        self.failure_threshold = threshold

    # Callbacks

    def _socket_action(self, sock_fd, ev_bitmask):
        # Event loop callback: act on ready sockets or timeouts
        # dprint("mask = %d, sock_fd = %d" % (ev_bitmask, sock_fd))
        handle_count = ffi.new("int *")
        _ = libcurl.curl_multi_socket_action(self._multi, sock_fd, ev_bitmask, handle_count)

        # Check if any handles have finished.
        if handle_count != len(self.handles):
            self._update_transfers()

    def _update_transfers(self):
        # Mark finished handles as done
        while True:
            queued = ffi.new("int *")
            pmsg: ffi.new("CURLMsg *") = libcurl.curl_multi_info_read(self._multi, queued)
            if pmsg == ffi.NULL:
                break

            msg = pmsg[0]
            if msg.msg == libcurl.CURLMSG_DONE:
                # Always true since only one msg type
                easyhash = gethash(msg.easy_handle)
                curl = self.handles[easyhash]
                curl.done = True

                if msg.data.result != libcurl.CURLE_OK:
                    curl.cerr = msg.data.result

    # Adding to multi

    def _add_handle(self, curl: Curl):
        # Add a handle
        dprint(curl.easyhash + ": Add handle")
        if curl.easyhash not in self.handles:
            self.handles[curl.easyhash] = curl
            libcurl.curl_multi_add_handle(self._multi, curl.easy)
            dprint(curl.easyhash + ": Added handle")
        else:
            dprint(curl.easyhash + ": Active handle")

    def add(self, curl: Curl):
        "Add a Curl handle to the multi instance for concurrent execution"
        with self._lock:
            dprint(curl.easyhash + ": Handles = %d" % len(self.handles))
            self._add_handle(curl)

    # Removing from multi

    def _remove_handle(self, curl: Curl, errstr=""):
        # Remove a handle and set status
        if curl.easyhash not in self.handles:
            return

        if curl.done is False:
            curl.done = True

        if len(errstr) != 0:
            curl.errstr += errstr + "; "

        dprint(curl.easyhash + ": Remove handle: " + curl.errstr)
        libcurl.curl_multi_remove_handle(self._multi, curl.easy)

        self.handles.pop(curl.easyhash)

    def remove(self, curl: Curl):
        "Remove a completed Curl handle from the multi instance"
        with self._lock:
            self._remove_handle(curl)

    def stop(self, curl: Curl):
        "Abort a running Curl handle and remove it from the multi instance"
        with self._lock:
            self._remove_handle(curl, errstr="Stopped")

    # Executing multi

    def _perform(self):
        # Perform all tasks in the multi instance

        # Snapshot socket lists and timer under lock
        with self._lock:
            rsnap_set = set(self.rlist)
            wsnap_set = set(self.wlist)
            timer = self.timer

        rsnap = list(rsnap_set)
        wsnap = list(wsnap_set)
        # select()/sleep() outside the lock so other threads can add/remove
        rready, wready, xready = [], [], []
        if len(rsnap) != 0 or len(wsnap) != 0:
            try:
                rready, wready, xready = select.select(rsnap, wsnap, list(rsnap_set | wsnap_set), timer)
            except OSError:
                # Socket closed between snapshot and select()
                return
        else:
            if timer is not None:
                time.sleep(timer)

        # Process ready sockets under lock
        with self._lock:
            if len(rready) == 0 and len(wready) == 0 and len(xready) == 0:
                # dprint("No activity")
                self._socket_action(libcurl.CURL_SOCKET_TIMEOUT, 0)
            else:
                for sock_fd in rready:
                    # dprint("Ready to read sock_fd %d" % sock_fd)
                    self._socket_action(sock_fd, libcurl.CURL_CSELECT_IN)
                for sock_fd in wready:
                    # dprint("Ready to write sock_fd %d" % sock_fd)
                    self._socket_action(sock_fd, libcurl.CURL_CSELECT_OUT)
                for sock_fd in xready:
                    # dprint("Error sock_fd %d" % sock_fd)
                    self._socket_action(sock_fd, libcurl.CURL_CSELECT_ERR)

    def do(self, curl: Curl):
        "Add a Curl handle and perform until completion; returns True on success"
        if not curl.is_easy:
            self.add(curl)
            while True:
                if curl.done:
                    break
                self._perform()
                time.sleep(0.01)
            curl._save_auth()
        else:
            dprint(curl.easyhash + ": Using easy interface")
            curl.perform()

        # Map some libcurl error codes to HTTP errors
        if curl.cerr == libcurl.CURLE_URL_MALFORMAT:
            # Bad request
            curl.resp = 400
            curl.errstr += Curl.strerror(curl.cerr)
        elif curl.cerr in [libcurl.CURLE_UNSUPPORTED_PROTOCOL, libcurl.CURLE_NOT_BUILT_IN]:
            # Not implemented
            curl.resp = 501
            curl.errstr += Curl.strerror(curl.cerr)
        elif curl.cerr in [
            libcurl.CURLE_COULDNT_RESOLVE_PROXY,
            libcurl.CURLE_COULDNT_RESOLVE_HOST,
            libcurl.CURLE_COULDNT_CONNECT,
        ]:
            # Bad gateway
            curl.resp = 502
            curl.errstr += Curl.strerror(curl.cerr)
        elif curl.cerr == libcurl.CURLE_OPERATION_TIMEDOUT:
            # Gateway timeout
            curl.resp = 504
            curl.errstr += Curl.strerror(curl.cerr)
        elif curl.cerr == libcurl.CURLE_SEND_FAIL_REWIND:
            # POST/PUT rewind not supported (#199) - retry
            curl.resp = 503
            curl.errstr += Curl.strerror(curl.cerr)
        elif curl.cerr in [
            libcurl.CURLE_SSL_CONNECT_ERROR,
            libcurl.CURLE_PEER_FAILED_VERIFICATION,
            libcurl.CURLE_SSL_CERTPROBLEM,
            libcurl.CURLE_SSL_CACERT_BADFILE,
        ]:
            # SSL/TLS error
            curl.resp = 502
            curl.errstr += Curl.strerror(curl.cerr)
        elif curl.cerr in [libcurl.CURLE_SEND_ERROR, libcurl.CURLE_RECV_ERROR, libcurl.CURLE_GOT_NOTHING]:
            # Network error
            curl.resp = 502
            curl.errstr += Curl.strerror(curl.cerr)
        elif curl.cerr == libcurl.CURLE_AUTH_ERROR:
            # Auth function error (SSPI/GSS-API failure)
            curl.resp = 407
            curl.errstr += Curl.strerror(curl.cerr)
        elif curl.cerr == libcurl.CURLE_HTTP2:
            # HTTP/2 framing error
            curl.resp = 502
            curl.errstr += Curl.strerror(curl.cerr)
        elif curl.cerr != libcurl.CURLE_OK:
            # Unmapped libcurl error
            curl.resp = 503
            curl.errstr += f"Curl error {curl.cerr}"

        # Only check proxy auth when libcurl itself succeeded - a non-OK cerr
        # means the HTTP response code from get_response() is unreliable and
        # must not be used to judge authentication status (px#250)
        if curl.proxy is not None and curl.cerr == libcurl.CURLE_OK:
            ret, codep = curl.get_response()
            if ret == 0 and codep == 407:
                # Proxy authentication required
                if curl.auth is not None:
                    # Proxy auth did not work for whatever reason
                    out = "Proxy authentication failed: "
                    if curl.user is not None:
                        out += "check user/password or try different auth method"
                    else:
                        out += "single sign-on failed, user/password might be required"

                    curl.resp = 401
                    curl.errstr += out + "; "

                    # Increment failure count for this proxy; block after threshold attempts
                    self.failed[curl.proxy] = self.failed.get(curl.proxy, 0) + 1
                else:
                    # Setup client to authenticate directly with upstream proxy
                    dprint(curl.easyhash + ": Client to authenticate with upstream proxy")
                    if not curl.is_connect:
                        # curl.errstr not set else connection will get closed during auth
                        curl.resp = codep
            else:
                # Successful (or non‑auth) response – reset failure counter for this proxy
                self.failed[curl.proxy] = 0

        if curl.is_connect and curl.sock_fd is None:
            # Get the active socket for select()
            dprint(curl.easyhash + ": Getting active socket")
            ret, sock_fd = curl.get_activesocket()
            if ret == libcurl.CURLE_OK:
                curl.sock_fd = sock_fd
            else:
                out = f"Failed to get active socket: {ret}, {sock_fd}"
                dprint(curl.easyhash + ": " + out)
                curl.errstr += out + "; "
                curl.resp = 503

        return len(curl.errstr) == 0

    def select(self, curl: Curl, client_sock, idle=30):
        "Run a bidirectional select loop between a client socket and a CONNECT tunnel"
        # TODO figure out if IPv6 or IPv4
        if curl.sock_fd is None:
            dprint(curl.easyhash + ": Cannot select() without active socket")
            return

        dprint(curl.easyhash + ": Starting select loop")
        curl_sock = socket.fromfd(curl.sock_fd, socket.AF_INET, socket.SOCK_STREAM)

        ret, used_proxy = curl.get_used_proxy()
        if ret != libcurl.CURLE_OK:
            dprint(curl.easyhash + ": Failed to get used proxy: " + str(ret))
            return

        if curl.is_connect and (not curl.is_tunnel and used_proxy):
            # Send original headers from client to tunnel and authenticate with
            # upstream proxy
            dprint(curl.easyhash + ": Sending original client headers")
            curl_sock.sendall((f"{curl.method} {curl.url} {curl.request_version}\r\n").encode())
            if curl.xheaders is not None:
                for header in curl.xheaders:
                    curl_sock.sendall(f"{header}: {curl.xheaders[header]}\r\n".encode())
            curl_sock.sendall(b"\r\n")

        # sockets will be removed from these lists, when they are
        # detected as closed by remote host; wlist contains sockets
        # only when data has to be written
        rlist = [client_sock, curl_sock]
        wlist = []

        # data to be written to client connection and proxy socket
        cl = 0
        cs = 0
        cdata = collections.deque()
        sdata = collections.deque()
        max_idle = time.time() + idle
        while rlist or wlist:
            (ins, outs, exs) = select.select(rlist, wlist, rlist, idle)
            if exs:
                dprint(curl.easyhash + ": Exception, breaking")
                break
            if ins:
                for i in ins:
                    if i is curl_sock:
                        out = client_sock
                        wdata = cdata
                        source = "server"
                    else:
                        out = curl_sock
                        wdata = sdata
                        source = "client"

                    try:
                        data = i.recv(4096)
                    except ConnectionError as exc:
                        # Fix #152 - handle connection errors gracefully
                        dprint(curl.easyhash + ": from %s: " % source + str(exc))
                        data = ""
                    datalen = len(data)
                    if datalen != 0:
                        cl += datalen
                        # Prepare data to send it later in outs section
                        wdata.append(data)
                        if out not in outs:
                            outs.append(out)
                        max_idle = time.time() + idle
                    else:
                        # No data means connection closed by remote host
                        dprint(curl.easyhash + ": Connection closed by %s" % source)
                        # Because tunnel is closed on one end there is
                        # no need to read from both ends
                        del rlist[:]
                        # Do not write anymore to the closed end
                        if i in wlist:
                            wlist.remove(i)
                        if i in outs:
                            outs.remove(i)
            if outs:
                for o in outs:
                    if o is curl_sock:
                        wdata = sdata
                    else:
                        wdata = cdata
                    # Fix #223
                    if not wdata:
                        continue
                    data = wdata[0]
                    # socket.send() may sending only a part of the data
                    # (as documentation says). To ensure sending all data
                    bsnt = o.send(data)
                    if bsnt > 0:
                        if bsnt < len(data):
                            # Not all data was sent; store data not
                            # sent and ensure select() get's it when
                            # the socket can be written again
                            wdata[0] = data[bsnt:]
                            if o not in wlist:
                                wlist.append(o)
                        else:
                            wdata.popleft()
                            if not wdata and o in wlist:
                                wlist.remove(o)
                        cs += bsnt
                    else:
                        dprint(curl.easyhash + ": No data sent")
                max_idle = time.time() + idle
            if max_idle < time.time():
                # No data in timeout seconds
                dprint(curl.easyhash + ": Server connection timeout")
                break

        # After serving the proxy tunnel it could not be used for samething else.
        # A proxy doesn't really know, when a proxy tunnnel isn't needed any
        # more (there is no content length for data). So servings will be ended
        # either after timeout seconds without data transfer or when at least
        # one side closes the connection. Close both proxy and client
        # connection if still open.
        dprint(curl.easyhash + ": %d bytes read, %d bytes written" % (cl, cs))

    # Context manager support

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # Cleanup multi

    def close(self):
        "Stop any running transfers and close this multi handle"
        dprint("Closing multi")
        with self._lock:
            pending = list(self.handles.values())
            for curl in pending:
                self._remove_handle(curl, errstr="Stopped")
            libcurl.curl_multi_cleanup(self._multi)

        global MCURL
        MCURL = None

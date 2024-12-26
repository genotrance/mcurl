"""Manage outbound HTTP connections using Curl & CurlMulti"""

import io
import os.path
import select
import socket
import sys
import threading
import time

try:
    import _cffi_backend
except ImportError as exc:
    print("Requires cffi")
    sys.exit(1)

try:
    from _libcurl_cffi import lib as libcurl
    from _libcurl_cffi import ffi
except OSError as exc:
    print("Requires libcurl")
    sys.exit(1)

# Debug shortcut


def dprint(x): return None


MCURL = None

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
        auth = auth[len("NO"):]
        authval = libcurl.CURLAUTH_ANY & ~(
            getattr(libcurl, "CURLAUTH_" + auth))
    elif auth.startswith("SAFENO"):
        auth = auth[len("SAFENO"):]
        authval = libcurl.CURLAUTH_ANYSAFE & ~(
            getattr(libcurl, "CURLAUTH_" + auth))
    elif auth.startswith("ONLY"):
        auth = auth[len("ONLY"):]
        authval = libcurl.CURLAUTH_ONLY | getattr(libcurl, "CURLAUTH_" + auth)
    else:
        authval = getattr(libcurl, "CURLAUTH_" + auth)

    return authval


def save_auth(curl, msg):
    "Find and cache proxy auth mechanism from headers sent by libcurl"
    if curl.proxy in MCURL.proxytype:
        # Already cached
        dprint(f"{curl.easyhash}: Proxy auth mechanism already cached")
        return True

    if curl.auth is None:
        # No need to cache auth - client will authenticate directly
        dprint(f"{curl.easyhash}: Skipping caching proxy auth mechanism")
        return True

    dprint(f"{curl.easyhash}: Checking proxy auth mechanism: {msg}")
    if msg.startswith("Proxy-Authorization:"):
        # Cache auth mechanism from proxy headers
        proxytype = msg.split(" ")[1].upper()
        MCURL.proxytype[curl.proxy] = proxytype
        dprint(f"{curl.easyhash}: Caching proxy auth mechanism for " +
               f"{curl.proxy} as {proxytype}")

        # Cached
        return True

    # Not yet cached
    return False

# Active thread running callbacks can print debug output for any other
# thread's easy - cannot assume it is for this thread. All dprint()s
# include easyhash to correlate instead


def yield_msgs(data, size):
    "Generator for curl debug messages"
    msgs = bytes(ffi.string(data)[:size]).decode("utf-8").strip()
    if "\r\n" in msgs:
        for msg in msgs.split("\r\n"):
            if len(msg) != 0:
                yield msg
    elif len(msgs) != 0:
        yield msgs


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
        if infotype == libcurl.CURLINFO_HEADER_OUT:
            save_auth(curl, msg)

    return libcurl.CURLE_OK


@ffi.def_extern()
def wa_callback(easy, infotype, data, size, userp):
    """
    curl debug callback to get info not provided by libcurl today
    - proxy auth mechanism from sent headers
    """

    del userp
    easyhash = gethash(easy)
    curl = MCURL.handles[easyhash]
    if infotype == libcurl.CURLINFO_HEADER_OUT:
        # If sent header
        for msg in yield_msgs(data, size):
            if save_auth(curl, msg):
                # Ignore rest of headers since auth (already) cached
                break

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
                    dprint(curl.easyhash +
                           ": Error writing to client: " + str(exc))
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
                dprint(curl.easyhash +
                       ": Error writing header to client: " + str(exc))
                return 0
        else:
            dprint(curl.easyhash + ": Ignored %d bytes" % tsize)
            return tsize

    return 0


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
        self.ceasyhash = py2cstr(self.easyhash)
        dprint(self.easyhash + ": New curl instance")

        self._setup(url, method, request_version, connect_timeout)

    def __del__(self):
        "Destructor - clean up resources"
        if libcurl is not None:
            if self.headers is not None:
                # Free curl headers if any
                libcurl.curl_slist_free_all(self.headers)
            libcurl.curl_easy_cleanup(self.easy)

    def _setup(self, url, method, request_version, connect_timeout):
        "Setup curl instance based on request info"
        dprint(self.easyhash + ": %s %s using %s" %
               (method, url, request_version))

        # Ignore proxy environment variables
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_PROXY, ffi.NULL)
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_NOPROXY, ffi.NULL)

        # Timeouts
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_CONNECTTIMEOUT, py2clong(connect_timeout))
        # libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_TIMEOUT, py2clong(60))

        # SSL CAINFO
        if sys.platform != "win32":
            # libcurl uses schannel on Windows which uses system CA certs
            cainfo = os.path.join(os.path.dirname(__file__), "cacert.pem")
            if os.path.exists(cainfo):
                dprint(self.easyhash + ": Using CAINFO from " + cainfo)
                libcurl.curl_easy_setopt(
                    self.easy, libcurl.CURLOPT_CAINFO, py2cstr(cainfo))

        # Set HTTP method
        self.method = method
        if method == "CONNECT":
            self.is_connect = True
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_CONNECT_ONLY, py2cbool(True))

            # No proxy yet so setup tunnel for direct CONNECT
            self.set_tunnel()

            if curl_version() < 0x072D00:
                # libcurl < v7.45 does not support CURLINFO_ACTIVESOCKET so it is not possible
                # to reuse existing connections
                libcurl.curl_easy_setopt(
                    self.easy, libcurl.CURLOPT_FRESH_CONNECT, py2cbool(True))
                dprint(self.easyhash + ": Fresh connection requested")

                # Need to know socket assigned for CONNECT since used later in select()
                # CURLINFO_ACTIVESOCKET not available on libcurl < v7.45  so need this
                # hack for older versions
                libcurl.curl_easy_setopt(
                    self.easy, libcurl.CURLOPT_SOCKOPTFUNCTION, libcurl.sockopt_callback)
                libcurl.curl_easy_setopt(
                    self.easy, libcurl.CURLOPT_SOCKOPTDATA, self.ceasyhash)

            # We want libcurl to make a simple HTTP connection to auth
            # with the upstream proxy and let client establish SSL
            if "://" not in url:
                url = "http://" + url
        elif method == "GET":
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_HTTPGET, py2cbool(True))
        elif method == "HEAD":
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_NOBODY, py2cbool(True))
        elif method == "POST":
            self.is_post = True
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_POST, py2cbool(True))
        elif method == "PUT":
            self.is_upload = True
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_UPLOAD, py2cbool(True))
        elif method in ["PATCH", "DELETE"]:
            if method == "PATCH":
                self.is_patch = True
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_CUSTOMREQUEST, py2cstr(method))
        else:
            dprint(self.easyhash + ": Unknown method: " + method)
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_CUSTOMREQUEST, py2cstr(method))

        self.url = url
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_URL, py2cstr(url))

        # Set HTTP version to use
        self.request_version = request_version
        version = request_version.split("/")[1].replace(".", "_")
        libcurl.curl_easy_setopt(self.easy, libcurl.CURLOPT_HTTP_VERSION,
                                 py2clong(getattr(libcurl, "CURL_HTTP_VERSION_" + version)))

        # Debug callback default disabled
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_DEBUGFUNCTION, libcurl.wa_callback)

        # Need libcurl verbose to save proxy auth mechanism
        self.set_verbose()

    def reset(self, url, method="GET", request_version="HTTP/1.1", connect_timeout=60):
        "Reuse existing curl instance for another request"
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

        self.cerr = libcurl.CURLE_OK
        self.done = False
        self.errstr = ""
        self.resp = 503
        self.sentheaders = False
        self.suppress = False

        self.is_connect = False
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
        "Set to tunnel through proxy if no proxy or proxy + auth"
        dprint(self.easyhash + ": HTTP proxy tunneling = " + str(tunnel))
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_HTTPPROXYTUNNEL, py2cbool(tunnel))
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_SUPPRESS_CONNECT_HEADERS, py2cbool(tunnel))
        self.is_tunnel = tunnel

    def set_proxy(self, proxy, port=0, noproxy=None):
        "Set proxy options - returns False if this proxy server has auth failures"
        if proxy in MCURL.failed:
            dprint(self.easyhash + ": Authentication issues with this proxy server")
            return False

        self.proxy = proxy
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_PROXY, py2cstr(proxy))
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_PROXYPORT, py2clong(port))
        if noproxy is not None:
            dprint(self.easyhash + ": Set noproxy to " + noproxy)
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_NOPROXY, py2cstr(noproxy))

        if self.is_connect:
            # Proxy but no auth (yet) so just connect and let client tunnel and authenticate
            self.set_tunnel(tunnel=False)

        return True

    def set_auth(self, user, password=None, auth="ANY"):
        "Set proxy authentication info - call after set_proxy() to enable auth caching"
        if user == ":":
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_PROXYUSERPWD, py2cstr(user))
        else:
            self.user = user
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_PROXYUSERNAME, py2cstr(user))
            if password is not None:
                libcurl.curl_easy_setopt(
                    self.easy, libcurl.CURLOPT_PROXYPASSWORD, py2cstr(password))
            else:
                dprint(self.easyhash + ": Blank password for user")
        if auth is not None:
            if self.proxy in MCURL.proxytype:
                # Use cached value
                self.auth = MCURL.proxytype[self.proxy]
                dprint(self.easyhash +
                       ": Using cached proxy auth mechanism " + self.auth)
            else:
                # Use specified value
                self.auth = auth
                dprint(self.easyhash +
                       ": Setting proxy auth mechanism to " + self.auth)

            authval = getauth(self.auth)
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_PROXYAUTH, py2clong(authval))

            if self.is_connect:
                # Proxy + auth so tunnel and authenticate
                self.set_tunnel()

    def set_headers(self, xheaders):
        "Set headers to send"
        self.headers = ffi.NULL
        skip_proxy_headers = True if self.proxy is not None and self.auth is not None else False
        for header in xheaders:
            lcheader = header.lower()
            if skip_proxy_headers and lcheader.startswith("proxy-"):
                # Don't forward proxy headers from client if no upstream proxy
                # or no auth specified (client will authenticate directly)
                dprint(self.easyhash + ": Skipping header =!> %s: %s" %
                       (header, xheaders[header]))
                continue
            elif lcheader == "content-length":
                size = int(xheaders[header])
                if self.is_upload or self.is_post:
                    # Save content-length for PUT/POST later
                    # Turn off Transfer-Encoding since size is known
                    self.size = size
                    self.headers = libcurl.curl_slist_append(
                        self.headers, py2cstr("Transfer-Encoding:"))
                    self.headers = libcurl.curl_slist_append(
                        self.headers, py2cstr("Expect:"))
                    if self.is_post:
                        libcurl.curl_easy_setopt(
                            self.easy, libcurl.CURLOPT_POSTFIELDSIZE, py2clong(size))
                    else:
                        libcurl.curl_easy_setopt(
                            self.easy, libcurl.CURLOPT_INFILESIZE, py2clong(size))
                elif self.is_patch:
                    # Get data from client - libcurl doesn't seem to use READFUNCTION
                    try:
                        data = self.client_rfile.read(size)
                    except AttributeError as exc:
                        dprint("set_headers() called before buffer()/bridge()?")
                        raise exc
                    libcurl.curl_easy_setopt(
                        self.easy, libcurl.CURLOPT_COPYPOSTFIELDS, py2custr(data))
            elif lcheader == "user-agent":
                # Forward user agent via setopt
                self.set_useragent(xheaders[header])
                continue
            dprint(self.easyhash + ": Adding header => " +
                   sanitized("%s: %s" % (header, xheaders[header])))
            self.headers = libcurl.curl_slist_append(self.headers,
                                                     py2cstr("%s: %s" % (header, xheaders[header])))

        if len(xheaders) != 0:
            if self.is_connect and not self.is_tunnel:
                # Send client headers later in select() - just connect to proxy
                # and let client tunnel and authenticate directly
                dprint(self.easyhash + ": Delaying headers")
                self.xheaders = xheaders
            else:
                dprint(self.easyhash + ": Setting headers")
                libcurl.curl_easy_setopt(
                    self.easy, libcurl.CURLOPT_HTTPHEADER, self.headers)

    def set_insecure(self, enable=True):
        "Set curl to ignore SSL errors"
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_SSL_VERIFYPEER, py2cbool(not enable))
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_SSL_VERIFYHOST, py2cbool(not enable))

    def set_verbose(self, enable=True):
        "Set verbose mode"
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_VERBOSE, py2cbool(enable))

    def set_debug(self, enable=True):
        """
        Enable debug output
          Call after set_proxy() and set_auth() to enable discovery and caching of proxy
          auth mechanism - libcurl does not provide an API to get this today - need to
          find it in sent header debug output
        """
        if enable:
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_DEBUGFUNCTION, libcurl.debug_callback)

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
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_READFUNCTION, libcurl.read_callback)
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_READDATA, self.ceasyhash)

        if client_wfile is not None:
            self.client_wfile = client_wfile
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_WRITEFUNCTION, libcurl.write_callback)
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_WRITEDATA, self.ceasyhash)

        if client_hfile is not None:
            self.client_hfile = client_hfile
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_HEADERFUNCTION, libcurl.header_callback)
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_HEADERDATA, self.ceasyhash)
        else:
            self.sentheaders = True

    def buffer(self, data=None):
        "Setup buffers to bridge curl perform"
        dprint(self.easyhash + ": Setting up buffers for bridge")
        rfile = None
        if data is not None:
            rfile = io.BytesIO()
            rfile.write(data)
            rfile.seek(0)

        wfile = io.BytesIO()
        hfile = io.BytesIO()

        self.bridge(rfile, wfile, hfile)

    def set_transfer_decoding(self, enable=False):
        "Set curl to turn off transfer decoding - let client do it"
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_HTTP_TRANSFER_DECODING, py2cbool(enable))

    def set_useragent(self, useragent):
        "Set user agent to send"
        if len(useragent) != 0:
            dprint(self.easyhash + ": Setting user agent to " + useragent)
            libcurl.curl_easy_setopt(
                self.easy, libcurl.CURLOPT_USERAGENT, py2cstr(useragent))

    def set_follow(self, enable=True):
        "Set curl to follow 3xx responses"
        libcurl.curl_easy_setopt(
            self.easy, libcurl.CURLOPT_FOLLOWLOCATION, py2cbool(enable))

    def perform(self):
        "Perform the easy handle"

        # Perform as a standalone easy handle, not using multi
        # However, add easyhash to MCURL.handles since it is used in curl callbacks
        MCURL.handles[self.easyhash] = self
        self.cerr = libcurl.curl_easy_perform(self.easy)
        if self.cerr != libcurl.CURLE_OK:
            dprint(self.easyhash + ": Connection failed: " +
                   str(self.cerr) + "; " + self.errstr)
        MCURL.handles.pop(self.easyhash)
        return self.cerr

    # Get status and info after running curl handle

    def get_response(self):
        "Return response code of completed request"
        codep = ffi.new("long *")
        if self.method == "CONNECT":
            ret = libcurl.curl_easy_getinfo(
                self.easy, libcurl.CURLINFO_HTTP_CONNECTCODE, codep)
        else:
            ret = libcurl.curl_easy_getinfo(
                self.easy, libcurl.CURLINFO_RESPONSE_CODE, codep)
        return ret, codep[0]

    def get_activesocket(self):
        "Return active socket for this easy instance"
        if sys.platform == "win32":
            sock_fd = ffi.new("unsigned int *")
        else:
            sock_fd = ffi.new("int *")
        ret = libcurl.curl_easy_getinfo(
            self.easy, libcurl.CURLINFO_ACTIVESOCKET, sock_fd)
        return ret, sock_fd[0]

    def get_primary_ip(self):
        "Return primary IP address of this easy instance"
        ip = ffi.new("char *[]")
        ret = libcurl.curl_easy_getinfo(
            self.easy, libcurl.CURLINFO_PRIMARY_IP, ip)
        return ret, ffi.string(ip).decode("utf-8")

    def get_used_proxy(self):
        "Return whether proxy was used for this easy instance"
        used_proxy = ffi.new("long *")
        ret = libcurl.curl_easy_getinfo(
            self.easy, libcurl.CURLINFO_USED_PROXY, used_proxy)
        return ret, used_proxy[0] != 0

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


@ffi.def_extern()
def socket_callback(easy, sock_fd, ev_bitmask, userp, socketp):
    # libcurl socket callback: add/remove actions for socket events
    del easy, userp, socketp
    if ev_bitmask & libcurl.CURL_POLL_IN or ev_bitmask & libcurl.CURL_POLL_INOUT:
        # dprint("Read sock_fd %d" % sock_fd)
        if sock_fd not in MCURL.rlist:
            MCURL.rlist.append(sock_fd)

    if ev_bitmask & libcurl.CURL_POLL_OUT or ev_bitmask & libcurl.CURL_POLL_INOUT:
        # dprint("Write sock_fd %d" % sock_fd)
        if sock_fd not in MCURL.wlist:
            MCURL.wlist.append(sock_fd)

    if ev_bitmask & libcurl.CURL_POLL_REMOVE:
        # dprint("Remove sock_fd %d" % sock_fd)
        if sock_fd in MCURL.rlist:
            MCURL.rlist.remove(sock_fd)
        if sock_fd in MCURL.wlist:
            MCURL.wlist.remove(sock_fd)

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


@ffi.def_extern()
def sockopt_callback(clientp, sock_fd, purpose):
    # Associate new socket with easy handle
    del purpose
    curl = MCURL.handles[cvp2pystr(clientp)]
    curl.sock_fd = sock_fd

    return libcurl.CURLE_OK


def print_curl_version():
    "Display curl version information"
    dprint(ffi.string(libcurl.curl_version()).decode("utf-8"))
    vinfo = libcurl.curl_version_info(libcurl.CURLVERSION_LAST-1)
    for feature in [
        "CURL_VERSION_SSL", "CURL_VERSION_SSPI", "CURL_VERSION_SPNEGO",
        "CURL_VERSION_GSSAPI", "CURL_VERSION_GSSNEGOTIATE",
        "CURL_VERSION_KERBEROS5", "CURL_VERSION_NTLM", "CURL_VERSION_NTLM_WB"
    ]:
        bit = getattr(libcurl, feature)
        avail = True if (bit & vinfo.features) > 0 else False
        dprint("%s: %s" % (feature, avail))
    dprint("Host: " + ffi.string(vinfo.host).decode("utf-8"))


def curl_version():
    return libcurl.curl_version_info(libcurl.CURLVERSION_LAST-1).version_num


class MCurl:
    "Helper class to manage a curl multi instance"

    _multi = None
    _lock = None

    handles = None
    proxytype = None
    failed = None  # Proxy servers with auth failures
    timer = None
    rlist = None
    wlist = None

    def __init__(self, debug_print=None):
        "Initialize multi interface"
        global dprint
        if debug_print is not None:
            dprint = debug_print
        else:
            # No need to sanitize since no debug
            def no_sanitized(msg): return msg
            sanitized = no_sanitized

        # Save as global to enable access via callbacks
        global MCURL
        MCURL = self

        print_curl_version()
        self._multi = libcurl.curl_multi_init()

        # Set a callback for registering or unregistering socket events.
        libcurl.curl_multi_setopt(
            self._multi, libcurl.CURLMOPT_SOCKETFUNCTION, libcurl.socket_callback)

        # Set a callback for scheduling or cancelling timeout actions.
        libcurl.curl_multi_setopt(
            self._multi, libcurl.CURLMOPT_TIMERFUNCTION, libcurl.multi_timer_callback)

        # Init
        self.handles = {}
        self.proxytype = {}
        self.failed = []
        self.rlist = []
        self.wlist = []
        self._lock = threading.Lock()

    def setopt(self, option, value):
        "Configure multi options"
        if option in (libcurl.CURLMOPT_SOCKETFUNCTION, libcurl.CURLMOPT_TIMERFUNCTION):
            raise Exception('Callback options reserved for the event loop')
        libcurl.curl_multi_setopt(self._multi, option, value)

    # Callbacks

    def _socket_action(self, sock_fd, ev_bitmask):
        # Event loop callback: act on ready sockets or timeouts
        # dprint("mask = %d, sock_fd = %d" % (ev_bitmask, sock_fd))
        handle_count = ffi.new("int *")
        _ = libcurl.curl_multi_socket_action(
            self._multi, sock_fd, ev_bitmask, handle_count)

        # Check if any handles have finished.
        if handle_count != len(self.handles):
            self._update_transfers()

    def _update_transfers(self):
        # Mark finished handles as done
        while True:
            queued = ffi.new("int *")
            pmsg: ffi.new("CURLMsg *") = libcurl.curl_multi_info_read(
                self._multi, queued)
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
                    curl.errstr = str(msg.data.result) + "; "

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
        "Add a Curl handle to perform"
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
        "Remove a Curl handle once done"
        with self._lock:
            self._remove_handle(curl)

    def stop(self, curl: Curl):
        "Stop a running curl handle and remove"
        with self._lock:
            self._remove_handle(curl, errstr="Stopped")

    # Executing multi

    def _perform(self):
        # Perform all tasks in the multi instance
        with self._lock:
            rlen = len(self.rlist)
            wlen = len(self.wlist)
            if rlen != 0 or wlen != 0:
                rready, wready, xready = select.select(
                    self.rlist, self.wlist, set(self.rlist) | set(self.wlist), self.timer)
            else:
                rready, wready, xready = [], [], []
                if self.timer is not None:
                    # Sleeping within lock - needs fix
                    time.sleep(self.timer)

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
        "Add a Curl handle and peform until completion"
        if not curl.is_easy:
            self.add(curl)
            while True:
                if curl.done:
                    break
                self._perform()
                time.sleep(0.01)
        else:
            dprint(curl.easyhash + ": Using easy interface")
            curl.perform()

        # Map some libcurl error codes to HTTP errors
        if curl.cerr == libcurl.CURLE_URL_MALFORMAT:
            # Bad request
            curl.resp = 400
            curl.errstr += "URL malformed"
        elif curl.cerr in [libcurl.CURLE_UNSUPPORTED_PROTOCOL,
                           libcurl.CURLE_NOT_BUILT_IN]:
            # Not implemented
            curl.resp = 501
            curl.errstr += "Unsupported protocol, not built-in, or function not found"
        elif curl.cerr in [libcurl.CURLE_COULDNT_RESOLVE_PROXY,
                           libcurl.CURLE_COULDNT_RESOLVE_HOST,
                           libcurl.CURLE_COULDNT_CONNECT]:
            # Bad gateway
            curl.resp = 502
            curl.errstr += "Could not resolve or connect to proxy or host"
        elif curl.cerr == libcurl.CURLE_OPERATION_TIMEDOUT:
            # Gateway timeout
            curl.resp = 504
            curl.errstr += "Operation timed out"

        if curl.proxy is not None:
            ret, codep = curl.get_response()
            if ret == 0 and codep == 407:
                # Proxy authentication required
                if curl.cerr == libcurl.CURLE_SEND_FAIL_REWIND:
                    # Issue #199 - POST/PUT rewind not supported
                    out = "POST/PUT rewind not supported (#199)"

                    # Retry since proxy auth not cached yet
                    curl.resp = 503
                    curl.errstr += out + "; "
                elif curl.auth is not None:
                    # Proxy auth did not work for whatever reason
                    out = "Proxy authentication failed: "
                    if curl.user is not None:
                        out += "check user/password or try different auth mechanism"
                    else:
                        out += "single sign-on failed, user/password might be required"

                    curl.resp = 401
                    curl.errstr += out + "; "

                    # Add this proxy to failed list and don't try again
                    with self._lock:
                        self.failed.append(curl.proxy)
                else:
                    # Setup client to authenticate directly with upstream proxy
                    dprint(curl.easyhash +
                           ": Client to authenticate with upstream proxy")
                    if not curl.is_connect:
                        # curl.errstr not set else connection will get closed during auth
                        curl.resp = codep

        if curl.is_connect and curl.sock_fd is None:
            # Need sock_fd for select()
            if curl_version() < 0x072D00:
                # This should never happen since we have set CURLOPT_FRESH_CONNECT = True
                # for CONNECT
                out = "Cannot reuse an SSL connection with libcurl < v7.45 - should never happen"
                dprint(curl.easyhash + ": " + out)
                curl.errstr += out + "; "
                curl.resp = 500
            else:
                # Get the active socket using getinfo() for select()
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
        "Run select loop between client and curl"
        # TODO figure out if IPv6 or IPv4
        if curl.sock_fd is None:
            dprint(curl.easyhash + ": Cannot select() without active socket")
            return

        dprint(curl.easyhash + ": Starting select loop")
        curl_sock = socket.fromfd(
            curl.sock_fd, socket.AF_INET, socket.SOCK_STREAM)

        ret, used_proxy = curl.get_used_proxy()
        if ret != libcurl.CURLE_OK:
            dprint(curl.easyhash + ": Failed to get used proxy: " + str(ret))
            return

        if curl.is_connect and (not curl.is_tunnel and used_proxy):
            # Send original headers from client to tunnel and authenticate with
            # upstream proxy
            dprint(curl.easyhash + ": Sending original client headers")
            curl_sock.sendall((f"{curl.method} {curl.url} {curl.request_version}\r\n").
                              encode("utf-8"))
            if curl.xheaders is not None:
                for header in curl.xheaders:
                    curl_sock.sendall(
                        f"{header}: {curl.xheaders[header]}\r\n".encode("utf-8"))
            curl_sock.sendall(b"\r\n")

        # sockets will be removed from these lists, when they are
        # detected as closed by remote host; wlist contains sockets
        # only when data has to be written
        rlist = [client_sock, curl_sock]
        wlist = []

        # data to be written to client connection and proxy socket
        cl = 0
        cs = 0
        cdata = []
        sdata = []
        max_idle = time.time() + idle
        while (rlist or wlist):
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
                        dprint(curl.easyhash + ": from %s: " %
                               source + str(exc))
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
                        dprint(curl.easyhash +
                               ": Connection closed by %s" % source)
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
                            wdata.pop(0)
                            if not data and o in wlist:
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

    # Cleanup multi

    def close(self):
        "Stop any running transfers and close this multi handle"
        dprint("Closing multi")
        for easyhash in tuple(self.handles):
            self.stop(self.handles[easyhash])
        libcurl.curl_multi_cleanup(self._multi)

        global MCURL
        MCURL = None

"""libcurl callbacks never raise and never depend on libcurl text being UTF-8 (genotrance/px#289).

On Windows libcurl writes SSPI and Schannel error text in the ANSI code page,
so a localized message (cp1252 here: 0x92 is the right single quote) is not
UTF-8. Proxies may send any bytes in headers. An exception inside a CFFI
callback is printed as "Exception ignored from cffi callback" and, on Windows,
shown in a "Python-CFFI error" message box, so every callback has an onerror
handler that returns the value libcurl needs instead.
"""

import base64
import os
import socketserver
import subprocess
import sys
import textwrap
import threading

import pytest

import mcurl
from mcurl import ffi, libcurl

USER = "user"
PASSWORD = "pass"  # noqa: S105
CP1252 = b"caf\xe9 l\x92h\xf4te"  # "cafe l'hote" with accents and a typographic apostrophe, invalid UTF-8
# SSPI Negotiate failure as libcurl formats it on a French Windows: English
# SEC_E_ name, then FormatMessageA text in the ANSI code page
SSPI_FR = (
    b"InitializeSecurityContext failed: SEC_E_NO_CREDENTIALS (0x8009030e) - "
    b"Aucune information d\x92identification n\x92est disponible dans le package de s\xe9curit\xe9\n"
)
# Schannel certificate error text (curlx_winapi_strerror), cp1252
SCHANNEL_FR = (
    b"schannel: CertGetCertificateChain trust error CERT_TRUST_IS_UNTRUSTED_ROOT - "
    b"Une cha\xeene de certificats a \xe9t\xe9 trait\xe9e mais s\x92est termin\xe9e\n"
)
NEGOTIATE_OUT = b"Proxy-Authorization: Negotiate YIIGhgYGKwYBBQUCoIIGejCCBnag\r\n\r\n"


@pytest.fixture
def multi():
    m = mcurl.MCurl()
    yield m
    m.close()


@pytest.fixture
def unraisable(monkeypatch):
    "Exceptions CFFI reports from callbacks (PyErr_WriteUnraisable)"
    seen = []
    monkeypatch.setattr(sys, "unraisablehook", lambda u: seen.append(repr(u.exc_value)))
    return seen


def _registered(multi, auth="ANY", debug=False):
    del multi
    curl = mcurl.Curl("http://origin.example/")
    curl.set_proxy("127.0.0.1", 9)
    curl.set_auth(USER, PASSWORD, auth)
    curl._debug = debug
    mcurl.MCURL.handles[curl.easyhash] = curl
    return curl


def _debug(curl, infotype, data, size=None):
    buf = ffi.new("char[]", data)
    return mcurl.debug_callback(curl.easy, infotype, buf, len(data) if size is None else size, ffi.NULL)


def test_sample_matches_reported_error():
    # The issue's traceback: "can't decode byte 0x92 in position 90"
    with pytest.raises(UnicodeDecodeError) as exc:
        SSPI_FR.decode("utf-8")
    assert (exc.value.start, SSPI_FR[90]) == (90, 0x92)


def test_yield_msgs_reads_exactly_size_bytes():
    # libcurl's data is not NUL-terminated: an embedded NUL must not cut it
    # short and bytes past size must not be read
    data = ffi.new("char[]", b"a\x00b\r\nc\x92d\r\nNOT PART OF IT")
    assert list(mcurl.yield_msgs(data, 10)) == ["a\x00b", "c�d"]


@pytest.mark.parametrize("debug", [False, True], ids=["nodebug", "debug"])
@pytest.mark.parametrize("infotype", ["CURLINFO_TEXT", "CURLINFO_HEADER_IN", "CURLINFO_HEADER_OUT"])
@pytest.mark.parametrize(
    "data", [SSPI_FR, SCHANNEL_FR, b"X-Note: " + CP1252 + b"\r\n"], ids=["sspi", "schannel", "hdr"]
)
def test_debug_callback_cp1252(multi, monkeypatch, data, infotype, debug):
    out = []
    monkeypatch.setattr(mcurl, "dprint", out.append)
    curl = _registered(multi, debug=debug)
    curl._negotiate_watch = True
    del out[:]
    assert _debug(curl, getattr(libcurl, infotype), data) == libcurl.CURLE_OK
    if debug:
        assert len(out) == 1 and "�" in out[0]
    else:
        assert out == []


def test_negotiate_token_matched_on_bytes_next_to_cp1252(multi):
    curl = _registered(multi)
    curl._negotiate_watch = True
    _debug(curl, libcurl.CURLINFO_HEADER_OUT, b"X-Note: " + CP1252 + b"\r\n")
    assert not curl._negotiate_token_sent
    _debug(curl, libcurl.CURLINFO_HEADER_OUT, b"CONNECT a:443 HTTP/1.1\r\nX-Note: " + CP1252 + b"\r\n" + NEGOTIATE_OUT)
    assert curl._negotiate_token_sent


def test_negotiate_token_needs_watch_and_size(multi):
    curl = _registered(multi)
    # Not watched: nothing is inspected
    _debug(curl, libcurl.CURLINFO_HEADER_OUT, NEGOTIATE_OUT)
    assert not curl._negotiate_token_sent
    # Watched: only the size bytes libcurl handed over count
    curl._negotiate_watch = True
    prefix = b"Host: a\r\n"
    _debug(curl, libcurl.CURLINFO_HEADER_OUT, prefix + NEGOTIATE_OUT, size=len(prefix))
    assert not curl._negotiate_token_sent
    # A token in received headers or text is not evidence
    _debug(curl, libcurl.CURLINFO_HEADER_IN, NEGOTIATE_OUT)
    _debug(curl, libcurl.CURLINFO_TEXT, NEGOTIATE_OUT)
    assert not curl._negotiate_token_sent


def test_header_and_write_callbacks_pass_bytes_exactly(multi):
    curl = _registered(multi)
    curl.buffer()
    header = b"X-Note: " + CP1252 + b"\x00tail\r\n"
    buf = ffi.new("char[]", header + b"PAST SIZE")
    assert mcurl.header_callback(buf, 1, len(header), curl.ceasyhash) == len(header)
    assert mcurl.header_callback(ffi.new("char[]", b"\r\n"), 1, 2, curl.ceasyhash) == 2
    body = CP1252 + b"\x00" + CP1252
    assert mcurl.write_callback(ffi.new("char[]", body), 1, len(body), curl.ceasyhash) == len(body)
    assert curl.get_headers(encoding=None) == header + b"\r\n"
    assert curl.get_data(encoding=None) == body


def test_read_callback_passes_bytes_exactly(multi):
    curl = _registered(multi)
    body = CP1252 + b"\x00" + CP1252
    curl.buffer(body)
    curl.set_headers({"Content-Length": len(body)})
    out = ffi.new("char[]", 64)
    n = mcurl.read_callback(out, 1, 64, curl.ceasyhash)
    assert ffi.unpack(out, n) == body


# Every callback through the C entry point libcurl calls. The state makes each
# one raise (MCurl is not created yet, or the handle is unknown); the onerror
# handler must keep the exception out of CFFI's reporting and return the value
# that tells libcurl to stop (or, for the debug callback, to carry on).
ENTRY_POINTS = textwrap.dedent(
    """
    import sys
    import mcurl
    from mcurl import ffi, libcurl

    logged = []
    mcurl.dprint = logged.append

    def call(name, *args):
        return ffi.addressof(libcurl, name)(*args)

    buf = ffi.new("char[]", %(sspi)r)
    key = ffi.new("char[]", b"unknown handle")
    # MCURL is None: each of these raises AttributeError
    print("socket_callback", call("socket_callback", ffi.NULL, 3, libcurl.CURL_POLL_IN, ffi.NULL, ffi.NULL))
    print("multi_timer_callback", call("multi_timer_callback", ffi.NULL, 10, ffi.NULL))
    print("debug_callback", call("debug_callback", ffi.NULL, libcurl.CURLINFO_TEXT, buf, len(buf) - 1, ffi.NULL))
    # An unknown handle: each of these raises KeyError
    c = mcurl.Curl("http://origin.example/")
    print("read_callback", call("read_callback", buf, 1, 16, key))
    print("write_callback", call("write_callback", buf, 1, 16, key))
    print("header_callback", call("header_callback", buf, 1, 16, key))
    print("xferinfo_callback", call("xferinfo_callback", key, 0, 0, 0, 0))
    print("seek_callback", call("seek_callback", key, 0, 0))
    # The px#289 case itself: localized libcurl text on a proxied auth handle
    c.set_proxy("127.0.0.1", 9)
    c.set_auth("u", "p", "ANY")
    mcurl.MCURL.handles[c.easyhash] = c
    for debug in (False, True):
        c._debug = debug
        print("text", call("debug_callback", c.easy, libcurl.CURLINFO_TEXT, buf, len(buf) - 1, ffi.NULL))
    print("logged", sum(m.startswith("Error in ") for m in logged))
    sys.stdout.flush()
    """
)


def test_callbacks_never_raise_from_c_entry_point():
    code = ENTRY_POINTS % {"sspi": SSPI_FR}
    # Run from the directory holding the mcurl under test so "-c" imports it
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(mcurl.__file__)))
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False, cwd=cwd
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cffi callback" not in proc.stderr.lower(), proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    got = dict(line.rsplit(" ", 1) for line in proc.stdout.splitlines())
    assert got == {
        "socket_callback": "-1",
        "multi_timer_callback": "-1",
        "debug_callback": str(libcurl.CURLE_OK),
        "read_callback": str(libcurl.CURL_READFUNC_ABORT),
        "write_callback": "0",
        "header_callback": "0",
        "xferinfo_callback": "1",
        "seek_callback": str(libcurl.CURL_SEEKFUNC_FAIL),
        "text": str(libcurl.CURLE_OK),
        "logged": "8",
    }, proc.stdout


# Implicit verbose output: only set_debug() and the Negotiate watch turn it on


@pytest.fixture
def setopts(patch_mcurl):
    seen = []
    real = libcurl.curl_easy_setopt

    def spy(easy, option, value):
        seen.append(option)
        return real(easy, option, value)

    patch_mcurl("libcurl", "curl_easy_setopt", spy)
    return seen


@pytest.mark.parametrize(
    ("auth", "debug", "verbose"),
    [
        ("NTLM", False, False),
        ("BASIC", False, False),
        ("DIGEST", False, False),
        ("ANY", False, True),
        ("NEGOTIATE", False, True),
        ("NTLM", True, True),
    ],
)
def test_verbose_only_with_debug_or_negotiate(multi, setopts, auth, debug, verbose):
    curl = mcurl.Curl("http://origin.example/")
    curl.set_proxy("127.0.0.1", 9)
    curl.set_auth(USER, PASSWORD, auth)
    curl.set_debug(debug)
    curl.buffer()
    del setopts[:]
    multi.do(curl)
    assert curl.resp == 502, curl.errstr
    assert curl._negotiate_watch is (auth in ("ANY", "NEGOTIATE"))
    if debug:
        # set_debug() already set them; do() leaves them alone
        assert libcurl.CURLOPT_VERBOSE not in setopts
    else:
        assert (libcurl.CURLOPT_VERBOSE in setopts) is verbose
        assert (libcurl.CURLOPT_DEBUGFUNCTION in setopts) is verbose


def test_watch_is_turned_off_when_no_longer_needed(multi, setopts):
    curl = mcurl.Curl("http://origin.example/")
    curl.set_proxy("127.0.0.1", 9)
    curl.set_auth(USER, PASSWORD, "ANY")
    multi.do(curl)
    assert curl._negotiate_watch
    # A clone keeps libcurl's options, then the method is narrowed to NTLM
    clone = curl.dup()
    clone.set_auth(USER, PASSWORD, "NTLM")
    del setopts[:]
    multi.do(clone)
    assert not clone._negotiate_watch
    assert libcurl.CURLOPT_VERBOSE in setopts and libcurl.CURLOPT_DEBUGFUNCTION in setopts


def test_no_verbose_without_proxy_auth(multi, setopts):
    curl = mcurl.Curl("http://127.0.0.1:9/")
    curl.buffer()
    multi.do(curl)
    assert libcurl.CURLOPT_VERBOSE not in setopts


# Non-UTF-8 bytes from a real proxied transfer through libcurl


class _Cp1252Proxy(socketserver.ThreadingTCPServer):
    "Keep-alive proxy and origin at once: Basic 407 then 200, both with cp1252 header bytes"

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, user=b"user"):
        super().__init__(("127.0.0.1", 0), _Cp1252Handler)
        self.expected = b"Basic " + base64.b64encode(user + b":" + PASSWORD.encode())
        self.requests = 0
        threading.Thread(target=self.serve_forever, daemon=True).start()


class _Cp1252Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            line = self.rfile.readline()
            if not line:
                return
            headers = {}
            while True:
                hline = self.rfile.readline()
                if hline in (b"\r\n", b"\n", b""):
                    break
                name, _, value = hline.partition(b":")
                headers[name.strip().lower()] = value.strip()
            body = self.rfile.read(int(headers.get(b"content-length", b"0")))
            self.server.requests += 1
            if headers.get(b"proxy-authorization") != self.server.expected:
                self.wfile.write(
                    b"HTTP/1.1 407 Proxy Authentication Required " + CP1252 + b"\r\n"
                    b'Proxy-Authenticate: Basic realm="' + CP1252 + b'"\r\n'
                    b"X-Proxy-Note: " + CP1252 + b"\r\nContent-Length: 0\r\n\r\n"
                )
                continue
            body = body or CP1252 + b"\x00" + CP1252
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\nX-Origin-Note: " + CP1252 + b"\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            )


@pytest.fixture
def cp1252_proxy(request):
    server = _Cp1252Proxy(**getattr(request, "param", {}))
    yield server
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("upload", [False, True], ids=["get", "post"])
@pytest.mark.parametrize("debug", [False, True], ids=["nodebug", "debug"])
def test_proxy_cp1252_transfer(multi, unraisable, cp1252_proxy, debug, upload):
    curl = mcurl.Curl("http://origin.example/", "POST" if upload else "GET")
    curl.set_proxy("127.0.0.1", cp1252_proxy.server_address[1])
    curl.set_auth(USER, PASSWORD, "ANY")
    curl.set_debug(debug)
    body = CP1252 + b"\x00upload"
    if upload:
        curl.buffer(body)
        curl.set_headers({"Content-Length": len(body)})
    else:
        curl.buffer()
    assert multi.do(curl), curl.errstr
    assert curl.get_response() == (0, 200)
    assert cp1252_proxy.requests >= 2  # the cp1252 407 leg happened
    assert curl.get_data(encoding=None) == (body if upload else CP1252 + b"\x00" + CP1252)
    assert b"X-Origin-Note: " + CP1252 + b"\r\n" in curl.get_headers(encoding=None)
    assert unraisable == []


@pytest.mark.parametrize("cp1252_proxy", [{"user": b"jos\xe9"}], indirect=True)
def test_libcurl_non_utf8_text_without_debug(multi, unraisable, cp1252_proxy):
    # libcurl itself puts non-UTF-8 bytes into its text ("Proxy auth using
    # Basic with user '...'"): the Linux stand-in for localized Windows text,
    # while the Negotiate watch has the debug callback on
    curl = mcurl.Curl("http://origin.example/")
    curl.set_proxy("127.0.0.1", cp1252_proxy.server_address[1])
    curl.set_auth(USER, PASSWORD, "ANY")
    curl.setopt(libcurl.CURLOPT_PROXYUSERNAME, b"jos\xe9")
    curl.buffer()
    assert multi.do(curl), curl.errstr
    assert curl._negotiate_watch
    assert curl.get_response() == (0, 200)
    assert unraisable == []

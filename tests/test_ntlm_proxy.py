"""Concurrent multi-leg NTLM proxy authentication through one MCurl (px#268).

A fake upstream proxy behaves like a connection-based SSPI/NTLM proxy: every
new connection is challenged with 407 first, then the type1/type2/type3 legs
run on one connection and only that connection is authenticated afterwards.
Intermediate 407 legs, and legs on connections the proxy closes, must never
count towards MCurl.failed; only a handshake that ends in a final 407 does.
"""

import base64
import socket
import socketserver
import struct
import threading

import pytest

import mcurl

TARGET = "http://ntlm-proxy.invalid/"
CONNECT_TARGET = "ntlm-proxy.invalid:443"
USER = "user"
BURST = 15
HAS_NTLM = bool(mcurl.get_curl_vinfo().features & mcurl.libcurl.CURL_VERSION_NTLM)

pytestmark = pytest.mark.skipif(
    not HAS_NTLM,
    reason="libcurl was built without NTLM support",
)


def _type2():
    # Minimal NTLM type 2 (challenge) message with unicode and NTLM flags
    flags = 0x00000001 | 0x00000200
    return (
        b"NTLMSSP\x00" + struct.pack("<IHHII", 2, 0, 0, 40, flags) + b"\x01\x02\x03\x04\x05\x06\x07\x08" + b"\x00" * 8
    )


def _type3_user(msg):
    # Return the user name from an NTLM type 3 (authenticate) message
    length, _, offset = struct.unpack("<HHI", msg[36:44])
    return msg[offset : offset + length].decode("utf-16-le", "replace")


class _NtlmProxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, close_first=False, negotiate=False, refuse_after_close=False):
        super().__init__(("127.0.0.1", 0), _NtlmHandler)
        # close_first: close the connection after the initial challenge, so
        # libcurl has to reconnect to send type1 (keep-alive otherwise)
        self.close_first = close_first
        self.negotiate = negotiate
        self.drop_type2 = 0  # Handshakes to interrupt by closing before type2
        self.drop_headers = 0  # Handshakes to cut off in the middle of the 407 headers
        self.refuse_after_close = refuse_after_close  # Stop listening after a 407 with Connection: close
        self.extra_headers = []  # Added to 200 responses
        self.lock = threading.Lock()
        self.challenges = 0  # 407 responses sent, intermediate or final
        self.authenticated = 0  # Successful type3 legs
        self.bodies = []  # (method, body) of requests answered after authentication

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class _NtlmHandler(socketserver.StreamRequestHandler):
    def _read_request(self):
        line = self.rfile.readline()
        if not line.strip():
            return None, {}
        headers = {}
        while True:
            header = self.rfile.readline()
            if header in (b"\r\n", b"\n", b""):
                break
            name, _, value = header.decode("latin-1").partition(":")
            headers[name.strip().lower()] = value.strip()
        length = int(headers.get("content-length", 0))
        self.body = self.rfile.read(length) if length else b""
        return line.decode("latin-1").split()[0], headers

    def _send(self, code, headers=(), body=b""):
        reason = {200: "OK", 407: "Proxy Authentication Required"}[code]
        out = [f"HTTP/1.1 {code} {reason}", *headers]
        if code == 200:
            out.extend(self.server.extra_headers)
        if code != 200 or body:
            out.append(f"Content-Length: {len(body)}")
        self.wfile.write(("\r\n".join(out) + "\r\n\r\n").encode() + body)
        self.wfile.flush()

    def _challenge(self, headers, close=False):
        with self.server.lock:
            self.server.challenges += 1
        if close:
            headers = [*headers, "Connection: close"]
        self._send(407, headers, b"auth required")

    def _authenticate(self, headers):
        # Run one handshake leg; returns True once authenticated, False to
        # await the next leg on this connection, None to close the connection
        server = self.server
        scheme, _, token = headers.get("proxy-authorization", "").partition(" ")
        msg = base64.b64decode(token) if scheme.upper() == "NTLM" and token else b""
        kind = struct.unpack("<I", msg[8:12])[0] if len(msg) >= 12 else 0
        if kind == 1:
            with server.lock:
                drop = server.drop_type2 > 0
                server.drop_type2 -= drop
                cut = server.drop_headers > 0
                server.drop_headers -= cut
            if drop:
                # Interrupt the handshake: close instead of sending type2
                return None
            if cut:
                # Close in the middle of the 407 header block
                self.wfile.write(b"HTTP/1.1 407 X\r\nProxy-Authenticate: NTLM ")
                self.wfile.flush()
                return None
            self._challenge(["Proxy-Authenticate: NTLM " + base64.b64encode(_type2()).decode()])
            return False
        if kind == 3 and _type3_user(msg) == USER:
            with server.lock:
                server.authenticated += 1
            return True
        offers = ["Proxy-Authenticate: NTLM"]
        if server.negotiate:
            offers.insert(0, "Proxy-Authenticate: Negotiate")
        close = kind == 3 or server.close_first
        self._challenge(offers, close=close)
        if close and server.refuse_after_close:
            # Further connects are refused (ECONNREFUSED)
            server.socket.shutdown(socket.SHUT_RD)
        return None if close else False

    def handle(self):
        authenticated = False
        while True:
            method, headers = self._read_request()
            if method is None:
                return
            if not authenticated:
                authenticated = self._authenticate(headers)
                if authenticated is None:
                    return
                if not authenticated:
                    continue
            if method == "CONNECT":
                self._send(200, ["Proxy-Agent: fake-ntlm"])
                # CONNECT_ONLY tunnel: hold it open until the client closes
                while self.rfile.read(4096):
                    pass
                return
            with self.server.lock:
                self.server.bodies.append((method, self.body))
            self._send(200, [], b"ok")


@pytest.fixture
def ntlm_proxy(request):
    server = _NtlmProxy(**getattr(request, "param", {}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def ffi_slist_guard(slist):
    # Free the slist when the handle (and so the keepalive list) is collected
    return mcurl.ffi.gc(slist, mcurl.libcurl.curl_slist_free_all)


def _request(multi, url, connect, user=USER, auth="ANY", resolve=None):
    # Returns (admitted, success, curl); resolve is a CURLOPT_RESOLVE entry
    curl = mcurl.Curl(CONNECT_TARGET, method="CONNECT") if connect else mcurl.Curl(TARGET)
    if not connect:
        curl.buffer()
    if not curl.set_proxy(url):
        return False, False, curl
    if resolve is not None:
        slist = mcurl.libcurl.curl_slist_append(mcurl.ffi.NULL, resolve.encode())
        curl._keepalive.append(ffi_slist_guard(slist))
        curl.setopt(mcurl.libcurl.CURLOPT_RESOLVE, slist)
    curl.set_auth(user, "pass", auth=auth)
    ok = multi.do(curl)
    return True, ok, curl


def _burst(multi, url, connect, count=BURST, user=USER, auth="ANY"):
    results = [None] * count
    barrier = threading.Barrier(count)

    def run(i):
        barrier.wait()
        results[i] = _request(multi, url, connect, user, auth)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results


VARIANTS = [
    pytest.param({}, id="keepalive"),
    pytest.param({"close_first": True}, id="close"),
    pytest.param({"negotiate": True}, id="negotiate-keepalive"),
    pytest.param({"negotiate": True, "close_first": True}, id="negotiate-close"),
]


@pytest.mark.parametrize("connect", [False, True], ids=["http", "connect"])
@pytest.mark.parametrize("ntlm_proxy", VARIANTS, indirect=True)
def test_concurrent_multi_leg_auth_does_not_block(ntlm_proxy, connect):
    multi = mcurl.MCurl()
    try:
        for _ in range(3):
            results = _burst(multi, ntlm_proxy.url, connect)
            detail = [(c.cerr, c.resp, c.errstr) for _, ok, c in results if not ok]
            assert all(admitted for admitted, _, _ in results), detail
            assert all(ok for _, ok, _ in results), detail
            assert multi.failed.get(ntlm_proxy.url, 0) == 0
            for _, _, curl in results:
                curl.close()
        assert ntlm_proxy.challenges > 0
        assert ntlm_proxy.authenticated > 0
    finally:
        multi.close()


@pytest.mark.parametrize("connect", [False, True], ids=["http", "connect"])
@pytest.mark.parametrize("ntlm_proxy", VARIANTS, indirect=True)
def test_success_resets_failures(ntlm_proxy, connect):
    multi = mcurl.MCurl()
    try:
        multi.failed[ntlm_proxy.url] = multi.failure_threshold - 1
        admitted, ok, curl = _request(multi, ntlm_proxy.url, connect)
        assert admitted and ok, (curl.cerr, curl.resp, curl.errstr)
        assert multi.failed[ntlm_proxy.url] == 0
        # Negotiate+NTLM proxies: confirms ANY actually fell back to NTLM,
        # not that Negotiate somehow completed (it cannot, without a KDC)
        assert multi.proxyauth.get(ntlm_proxy.url) == mcurl.libcurl.CURLAUTH_NTLM
    finally:
        multi.close()


@pytest.mark.parametrize("connect", [False, True], ids=["http", "connect"])
@pytest.mark.parametrize("ntlm_proxy", VARIANTS, indirect=True)
def test_wrong_credentials_block_after_threshold(ntlm_proxy, connect):
    multi = mcurl.MCurl()
    try:
        for i in range(multi.failure_threshold):
            admitted, ok, curl = _request(multi, ntlm_proxy.url, connect, user="wrong")
            assert admitted and not ok
            assert curl.resp == 401
            assert multi.failed[ntlm_proxy.url] == i + 1
        admitted, _, _ = _request(multi, ntlm_proxy.url, connect)
        assert not admitted
    finally:
        multi.close()


@pytest.mark.parametrize("connect", [False, True], ids=["http", "connect"])
def test_wrong_credentials_burst_blocks(ntlm_proxy, connect):
    multi = mcurl.MCurl()
    try:
        results = _burst(multi, ntlm_proxy.url, connect, user="wrong")
        assert not any(ok for _, ok, _ in results)
        assert multi.failed[ntlm_proxy.url] >= multi.failure_threshold
        assert not _request(multi, ntlm_proxy.url, connect)[0]
    finally:
        multi.close()


@pytest.mark.parametrize("connect", [False, True], ids=["http", "connect"])
@pytest.mark.parametrize("ntlm_proxy", VARIANTS[:2], indirect=True)
def test_interrupted_handshakes_do_not_count(ntlm_proxy, connect):
    # Handshakes cut by a network error before type2 are inconclusive, even
    # though libcurl still reports the earlier 407 leg as HTTP_CONNECTCODE
    multi = mcurl.MCurl()
    try:
        ntlm_proxy.drop_type2 = multi.failure_threshold + 2
        for _ in range(multi.failure_threshold + 2):
            admitted, ok, curl = _request(multi, ntlm_proxy.url, connect)
            # libcurl may silently retry a request whose reused connection closed
            assert admitted
            assert ok or curl.resp == 502, (curl.cerr, curl.resp, curl.errstr)
            assert multi.failed.get(ntlm_proxy.url, 0) == 0
        admitted, ok, curl = _request(multi, ntlm_proxy.url, connect)
        assert admitted and ok, (curl.cerr, curl.resp, curl.errstr)
        assert multi.failed[ntlm_proxy.url] == 0
    finally:
        multi.close()


@pytest.mark.parametrize("connect", [False, True], ids=["http", "connect"])
@pytest.mark.parametrize("ntlm_proxy", VARIANTS[:2], indirect=True)
def test_concurrent_interrupted_handshakes_do_not_block(ntlm_proxy, connect):
    multi = mcurl.MCurl()
    try:
        # v8.23.0.2 counted each of these and refused every later request
        ntlm_proxy.drop_type2 = multi.failure_threshold
        results = _burst(multi, ntlm_proxy.url, connect, count=multi.failure_threshold)
        assert all(admitted for admitted, _, _ in results)
        assert [c.resp for _, ok, c in results if not ok and c.resp != 502] == []
        assert multi.failed.get(ntlm_proxy.url, 0) == 0
        results = _burst(multi, ntlm_proxy.url, connect)
        detail = [(c.cerr, c.resp, c.errstr) for _, ok, c in results if not ok]
        assert all(admitted and ok for admitted, ok, _ in results), detail
        assert multi.failed[ntlm_proxy.url] == 0
    finally:
        multi.close()


@pytest.mark.parametrize("connect", [False, True], ids=["http", "connect"])
def test_final_407_counts_after_failed_connect_attempt(ntlm_proxy, connect):
    # The proxy host resolves first to a refused address, so the transfer
    # carries an earlier socket errno (CURLINFO_OS_ERRNO) when the genuine
    # final 407 arrives. The errno must not make the 407 inconclusive.
    multi = mcurl.MCurl()
    port = ntlm_proxy.server_address[1]
    url = f"http://ntlm-proxy.test:{port}"
    resolve = f"ntlm-proxy.test:{port}:127.0.0.2,127.0.0.1"
    try:
        for i in range(multi.failure_threshold):
            admitted, ok, curl = _request(multi, url, connect, user="wrong", resolve=resolve)
            assert admitted and not ok
            assert curl.get_os_errno() != 0
            assert curl.resp == 401, (curl.cerr, curl.resp, curl.errstr)
            assert multi.failed[url] == i + 1
        assert not _request(multi, url, connect, resolve=resolve)[0]
    finally:
        multi.close()


@pytest.mark.parametrize("connect", [False, True], ids=["http", "connect"])
@pytest.mark.parametrize("ntlm_proxy", [{"close_first": True, "refuse_after_close": True}], indirect=True)
def test_407_close_then_refused_reconnect_is_inconclusive(ntlm_proxy, connect):
    # A 407 leg with Connection: close followed by a refused reconnect leaves
    # that leg's 407 as the connect code, but it was not the final answer
    multi = mcurl.MCurl()
    try:
        for _ in range(multi.failure_threshold + 1):
            admitted, ok, curl = _request(multi, ntlm_proxy.url, connect)
            assert admitted and not ok
            assert curl.cerr != 0 and curl.get_os_errno() != 0
            assert curl.resp == 502, (curl.cerr, curl.resp, curl.errstr)
            assert multi.failed.get(ntlm_proxy.url, 0) == 0
    finally:
        multi.close()


@pytest.mark.parametrize("connect", [False, True], ids=["http", "connect"])
@pytest.mark.parametrize("ntlm_proxy", VARIANTS[:2], indirect=True)
def test_truncated_407_headers_do_not_count(ntlm_proxy, connect):
    # A proxy that closes in the middle of the 407 header block never gave an
    # auth answer, even if libcurl reports CURLE_OK with response code 407
    multi = mcurl.MCurl()
    try:
        ntlm_proxy.drop_headers = multi.failure_threshold + 2
        for _ in range(multi.failure_threshold + 2):
            admitted, ok, curl = _request(multi, ntlm_proxy.url, connect)
            assert admitted
            assert ok or curl.resp == 502, (curl.cerr, curl.resp, curl.errstr)
            assert multi.failed.get(ntlm_proxy.url, 0) == 0
        admitted, ok, curl = _request(multi, ntlm_proxy.url, connect)
        assert admitted and ok, (curl.cerr, curl.resp, curl.errstr)
    finally:
        multi.close()


@pytest.mark.parametrize("ntlm_proxy", [{"negotiate": True}], indirect=True)
def test_failed_auth_method_is_not_cached(ntlm_proxy):
    # ANY picks Negotiate, which cannot complete without a KDC, so mcurl
    # retries with NTLM; with a wrong password that also fails, and the final
    # 407 must not cache a method that overrides a later explicit NTLM request
    multi = mcurl.MCurl()
    try:
        admitted, ok, curl = _request(multi, ntlm_proxy.url, False, user="wrong", auth="ANY")
        assert admitted and not ok and curl.resp == 401, (curl.cerr, curl.resp, curl.errstr)
        assert ntlm_proxy.url not in multi.proxyauth
        assert multi.failed[ntlm_proxy.url] == 1
        admitted, ok, curl = _request(multi, ntlm_proxy.url, False, auth="NTLM")
        assert admitted and ok, (curl.cerr, curl.resp, curl.errstr)
        assert multi.failed[ntlm_proxy.url] == 0
        assert multi.proxyauth.get(ntlm_proxy.url) == mcurl.libcurl.CURLAUTH_NTLM
    finally:
        multi.close()


@pytest.mark.parametrize("connect", [False, True], ids=["http", "connect"])
def test_final_407_clears_cached_auth(ntlm_proxy, connect):
    multi = mcurl.MCurl()
    try:
        admitted, ok, _ = _request(multi, ntlm_proxy.url, connect)
        assert admitted and ok
        assert multi.proxyauth[ntlm_proxy.url] == mcurl.libcurl.CURLAUTH_NTLM
    finally:
        multi.close()
    multi = mcurl.MCurl()
    try:
        # A method cached earlier is dropped once the proxy rejects it
        multi.proxyauth[ntlm_proxy.url] = mcurl.libcurl.CURLAUTH_NTLM
        admitted, ok, curl = _request(multi, ntlm_proxy.url, connect, user="wrong")
        assert admitted and not ok and curl.resp == 401
        assert ntlm_proxy.url not in multi.proxyauth
    finally:
        multi.close()


def test_reset_proxy_failures_clears_cached_auth():
    multi = mcurl.MCurl()
    try:
        multi.proxyauth.update({"a": 1, "b": 2})
        multi.reset_proxy_failures("a")
        assert multi.proxyauth == {"b": 2}
        multi.reset_proxy_failures()
        assert multi.proxyauth == {}
    finally:
        multi.close()


@pytest.mark.parametrize("ntlm_proxy", [{}], indirect=True)
def test_header_containing_407_is_not_a_407_status(ntlm_proxy):
    # Only an HTTP status line with code 407 starts header suppression
    ntlm_proxy.extra_headers = ["Host-Id: a407", "Proxy-Agent: fake-407", "X-Code: 407"]
    multi = mcurl.MCurl()
    try:
        admitted, ok, curl = _request(multi, ntlm_proxy.url, False)
        assert admitted and ok, (curl.cerr, curl.resp, curl.errstr)
        headers = curl.get_headers()
        for line in ntlm_proxy.extra_headers:
            assert line in headers
        # The 407 challenge legs of the handshake stay hidden from the client
        assert "407" not in headers.split("\r\n", 1)[0]
        assert "Proxy-Authenticate" not in headers
    finally:
        multi.close()


BODY = bytes(range(256)) * 24  # 6 KB, not text


def _body_request(multi, url, method, body, auth="ANY", user=USER, easy=False):
    # POST/PUT with a known-length body supplied through the read callback
    curl = mcurl.Curl(TARGET, method=method)
    curl.buffer(body)
    curl.set_headers({"Content-Length": str(len(body))})
    curl.is_easy = easy
    assert curl.set_proxy(url)
    curl.set_auth(user, "pass", auth=auth)
    return multi.do(curl), curl


@pytest.mark.parametrize("easy", [False, True], ids=["multi", "easy"])
@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize("ntlm_proxy", [v.values[0] for v in VARIANTS], ids=[v.id for v in VARIANTS], indirect=True)
def test_body_resent_across_auth_legs(ntlm_proxy, method, easy):
    # ANY sends the whole body on the first unauthenticated leg, so libcurl has
    # to rewind and resend it on the authenticated leg (and, with Negotiate
    # failing locally, once more for the NTLM retry)
    multi = mcurl.MCurl()
    try:
        ok, curl = _body_request(multi, ntlm_proxy.url, method, BODY, easy=easy)
        assert ok and curl.cerr == 0 and curl.get_response()[1] == 200, (curl.cerr, curl.resp, curl.errstr)
        assert ntlm_proxy.bodies == [(method, BODY)]
        assert multi.failed.get(ntlm_proxy.url, 0) == 0
    finally:
        multi.close()


@pytest.mark.parametrize("easy", [False, True], ids=["multi", "easy"])
@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize("ntlm_proxy", [{}, {"negotiate": True}], indirect=True, ids=["ntlm", "negotiate"])
def test_body_explicit_ntlm(ntlm_proxy, method, easy):
    multi = mcurl.MCurl()
    try:
        ok, curl = _body_request(multi, ntlm_proxy.url, method, BODY, auth="NTLM", easy=easy)
        assert ok and curl.get_response()[1] == 200, (curl.cerr, curl.resp, curl.errstr)
        assert ntlm_proxy.bodies == [(method, BODY)]
        assert multi.failed.get(ntlm_proxy.url, 0) == 0
    finally:
        multi.close()


@pytest.mark.parametrize("easy", [False, True], ids=["multi", "easy"])
@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize(
    "ntlm_proxy",
    [{"negotiate": True}, {"negotiate": True, "close_first": True}],
    indirect=True,
    ids=["keepalive", "close"],
)
def test_body_wrong_password_counts_once(ntlm_proxy, method, easy):
    multi = mcurl.MCurl()
    try:
        ok, curl = _body_request(multi, ntlm_proxy.url, method, BODY, user="wrong", easy=easy)
        assert not ok and curl.resp == 401, (curl.cerr, curl.resp, curl.errstr)
        assert multi.failed[ntlm_proxy.url] == 1
        assert ntlm_proxy.bodies == []
    finally:
        multi.close()


@pytest.mark.parametrize("ntlm_proxy", [{"negotiate": True}], indirect=True)
def test_body_not_rewindable_negotiate_retry_is_inconclusive(ntlm_proxy):
    # A body source that cannot seek cannot be resent for the NTLM retry: the
    # result is inconclusive and does not count as an auth failure
    multi = mcurl.MCurl()
    try:
        curl = mcurl.Curl(TARGET, method="POST")
        curl.buffer(BODY)
        curl.set_seek(lambda offset, origin: mcurl.libcurl.CURL_SEEKFUNC_CANTSEEK)
        curl.set_headers({"Content-Length": str(len(BODY))})
        assert curl.set_proxy(ntlm_proxy.url)
        curl.set_auth(USER, "pass", auth="ANY")
        assert not multi.do(curl)
        assert curl.resp != 401
        assert multi.failed.get(ntlm_proxy.url, 0) == 0
    finally:
        multi.close()

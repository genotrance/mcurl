"""Concurrent multi-leg NTLM proxy authentication through one MCurl (px#268).

A fake upstream proxy behaves like a connection-based SSPI/NTLM proxy: every
new connection is challenged with 407 first, then the type1/type2/type3 legs
run on one connection and only that connection is authenticated afterwards.
Intermediate 407 legs, and legs on connections the proxy closes, must never
count towards MCurl.failed; only a handshake that ends in a final 407 does.
"""

import base64
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

    def __init__(self, close_first=False, negotiate=False):
        super().__init__(("127.0.0.1", 0), _NtlmHandler)
        # close_first: close the connection after the initial challenge, so
        # libcurl has to reconnect to send type1 (keep-alive otherwise)
        self.close_first = close_first
        self.negotiate = negotiate
        self.drop_type2 = 0  # Handshakes to interrupt by closing before type2
        self.lock = threading.Lock()
        self.challenges = 0  # 407 responses sent, intermediate or final
        self.authenticated = 0  # Successful type3 legs

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
        if length:
            self.rfile.read(length)
        return line.decode("latin-1").split()[0], headers

    def _send(self, code, headers=(), body=b""):
        reason = {200: "OK", 407: "Proxy Authentication Required"}[code]
        out = [f"HTTP/1.1 {code} {reason}", *headers]
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
            if drop:
                # Interrupt the handshake: close instead of sending type2
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
            self._send(200, [], b"ok")


@pytest.fixture
def ntlm_proxy(request):
    server = _NtlmProxy(**getattr(request, "param", {}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def _request(multi, url, connect, user=USER, auth="ANY"):
    # Returns (admitted, success, curl)
    curl = mcurl.Curl(CONNECT_TARGET, method="CONNECT") if connect else mcurl.Curl(TARGET)
    if not connect:
        curl.buffer()
    if not curl.set_proxy(url):
        return False, False, curl
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


def _auth(proxy):
    # Negotiate needs a KDC or SSPI that the test cannot provide, so pick NTLM
    # explicitly when the proxy offers both, as SSPI would fall back to it
    return "NTLM" if proxy.negotiate else "ANY"


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
            results = _burst(multi, ntlm_proxy.url, connect, auth=_auth(ntlm_proxy))
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
        admitted, ok, curl = _request(multi, ntlm_proxy.url, connect, auth=_auth(ntlm_proxy))
        assert admitted and ok, (curl.cerr, curl.resp, curl.errstr)
        assert multi.failed[ntlm_proxy.url] == 0
    finally:
        multi.close()


@pytest.mark.parametrize("connect", [False, True], ids=["http", "connect"])
@pytest.mark.parametrize("ntlm_proxy", VARIANTS, indirect=True)
def test_wrong_credentials_block_after_threshold(ntlm_proxy, connect):
    multi = mcurl.MCurl()
    try:
        for i in range(multi.failure_threshold):
            admitted, ok, curl = _request(multi, ntlm_proxy.url, connect, user="wrong", auth=_auth(ntlm_proxy))
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

"""Streamed bridge() uploads through an authenticating proxy (curl error 65).

px hands Curl.bridge() a body source that cannot seek. With the proxy auth
method not yet known (ANY), libcurl used to send the body on the first,
unauthenticated leg, get a 407 and fail with CURLE_SEND_FAIL_REWIND. mcurl now
sends Expect: 100-continue for such uploads so the probe leg carries no body,
streams chunked bodies until EOF and never reads the source twice.

The fake proxy here honours Expect like Squid: an unauthenticated request that
waits for 100 Continue gets its 407 straight away, with the connection closed
since the body was never read. NTLM legs run on one connection.
"""

import base64
import hashlib
import io
import select
import socket
import socketserver
import struct
import sys
import threading
import time
import tracemalloc

import pytest

import mcurl

TARGET = "http://stream-upload.invalid/upload"
USER = "user"
HAS_NTLM = bool(mcurl.get_curl_vinfo().features & mcurl.libcurl.CURL_VERSION_NTLM)

pytestmark = pytest.mark.skipif(not HAS_NTLM, reason="libcurl was built without NTLM support")

PATTERN = bytes(range(256)) * 64  # 16 KiB


def _type2():
    flags = 0x00000001 | 0x00000200
    return (
        b"NTLMSSP\x00" + struct.pack("<IHHII", 2, 0, 0, 40, flags) + b"\x01\x02\x03\x04\x05\x06\x07\x08" + b"\x00" * 8
    )


def _type3_user(msg):
    length, _, offset = struct.unpack("<HHI", msg[36:44])
    return msg[offset : offset + length].decode("utf-16-le", "replace")


class StreamSource:
    """Non-seekable body source: generated on the fly, never held in memory.

    Every read() returns at most max_read bytes (short reads) and is recorded,
    so tests can check the body was read exactly once, in order.
    """

    def __init__(self, length, max_read=7000):
        self.length = length
        self.max_read = max_read
        self.pos = 0
        self.reads = []  # (offset, length) of every non-empty read
        self.sha = hashlib.sha256()

    def read(self, size=-1):
        if size < 0:
            size = self.length
        size = min(size, self.max_read, self.length - self.pos)
        start = self.pos % len(PATTERN)
        data = (PATTERN[start:] + PATTERN[:start]) * (size // len(PATTERN) + 1)
        data = data[:size]
        if data:
            self.reads.append((self.pos, len(data)))
            self.sha.update(data)
        self.pos += len(data)
        return data

    def seekable(self):
        return False

    def assert_read_once(self):
        offset = 0
        for start, length in self.reads:
            assert start == offset, "source read out of order or twice"
            offset += length
        assert offset == self.length


class PausingStreamSource:
    """Non-seekable body source that pauses (read() returns None) instead of blocking.

    release(n) makes the next n bytes (cumulatively, from the same PATTERN
    generator as StreamSource) available; until then read_callback gets None
    and returns CURL_READFUNC_PAUSE. The caller resumes with
    curl.resume_upload() from any thread, mirroring px's streaming RequestBody
    whose read() never blocks the mcurl thread.
    """

    def __init__(self, length):
        self.length = length
        self.pos = 0
        self.available = 0
        self.pause_count = 0
        self.reads = []
        self.sha = hashlib.sha256()
        self._lock = threading.Lock()
        # Set on the first pause so a driver thread can block efficiently
        # instead of spin-polling, which matters with dozens running at once
        self.paused = threading.Event()

    def release(self, nbytes):
        with self._lock:
            self.available = min(self.length, self.available + nbytes)

    def read(self, size=-1):
        if size < 0:
            size = self.length - self.pos
        with self._lock:
            avail = self.available - self.pos
        if avail <= 0:
            if self.pos >= self.length:
                return b""
            self.pause_count += 1
            self.paused.set()
            return None
        size = min(size, avail)
        start = self.pos % len(PATTERN)
        data = (PATTERN[start:] + PATTERN[:start]) * (size // len(PATTERN) + 1)
        data = data[:size]
        self.reads.append((self.pos, len(data)))
        self.sha.update(data)
        self.pos += len(data)
        return data

    def seekable(self):
        return False


def _drive(curl, source, step=4000, delay=0.01):
    "Trickle-feed a PausingStreamSource and wake mcurl, as a slow client would"
    # Wait for the first real pause so the handshake/probe legs (which need no
    # body) don't let the driver race ahead and release everything up front.
    # A blocking wait (not polling) matters once dozens of these run at once.
    source.paused.wait(30)
    while source.available < source.length:
        time.sleep(delay)
        source.release(step)
        curl.resume_upload()
    curl.resume_upload()


class _Proxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, negotiate=False, ignore_expect=False):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.negotiate = negotiate
        self.ignore_expect = ignore_expect
        self.lock = threading.Lock()
        self.legs = []  # (kind, method, headers, body bytes received) per 407 leg
        self.bodies = []  # (method, length, sha256, headers) after authentication

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(socketserver.StreamRequestHandler):
    rbufsize = 0  # Unbuffered so _pending_bytes() sees every byte sent
    timeout = 5  # A short body must not hang the test

    def _read_head(self):
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
        return line.decode("latin-1").split()[0], headers

    def _read_body(self, headers):
        # Stream the body into a hash; returns (length, sha256 hex)
        sha = hashlib.sha256()
        total = 0
        if "chunked" in headers.get("transfer-encoding", "").lower():
            while True:
                size = int(self.rfile.readline().split(b";")[0], 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    break
                left = size
                while left:
                    data = self.rfile.read(min(left, 65536))
                    sha.update(data)
                    left -= len(data)
                total += size
                self.rfile.readline()
        else:
            left = int(headers.get("content-length", 0))
            while left:
                data = self.rfile.read(min(left, 65536))
                if not data:
                    break
                sha.update(data)
                total += len(data)
                left -= len(data)
        return total, sha.hexdigest()

    def _pending_bytes(self):
        # Body bytes the client sent without waiting for 100 Continue; rfile
        # is unbuffered, so anything sent is still on the socket
        if not select.select([self.connection], [], [], 0.2)[0]:
            return 0
        return len(self.connection.recv(65536, socket.MSG_PEEK))

    def _send(self, code, headers=(), body=b""):
        reason = {200: "OK", 407: "Proxy Authentication Required"}[code]
        out = [f"HTTP/1.1 {code} {reason}", *headers, f"Content-Length: {len(body)}"]
        self.wfile.write(("\r\n".join(out) + "\r\n\r\n").encode() + body)
        self.wfile.flush()

    def _record(self, kind, method, headers, received):
        with self.server.lock:
            self.server.legs.append((kind, method, headers, received))

    def handle(self):
        server = self.server
        saw_type1 = False
        authenticated = False  # NTLM authenticates the connection
        while True:
            method, headers = self._read_head()
            if method is None:
                return
            expect = headers.get("expect", "").lower() == "100-continue"
            scheme, _, token = headers.get("proxy-authorization", "").partition(" ")
            msg = base64.b64decode(token) if scheme.upper() == "NTLM" and token else b""
            kind = struct.unpack("<I", msg[8:12])[0] if len(msg) >= 12 else 0
            if authenticated or (kind == 3 and saw_type1 and _type3_user(msg) == USER):
                authenticated = True
                if expect:
                    self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                    self.wfile.flush()
                length, digest = self._read_body(headers)
                with server.lock:
                    server.bodies.append((method, length, digest, headers))
                self._send(200, [], b"ok")
                continue
            if kind == 1:
                length, _ = self._read_body(headers)
                self._record("type1", method, headers, length)
                saw_type1 = True
                self._send(407, ["Proxy-Authenticate: NTLM " + base64.b64encode(_type2()).decode()])
                continue
            # Unauthenticated probe (or a failed type3)
            offers = ["Proxy-Authenticate: NTLM"]
            if server.negotiate:
                offers.insert(0, "Proxy-Authenticate: Negotiate")
            if expect and not server.ignore_expect:
                # Squid style: answer before the body, then close since the
                # unread body would otherwise be parsed as the next request
                self._record("probe", method, headers, self._pending_bytes())
                self._send(407, [*offers, "Connection: close"])
                return
            length, _ = self._read_body(headers)
            self._record("probe", method, headers, length)
            self._send(407, offers)


@pytest.fixture
def proxy(request):
    server = _Proxy(**getattr(request, "param", {}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def multi():
    m = mcurl.MCurl()
    yield m
    m.close()


def _upload(multi, proxy, method, source, length=None, auth="ANY", easy=False):
    # Same call order as px: proxy, auth, bridge, headers
    curl = mcurl.Curl(TARGET, method=method)
    curl.is_easy = easy
    assert curl.set_proxy(proxy.url)
    curl.set_auth(USER, "pass", auth=auth)
    curl.bridge(source, io.BytesIO(), io.BytesIO())
    headers = {"Content-Type": "application/octet-stream"}
    if length is None:
        headers["Transfer-Encoding"] = "chunked"
    else:
        headers["Content-Length"] = str(length)
    curl.set_headers(headers)
    return multi.do(curl), curl


def _expected(length):
    src = StreamSource(length, max_read=1 << 20)
    while src.read(1 << 20):
        pass
    return src.sha.hexdigest()


PROXIES = [pytest.param({}, id="ntlm")]
if sys.platform != "win32":
    # ANY picks Negotiate first; GSS-API fails locally without a ticket and
    # do() retries with NTLM, which needs the body to be still unread
    PROXIES.append(pytest.param({"negotiate": True}, id="negotiate"))


@pytest.mark.parametrize("easy", [False, True], ids=["multi", "easy"])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
@pytest.mark.parametrize("proxy", PROXIES, indirect=True)
def test_unknown_auth_streamed_body(proxy, multi, method, easy):
    source = StreamSource(20000)
    ok, curl = _upload(multi, proxy, method, source, 20000, easy=easy)
    assert ok and curl.cerr == 0 and curl.get_response()[1] == 200, (curl.cerr, curl.resp, curl.errstr)
    assert [(m, n, d) for m, n, d, _ in proxy.bodies] == [(method, 20000, _expected(20000))]
    source.assert_read_once()
    probes = [leg for leg in proxy.legs if leg[0] == "probe"]
    assert probes and all(received == 0 for *_, received in probes)
    assert probes[0][2].get("expect", "").lower() == "100-continue"
    assert multi.failed.get(proxy.url, 0) == 0
    # The picked method is cached, so the next upload starts with NTLM type1
    assert multi.proxyauth[proxy.url] == mcurl.libcurl.CURLAUTH_NTLM


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
@pytest.mark.parametrize("length", [0, 1, 5 * 1024 * 1024 + 3], ids=["empty", "small", "multi-mb"])
def test_known_ntlm_uses_cl0_type1(proxy, multi, method, length):
    source = StreamSource(length, max_read=60000)
    ok, curl = _upload(multi, proxy, method, source, length, auth="NTLM")
    assert ok and curl.get_response()[1] == 200, (curl.cerr, curl.resp, curl.errstr)
    assert [(m, n, d) for m, n, d, _ in proxy.bodies] == [(method, length, _expected(length))]
    source.assert_read_once()
    type1 = [leg for leg in proxy.legs if leg[0] == "type1"]
    assert type1 and all(leg[3] == 0 for leg in type1)
    if length:
        # libcurl sends a Content-Length: 0 type1 itself; no Expect round trip
        assert type1[0][2].get("content-length") == "0"
        assert "expect" not in proxy.bodies[0][3]
    assert not [leg for leg in proxy.legs if leg[0] == "probe"]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
@pytest.mark.parametrize("length", [1, 3 * 1024 * 1024 + 1], ids=["small", "multi-mb"])
def test_chunked_unknown_length_streamed(proxy, multi, method, length):
    # devel used to return 0 from the read callback and send an empty body
    source = StreamSource(length, max_read=60000)
    ok, curl = _upload(multi, proxy, method, source, None)
    assert ok and curl.get_response()[1] == 200, (curl.cerr, curl.resp, curl.errstr)
    assert [(m, n, d) for m, n, d, _ in proxy.bodies] == [(method, length, _expected(length))]
    assert "chunked" in proxy.bodies[0][3].get("transfer-encoding", "")
    source.assert_read_once()
    assert all(received == 0 for kind, _, _, received in proxy.legs if kind == "probe")


def test_second_upload_starts_with_cached_ntlm(proxy, multi):
    ok, _ = _upload(multi, proxy, "POST", StreamSource(100), 100)
    assert ok
    del proxy.legs[:]
    ok, curl = _upload(multi, proxy, "POST", StreamSource(100), 100)
    assert ok, (curl.cerr, curl.resp, curl.errstr)
    # Reuses the authenticated connection, or starts with type1 on a new one
    assert [leg[0] for leg in proxy.legs] in ([], ["type1"])


def test_large_streamed_body_bounded_memory(proxy, multi):
    length = 8 * 1024 * 1024
    source = StreamSource(length, max_read=60000)
    tracemalloc.start()
    try:
        ok, curl = _upload(multi, proxy, "PUT", source, length)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert ok, (curl.cerr, curl.resp, curl.errstr)
    source.assert_read_once()
    assert proxy.bodies[0][1] == length
    assert peak < 4 * 1024 * 1024, peak


@pytest.mark.parametrize("proxy", [{"ignore_expect": True}], indirect=True)
def test_proxy_ignoring_expect_gives_clear_error(proxy, multi):
    # The proxy reads the body libcurl sends after its Expect timeout and only
    # then answers 407: the source cannot rewind, so the request fails cleanly
    source = StreamSource(5000)
    ok, curl = _upload(multi, proxy, "POST", source, 5000)
    assert not ok
    assert curl.cerr == mcurl.libcurl.CURLE_SEND_FAIL_REWIND
    assert curl.resp == 503 and curl.errstr.startswith(mcurl.REWIND_ERROR)
    assert multi.failed.get(proxy.url, 0) == 0
    assert proxy.bodies == []
    source.assert_read_once()
    # The method learned on the probe leg is cached for the next request
    assert mcurl.MCURL.proxyauth.get(proxy.url) == mcurl.libcurl.CURLAUTH_NTLM
    ok, curl = _upload(multi, proxy, "POST", StreamSource(5000), 5000)
    assert ok, (curl.cerr, curl.resp, curl.errstr)


def test_unknown_auth_streamed_body_pausing_source(proxy, multi):
    # Same Expect: 100-continue probe path as test_unknown_auth_streamed_body,
    # but the source pauses (CURL_READFUNC_PAUSE) instead of being ready
    # immediately, and is fed and resumed from a second thread
    length = 20000
    source = PausingStreamSource(length)
    curl = mcurl.Curl(TARGET, method="POST")
    assert curl.set_proxy(proxy.url)
    curl.set_auth(USER, "pass", auth="ANY")
    curl.bridge(source, io.BytesIO(), io.BytesIO())
    curl.set_headers({"Content-Type": "application/octet-stream", "Content-Length": str(length)})
    driver = threading.Thread(target=_drive, args=(curl, source), kwargs={"step": 4000, "delay": 0.01})
    driver.start()
    ok = multi.do(curl)
    driver.join(5)
    assert ok and curl.cerr == 0 and curl.get_response()[1] == 200, (curl.cerr, curl.resp, curl.errstr)
    assert source.pause_count >= 1
    assert [(m, n, d) for m, n, d, _ in proxy.bodies] == [("POST", length, _expected(length))]
    probes = [leg for leg in proxy.legs if leg[0] == "probe"]
    assert probes and all(received == 0 for *_, received in probes)
    assert probes[0][2].get("expect", "").lower() == "100-continue"


@pytest.mark.slow
def test_concurrent_paused_uploads_deliver_exact_bodies(proxy, multi):
    # One stalled read must not serialize the others: 60 uploads, each paused
    # and fed from its own thread, must all complete through the same proxy
    n = 60
    length = 20000
    expected_sha = _expected(length)
    results = [None] * n

    def one(i):
        source = PausingStreamSource(length)
        curl = mcurl.Curl(TARGET, method="POST")
        assert curl.set_proxy(proxy.url)
        curl.set_auth(USER, "pass", auth="ANY")
        curl.bridge(source, io.BytesIO(), io.BytesIO())
        curl.set_headers({"Content-Type": "application/octet-stream", "Content-Length": str(length)})
        driver = threading.Thread(target=_drive, args=(curl, source), kwargs={"step": 2000, "delay": 0.005})
        driver.start()
        ok = multi.do(curl)
        driver.join(20)
        results[i] = (ok, curl, source)

    workers = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(45)

    for ok, curl, source in results:
        assert ok and curl.cerr == 0 and curl.get_response()[1] == 200, (curl.cerr, curl.resp, curl.errstr)
        assert source.pause_count >= 1
    bodies = sorted((bn, d) for _, bn, d, _ in proxy.bodies)
    assert bodies == sorted([(length, expected_sha)] * n)


def test_rewind_only_when_nothing_read():
    curl = mcurl.Curl(TARGET, method="POST")
    source = StreamSource(10)
    curl.bridge(source)
    assert curl._seek(0, 0) == mcurl.libcurl.CURL_SEEKFUNC_OK
    curl._body_read = 1
    assert curl._seek(0, 0) == mcurl.libcurl.CURL_SEEKFUNC_CANTSEEK
    seekable = mcurl.Curl(TARGET, method="POST")
    seekable.bridge(io.BytesIO(b"abc"))
    seekable._body_read = 3
    assert seekable._seek(0, 0) == mcurl.libcurl.CURL_SEEKFUNC_OK

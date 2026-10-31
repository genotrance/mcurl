"""Non-blocking bridge() sources: CURL_READFUNC_PAUSE / CURL_WRITEFUNC_PAUSE.

read_callback and write_callback used to run client_rfile.read() and
client_wfile.write() inside _socket_action under MCurl._lock. A source that
has no data yet (or a downstream that cannot accept more) had to block the
calling thread there, freezing every other transfer on the same MCurl. Now
read() may return None (or raise BlockingIOError) to mean "no data yet"
without blocking; mcurl returns CURL_READFUNC_PAUSE and the transfer is
resumed from any thread with Curl.resume_upload() / MCurl.resume(curl). EOF is
still signalled with b"". write() has the symmetric contract for a downstream
that would block, paired with CURL_WRITEFUNC_PAUSE and resume_download().
"""

import io
import socket
import socketserver
import threading
import time

import pytest

import mcurl

PATTERN = bytes(range(256)) * 16  # 4 KiB, cycled by absolute offset


def _chunk(pos, size):
    # Same offset-based generator as StreamSource in test_stream_upload.py:
    # the result only depends on absolute position, not on how reads are
    # chunked, so sources that release data at different rates still agree.
    start = pos % len(PATTERN)
    data = (PATTERN[start:] + PATTERN[:start]) * (size // len(PATTERN) + 1)
    return data[:size]


class PausingSource:
    """Non-seekable body source that pauses instead of blocking.

    release(n) makes the next n bytes (cumulatively) available to read();
    until then, read() returns None - the non-blocking "no data yet" contract
    - which read_callback turns into CURL_READFUNC_PAUSE. The caller is
    responsible for calling curl.resume_upload() after release(), exactly as
    a real source and its I/O-ready notification are independent.
    """

    def __init__(self, length):
        self.length = length
        self.pos = 0
        self.available = 0
        self.pause_count = 0
        self._lock = threading.Lock()

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
            return None
        size = min(size, avail)
        data = _chunk(self.pos, size)
        self.pos += len(data)
        return data

    def seekable(self):
        return False


class PausingSink:
    """Non-blocking response sink: write() pauses instead of blocking.

    Accepts nothing until allow() is called from another thread; mirrors a
    slow downstream client reading the response too slowly to keep up.
    """

    def __init__(self):
        self.data = bytearray()
        self.pause_count = 0
        self._allowed = threading.Event()

    def allow(self):
        self._allowed.set()

    def write(self, buf):
        if not self._allowed.is_set():
            self.pause_count += 1
            return None
        self.data += bytes(buf)
        return len(buf)


class PartialSink:
    """Non-blocking sink accepting at most `limit` bytes per write() call.

    Once `limit_calls` calls were served the sink returns None (not ready)
    until allow() is called, like a socket whose send buffer is full.
    """

    def __init__(self, limit=1000, ready_calls=3):
        self.data = bytearray()
        self.limit = limit
        self.ready_calls = ready_calls
        self.calls = 0
        self.partial = 0
        self._allowed = threading.Event()

    def allow(self):
        self._allowed.set()

    def write(self, buf):
        if not self._allowed.is_set():
            self.calls += 1
            if self.calls > self.ready_calls:
                return None
        n = min(len(buf), self.limit)
        if n < len(buf):
            self.partial += 1
        self.data += bytes(buf[:n])
        return n


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@pytest.fixture
def local_server():
    servers = []

    def start(handler):
        server = _Server(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _url(address, path="/"):
    return f"http://{address[0]}:{address[1]}{path}"


class _EchoHandler(socketserver.BaseRequestHandler):
    "Reads one HTTP/1.1 request (any method) fully and answers 200 ok"

    def handle(self):
        buf = b""
        while b"\r\n\r\n" not in buf:
            data = self.request.recv(4096)
            if not data:
                return
            buf += data
        head, _, rest = buf.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n")[1:]:
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value.strip())
        left = length - len(rest)
        while left > 0:
            data = self.request.recv(min(left, 65536))
            if not data:
                break
            left -= len(data)
        self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")


@pytest.fixture
def multi():
    m = mcurl.MCurl()
    yield m
    m.close()


def test_resume_wakes_blocked_select():
    "A resume from another thread cuts a blocked select() short instead of waiting out its timeout"
    multi = mcurl.MCurl()
    multi.timer = None
    reader, writer = socket.socketpair()
    try:
        multi.rlist = {reader.fileno()}
        elapsed = []

        def run():
            start = time.monotonic()
            multi._perform()
            elapsed.append(time.monotonic() - start)

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.1)
        fake_curl = type("FakeCurl", (), {"easyhash": "does-not-exist"})()
        multi.resume(fake_curl)
        worker.join(2)
        assert elapsed, "background _perform() never returned"
        # Without the wake pipe this would wait out MULTI_SELECT_TIMEOUT (1.0s)
        assert elapsed[0] < 0.5, elapsed
    finally:
        reader.close()
        writer.close()
        multi.close()


@pytest.mark.slow
def test_stalled_uploader_does_not_delay_concurrent_transfers(local_server, multi):
    upload_addr = local_server(_EchoHandler)
    get_addr = local_server(_EchoHandler)

    length = 4000
    source = PausingSource(length)
    curl = mcurl.Curl(_url(upload_addr), method="POST")
    curl.bridge(source, io.BytesIO(), None)
    curl.set_headers({"Content-Length": str(length)})

    result = {}

    def run_upload():
        result["ok"] = multi.do(curl)

    uploader = threading.Thread(target=run_upload)
    uploader.start()
    try:
        deadline = time.monotonic() + 5
        while source.pause_count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert source.pause_count >= 1, "uploader never paused"

        # Genuinely stalled for several seconds - no data released at all
        time.sleep(3)

        get_curl = mcurl.Curl(_url(get_addr))
        get_curl.buffer()
        start = time.monotonic()
        ok = multi.do(get_curl)
        elapsed = time.monotonic() - start
        assert ok and get_curl.get_response()[1] == 200
        assert elapsed < 1.0, f"GET took {elapsed}s behind a stalled upload on the same MCurl"

        other_source = PausingSource(0)
        other_source.release(0)
        other_curl = mcurl.Curl(_url(upload_addr), method="POST")
        other_curl.bridge(other_source, io.BytesIO(), None)
        other_curl.set_headers({"Content-Length": "0"})
        start = time.monotonic()
        ok = multi.do(other_curl)
        elapsed = time.monotonic() - start
        assert ok and other_curl.get_response()[1] == 200
        assert elapsed < 1.0, f"second upload took {elapsed}s behind a stalled upload on the same MCurl"
    finally:
        # Let the stalled upload finish so the thread and server connection close
        while source.available < source.length:
            source.release(1000)
            curl.resume_upload()
            time.sleep(0.02)
        curl.resume_upload()
        uploader.join(5)
    assert result.get("ok"), (curl.cerr, curl.resp, curl.errstr)


def test_stop_removes_paused_handle_without_hanging(local_server, multi):
    addr = local_server(_EchoHandler)
    source = PausingSource(4000)
    curl = mcurl.Curl(_url(addr), method="POST")
    curl.bridge(source, io.BytesIO(), None)
    curl.set_headers({"Content-Length": "4000"})

    worker = threading.Thread(target=multi.do, args=(curl,))
    worker.start()
    deadline = time.monotonic() + 5
    while source.pause_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert source.pause_count >= 1

    start = time.monotonic()
    multi.stop(curl)
    worker.join(5)
    elapsed = time.monotonic() - start
    assert not worker.is_alive(), "do() did not return after stop() on a paused handle"
    assert elapsed < 2.0, elapsed
    assert curl._stopped


def test_timeout_aborts_paused_upload_with_clear_error(local_server, multi):
    addr = local_server(_EchoHandler)
    source = PausingSource(4000)  # never released: read() always returns None
    curl = mcurl.Curl(_url(addr), method="POST")
    curl.set_timeout(1)
    curl.bridge(source, io.BytesIO(), None)
    curl.set_headers({"Content-Length": "4000"})

    start = time.monotonic()
    ok = multi.do(curl)
    elapsed = time.monotonic() - start
    assert not ok
    assert curl.cerr == mcurl.libcurl.CURLE_OPERATION_TIMEDOUT
    assert curl.resp == 504
    assert elapsed < 5.0, elapsed


def test_paused_write_resumed_from_other_thread(local_server, multi):
    "A slow client_wfile pauses the download (CURL_WRITEFUNC_PAUSE) and resume_download() continues it"
    body = b"x" * 20000

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            self.request.sendall(f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)

    addr = local_server(Handler)
    sink = PausingSink()
    curl = mcurl.Curl(_url(addr))
    curl.bridge(None, sink, None)

    result = {}

    def run():
        result["ok"] = multi.do(curl)

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.2)
    assert sink.pause_count >= 1, "download never paused"
    assert bytes(sink.data) != body

    sink.allow()
    curl.resume_download()
    worker.join(5)
    assert not worker.is_alive()
    assert result.get("ok"), (curl.cerr, curl.resp, curl.errstr)
    assert bytes(sink.data) == body


@pytest.mark.parametrize("ready_calls", [1000000, 3])
def test_partial_writes_keep_all_bytes_in_order(local_server, multi, ready_calls):
    "A sink that accepts only part of each buffer loses nothing, in order, even when it later blocks"
    body = _chunk(0, 200000)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            self.request.sendall(f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)

    addr = local_server(Handler)
    sink = PartialSink(limit=777, ready_calls=ready_calls)
    curl = mcurl.Curl(_url(addr))
    curl.bridge(None, sink, None)

    result = {}
    worker = threading.Thread(target=lambda: result.update(ok=multi.do(curl)))
    worker.start()
    if ready_calls < 1000000:
        time.sleep(0.3)
        assert bytes(sink.data) != body
        sink.allow()
        curl.resume_download()
    worker.join(10)
    assert not worker.is_alive()
    assert result.get("ok"), (curl.cerr, curl.resp, curl.errstr)
    assert sink.partial > 0
    assert bytes(sink.data) == body


def test_partial_write_pending_at_completion_is_flushed(local_server, multi):
    "A transfer finishing while a suffix is pending still delivers the final bytes after resume"
    body = b"y" * 5000

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            self.request.sendall(f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)

    addr = local_server(Handler)
    sink = PartialSink(limit=100, ready_calls=1)
    curl = mcurl.Curl(_url(addr))
    curl.bridge(None, sink, None)

    result = {}
    worker = threading.Thread(target=lambda: result.update(ok=multi.do(curl)))
    worker.start()
    time.sleep(0.3)
    assert worker.is_alive()
    sink.allow()
    curl.resume_download()
    worker.join(10)
    assert not worker.is_alive()
    assert result.get("ok"), (curl.cerr, curl.resp, curl.errstr)
    assert bytes(sink.data) == body

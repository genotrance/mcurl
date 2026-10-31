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


class CompletionFailSink:
    "Sink that first accepts a partial chunk, then fails while flushing its suffix."

    def __init__(self):
        self.data = bytearray()
        self.partial = False
        self._fail = threading.Event()

    def fail(self):
        self._fail.set()

    def write(self, buf):
        if not self.partial:
            self.partial = True
            n = min(len(buf), 100)
            self.data += bytes(buf[:n])
            return n
        if self._fail.is_set():
            raise ConnectionError
        return None


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
        fake_curl = type("FakeCurl", (), {"easyhash": "does-not-exist", "_transfer_id": 0, "_active": True})()
        multi.resume(fake_curl)
        worker.join(2)
        assert elapsed, "background _perform() never returned"
        # Without the wake pipe this would wait out MULTI_SELECT_TIMEOUT (1.0s)
        assert elapsed[0] < 0.5, elapsed
    finally:
        reader.close()
        writer.close()
        multi.close()


def test_select_error_flushes_pending_completion(multi, monkeypatch):
    """A stale Windows socket must not strand a partial sink's final suffix."""

    class Sink:
        def __init__(self):
            self.data = bytearray()

        def write(self, buf):
            self.data += buf
            return len(buf)

    curl = mcurl.Curl("http://127.0.0.1:1/")
    sink = Sink()
    curl.client_wfile = sink
    curl._wpending = b"final suffix"
    curl._wdone = mcurl.libcurl.CURLE_OK
    multi.handles[curl.easyhash] = curl
    multi.rlist.add(123)

    def select_error(*args):
        raise OSError(10038, "not a socket")

    monkeypatch.setattr(mcurl.select, "select", select_error)
    multi._perform()
    assert bytes(sink.data) == b"final suffix"
    assert not curl._wpending
    assert curl.done


def test_flush_unpause_runs_timeout_without_writable_socket(multi, monkeypatch):
    """A Windows-style pause can remove writable interest while a suffix waits.

    The selected read event flushes the suffix, but does not itself prove that
    libcurl has a writable socket to drive after the receive unpause. The
    timeout action must run in the same turn so cached response data continues.
    """

    class Sink:
        def write(self, buf):
            return len(buf)

    curl = mcurl.Curl("http://127.0.0.1:1/")
    curl.client_wfile = Sink()
    curl._wpending = b"suffix"
    curl._pause_mask = mcurl.libcurl.CURLPAUSE_RECV
    multi.handles[curl.easyhash] = curl
    multi.rlist = {123}
    actions = []

    def windows_paused_select(readers, writers, errors, timeout):
        # Windows does not retain Linux's always-writable interest while RECV
        # is paused. A stale read notification is all this turn can report.
        assert readers[0] == 123
        assert writers == []
        assert curl._pause_mask & mcurl.libcurl.CURLPAUSE_RECV
        return [123], [], []

    monkeypatch.setattr(mcurl.select, "select", windows_paused_select)
    monkeypatch.setattr(multi, "_socket_action", lambda sock, events: actions.append((sock, events)))
    multi._perform()

    assert not curl._wpending
    assert curl._pause_mask == 0
    assert actions == [
        (123, mcurl.libcurl.CURL_CSELECT_IN),
        (mcurl.libcurl.CURL_SOCKET_TIMEOUT, 0),
    ]


def test_timer_callback_stores_deadline(multi, monkeypatch):
    now = [100.0]
    waits = []
    monkeypatch.setattr(mcurl.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(mcurl.select, "select", lambda *args: waits.append(args[-1]) or ([], [], []))
    multi.rlist = {123}

    mcurl.multi_timer_callback(mcurl.ffi.NULL, 250, mcurl.ffi.NULL)
    now[0] = 100.2
    multi._perform()

    assert waits == [pytest.approx(0.05)]


def test_select_value_error_keeps_registered_socket(multi, monkeypatch):
    """Linux fd >= FD_SETSIZE is not evidence that a socket was closed."""
    multi.rlist = {123}
    actions = []
    monkeypatch.setattr(mcurl.select, "select", lambda *args: (_ for _ in ()).throw(ValueError("fd out of range")))
    monkeypatch.setattr(multi, "_socket_action", lambda sock, events: actions.append((sock, events)))

    multi._perform()

    assert multi.rlist == {123}
    assert actions == [(mcurl.libcurl.CURL_SOCKET_TIMEOUT, 0)]


@pytest.mark.parametrize("ready_calls", [1000000, 3])
def test_stale_socket_cannot_strand_partial_write_transfer(local_server, multi, monkeypatch, ready_calls):
    "A dead descriptor left in the socket lists must be dropped, not make every later select() fail"
    body = _chunk(0, 200000)
    stale = 54321
    failures = []
    real_select = mcurl.select.select

    def select_with_stale(rlist, wlist, xlist, *args):
        # WSAENOTSOCK (Windows) for as long as the dead descriptor is listed
        if stale in [*rlist, *wlist, *xlist]:
            failures.append(stale)
            raise OSError(10038, "not a socket")
        return real_select(rlist, wlist, xlist, *args)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            self.request.sendall(f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)

    addr = local_server(Handler)
    sink = PartialSink(limit=777, ready_calls=ready_calls)
    curl = mcurl.Curl(_url(addr))
    curl.bridge(None, sink, None)
    monkeypatch.setattr(mcurl.select, "select", select_with_stale)
    multi.rlist.add(stale)
    multi.wlist.add(stale)

    result = {}
    worker = threading.Thread(target=lambda: result.update(ok=multi.do(curl)), daemon=True)
    worker.start()
    if ready_calls < 1000000:
        time.sleep(0.3)
        assert bytes(sink.data) != body
        sink.allow()
        curl.resume_download()
    worker.join(10)
    assert not worker.is_alive(), f"transfer stranded after {len(failures)} select failures"
    assert result.get("ok"), (curl.cerr, curl.resp, curl.errstr)
    assert sink.partial > 0
    assert bytes(sink.data) == body
    assert stale not in multi.rlist
    assert stale not in multi.wlist
    assert 1 <= len(failures) <= 3


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


def test_partial_write_flush_failure_at_completion_is_write_error(local_server, multi):
    "A final pending suffix that cannot reach its sink must fail the transfer."
    body = b"z" * 5000

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            self.request.sendall(f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)

    addr = local_server(Handler)
    sink = CompletionFailSink()
    curl = mcurl.Curl(_url(addr))
    curl.bridge(None, sink, None)

    result = {}
    worker = threading.Thread(target=lambda: result.update(ok=multi.do(curl)))
    worker.start()
    time.sleep(0.3)
    assert worker.is_alive()
    assert sink.partial

    sink.fail()
    curl.resume_download()
    worker.join(10)
    assert not worker.is_alive()
    assert result["ok"] is False
    assert curl.cerr == mcurl.libcurl.CURLE_WRITE_ERROR
    assert curl._wfail
    assert not curl._wpending


SEND = mcurl.libcurl.CURLPAUSE_SEND
RECV = mcurl.libcurl.CURLPAUSE_RECV


def _public_resume(curl, bit, transfer_id=None):
    if bit == SEND:
        return curl.resume_upload(transfer_id)
    return curl.resume_download(transfer_id)


class _LateCall(threading.Thread):
    """A foreign-thread call that is already in flight but only executes once go() is called.

    Stands in for a resume() whose thread was descheduled (GIL, OS, or a
    slow event loop) for as long as the test needs: everything the call
    captured (the Curl and any transfer_id) is bound before the delay.
    """

    def __init__(self, fn, *args):
        super().__init__(daemon=True)
        self._fn = fn
        self._args = args
        self._go = threading.Event()
        self.result = None
        self.error = None
        self.start()

    def run(self):
        self._go.wait()
        try:
            self.result = self._fn(*self._args)
        except Exception as exc:
            self.error = exc

    def go(self):
        self._go.set()
        self.join(2)
        assert not self.is_alive(), "late resume() call did not complete"
        return self


def _end_transfer(multi, curl, ending):
    if ending in ("remove", "remove+reset"):
        multi.remove(curl)
    else:
        multi.stop(curl)
    if ending != "remove":
        curl.reset("http://127.0.0.1:1/")


def _apply(multi):
    with multi._lock:
        return multi._apply_pending_resumes()


@pytest.mark.parametrize("bit", [SEND, RECV], ids=["send", "recv"])
@pytest.mark.parametrize("ending", ["remove", "remove+reset", "stop+reset"])
@pytest.mark.parametrize("next_paused", [False, True], ids=["before-next-pause", "after-next-pause"])
def test_late_resume_cannot_cross_into_next_transfer(multi, bit, ending, next_paused):
    """T40952da8: a resume meant for transfer 1 that only executes once the same Curl
    runs transfer 2 must not clear transfer 2's pause, whether it lands before or
    after transfer 2 pauses that direction for its own reason.
    """
    curl = mcurl.Curl("http://127.0.0.1:1/")
    multi.add(curl)
    first = curl.transfer_id
    late_with_id = _LateCall(_public_resume, curl, bit, first)
    late_without_id = _LateCall(_public_resume, curl, bit)

    _end_transfer(multi, curl, ending)
    multi.add(curl)
    assert curl.transfer_id != first
    if next_paused:
        curl._pause_mask = bit

    assert late_with_id.go().result is False
    assert isinstance(late_without_id.go().error, ValueError)
    assert multi._pending_resume == {}
    curl._pause_mask = bit
    assert not _apply(multi)
    assert curl._pause_mask == bit, "resume for the previous transfer cleared the next transfer's pause"

    # The next transfer's own resume, by its own transfer_id, still works
    assert _public_resume(curl, bit, curl.transfer_id) is True
    assert _apply(multi)
    assert curl._pause_mask == 0
    multi.remove(curl)


@pytest.mark.parametrize("bit", [SEND, RECV], ids=["send", "recv"])
def test_late_resume_between_transfers_is_discarded(multi, bit):
    "A late resume landing after remove() or reset() but before the next add() is refused, not kept for it"
    curl = mcurl.Curl("http://127.0.0.1:1/")
    multi.add(curl)
    first = curl.transfer_id
    multi.remove(curl)
    assert _public_resume(curl, bit, first) is False
    assert _public_resume(curl, bit) is False
    curl.reset("http://127.0.0.1:1/")
    assert _public_resume(curl, bit, first) is False
    assert _public_resume(curl, bit) is False
    # Even a request already carrying the next transfer's id is not kept
    # across add(): nothing can be paused before the transfer runs
    assert _public_resume(curl, bit, curl.transfer_id) is False
    assert multi._pending_resume == {}

    multi.add(curl)
    curl._pause_mask = bit
    assert not _apply(multi)
    assert curl._pause_mask == bit
    multi.remove(curl)


def test_queued_resume_does_not_leak_to_reused_handle(multi):
    "Resumes queued for a transfer are dropped when it ends and cannot clear a pause on a reused Curl."
    curl = mcurl.Curl("http://127.0.0.1:1/")
    for ending in ("remove", "remove+reset", "stop+reset"):
        multi.add(curl)
        first = curl.transfer_id
        if first == 0:
            # A fresh Curl's first transfer is unambiguous without an id
            assert multi.resume(curl, RECV) is True
        assert multi.resume(curl, RECV, first) is True
        assert multi._pending_resume == {curl.easyhash: (first, RECV)}
        _end_transfer(multi, curl, ending)
        assert multi._pending_resume == {}
        assert curl.transfer_id > first

        multi.add(curl)
        curl._pause_mask = RECV
        assert not _apply(multi)
        assert curl._pause_mask == RECV
        multi.remove(curl)


def test_retry_within_do_keeps_transfer_id(multi):
    "do() re-adding the same transfer for an auth retry keeps its transfer_id valid"
    curl = mcurl.Curl("http://127.0.0.1:1/")
    multi.add(curl)
    transfer_id = curl.transfer_id
    with multi._lock:
        multi._remove_handle(curl)
    # Between the legs nothing is paused, so the request is refused
    assert curl.resume_upload(transfer_id) is False
    multi.add(curl)
    assert curl.transfer_id == transfer_id
    curl._pause_mask = SEND
    assert curl.resume_upload(transfer_id) is True
    assert _apply(multi)
    assert curl._pause_mask == 0
    multi.remove(curl)
    assert curl.resume_upload(transfer_id) is False


@pytest.mark.parametrize("with_id", [False, True], ids=["without-id", "with-id"])
def test_concurrent_resume_cannot_leak_into_reused_handle(multi, monkeypatch, with_id):
    """A resume() racing _add_handle() right at the point Tc241996d found unprotected
    (after the stale queue entry is dropped, before the reused handle is republished as
    active) must not survive to clear a pause the new transfer set for its own reason.
    """
    curl = mcurl.Curl("http://127.0.0.1:1/")

    multi.add(curl)
    multi.remove(curl)
    curl.reset("http://127.0.0.1:1/")
    transfer_id = curl.transfer_id if with_id else None

    original_drop = mcurl.MCurl._drop_pending_resume
    racer = []

    def racing_drop(self, dropped_curl, *args):
        # Runs inside _add_handle, exactly where the stale entry is dropped
        # and before the handle is republished as active - inject the
        # foreign-thread resume() right here.
        original_drop(self, dropped_curl, *args)
        racer.append(_LateCall(self.resume, dropped_curl, RECV, transfer_id).go())

    monkeypatch.setattr(mcurl.MCurl, "_drop_pending_resume", racing_drop)
    multi.add(curl)
    monkeypatch.undo()

    assert len(racer) == 1 and racer[0].result is False and racer[0].error is None

    # The new transfer pauses receiving for its own, unrelated reason.
    curl._pause_mask = RECV
    _apply(multi)
    assert curl._pause_mask == RECV, "stale resume from the previous transfer leaked into the reused handle"
    multi.remove(curl)


class _SignallingSource(PausingSource):
    "PausingSource that also sets an Event each time read() pauses"

    def __init__(self, length):
        super().__init__(length)
        self.paused = threading.Event()

    def read(self, size=-1):
        data = super().read(size)
        if data is None:
            self.paused.set()
        return data


def test_reused_curl_streams_with_transfer_id(local_server, multi):
    "Two paused uploads on one reset() Curl, each resumed by its own transfer_id; the first's is refused on the second"
    addr = local_server(_EchoHandler)
    curl = mcurl.Curl(_url(addr), method="POST")
    ids = []
    for _ in range(2):
        source = _SignallingSource(4000)
        curl.bridge(source, io.BytesIO(), None)
        curl.set_headers({"Content-Length": "4000"})
        ids.append(curl.transfer_id)
        result: dict = {}
        worker = threading.Thread(target=lambda result=result: result.update(ok=multi.do(curl)))
        worker.start()
        assert source.paused.wait(5), "upload never paused"
        if len(ids) == 2:
            assert curl.resume_upload(ids[0]) is False
            with pytest.raises(ValueError):
                curl.resume_upload()
        source.release(4000)
        assert curl.resume_upload(ids[-1]) is True
        worker.join(5)
        assert not worker.is_alive()
        assert result.get("ok"), (curl.cerr, curl.resp, curl.errstr)
        multi.remove(curl)
        curl.reset(_url(addr), method="POST")
    assert ids[0] != ids[1]

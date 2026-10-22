"""Auth retry for proxies blocked after repeated 407s, against a local fake proxy."""

import http.server
import threading
import time

import pytest

import mcurl

TARGET = "http://auth-retry.invalid/"


class _FakeProxy(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _ProxyHandler)
        self.mode = "deny"  # deny: 407, allow: 200, drop: close without response
        self.delay = 0
        self.attempts = 0  # Requests carrying Proxy-Authorization
        self.lock = threading.Lock()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        server = self.server
        if self.headers.get("Proxy-Authorization") is not None:
            with server.lock:
                server.attempts += 1
            time.sleep(server.delay)
            if server.mode == "drop":
                self.close_connection = True
                return
            if server.mode == "allow":
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                return
        self.send_response(407)
        self.send_header("Proxy-Authenticate", 'Basic realm="fake"')
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()


@pytest.fixture
def proxy():
    server = _FakeProxy()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def multi():
    m = mcurl.MCurl()
    yield m
    m.close()


def _curl(proxy):
    # Returns a configured Curl, or None if set_proxy() refused the proxy
    curl = mcurl.Curl(TARGET)
    curl.buffer()
    if not curl.set_proxy(proxy.url):
        return None
    curl.set_auth("user", "pass", auth="BASIC")
    return curl


def _request(multi, proxy):
    # Returns the response code, or None if the proxy was refused
    curl = _curl(proxy)
    if curl is None:
        return None
    multi.do(curl)
    return curl.resp if curl.errstr else curl.get_response()[1]


def _block(multi, proxy):
    # Fail auth until the proxy is blocked
    proxy.mode = "deny"
    for _ in range(multi.failure_threshold):
        assert _request(multi, proxy) == 401
    assert multi.failed[proxy.url] == multi.failure_threshold
    assert _curl(proxy) is None


def _force_block(multi, proxy):
    # Block by setting the count directly; the interval starts at the first refusal
    multi.failed[proxy.url] = multi.failure_threshold
    assert _curl(proxy) is None
    time.sleep(multi.auth_retry + 0.05)


def test_default_latches_until_reset(proxy, multi):
    assert multi.auth_retry == 0
    _block(multi, proxy)
    assert proxy.attempts == 3

    # Recovery upstream does not help without a retry interval
    proxy.mode = "allow"
    time.sleep(0.3)
    assert _request(multi, proxy) is None
    assert proxy.attempts == 3

    multi.reset_proxy_failures()
    assert multi.failed == {}
    assert _request(multi, proxy) == 200
    assert multi.failed[proxy.url] == 0


def test_sequential_probe_recovers(proxy, multi):
    multi.set_auth_retry(0.5)
    _block(multi, proxy)
    assert _request(multi, proxy) is None

    time.sleep(0.6)
    probe = _curl(proxy)
    assert probe is not None
    # The probe holder may set the proxy again; others are refused until it completes
    assert probe.set_proxy(proxy.url)
    assert _curl(proxy) is None

    proxy.mode = "allow"
    assert multi.do(probe), probe.errstr
    assert probe.get_data() == "ok"
    assert multi.failed[proxy.url] == 0
    assert proxy.attempts == 4
    assert all(_request(multi, proxy) == 200 for _ in range(3))


def test_failed_probe_reblocks(proxy, multi):
    multi.set_auth_retry(0.3)
    _block(multi, proxy)

    time.sleep(0.4)
    assert _request(multi, proxy) == 401
    assert proxy.attempts == 4
    assert multi.failed[proxy.url] == 4
    # Blocked again for another interval
    assert _request(multi, proxy) is None
    time.sleep(0.4)
    proxy.mode = "allow"
    assert _request(multi, proxy) == 200
    assert proxy.attempts == 5


def test_failed_probe_backoff(proxy, multi):
    multi.set_auth_retry(0.3, max_interval=1.2)
    _block(multi, proxy)

    time.sleep(0.4)
    assert _request(multi, proxy) == 401
    # Interval doubled to 0.6s
    time.sleep(0.4)
    assert _request(multi, proxy) is None
    time.sleep(0.3)
    assert _request(multi, proxy) == 401
    # Doubled to 1.2s, the cap
    assert multi._blocked[proxy.url][1] == pytest.approx(1.2)
    multi.set_auth_retry(0.01, max_interval=0.02)
    multi._blocked[proxy.url] = (0, 0.02)
    assert _request(multi, proxy) == 401
    assert multi._blocked[proxy.url][1] == pytest.approx(0.02)
    assert proxy.attempts == 6


def test_inconclusive_probe_reblocks(proxy, multi):
    multi.set_auth_retry(0.3)
    _block(multi, proxy)

    time.sleep(0.4)
    proxy.mode = "drop"
    probe = _curl(proxy)
    assert probe is not None
    assert not multi.do(probe)
    assert probe.cerr != mcurl.libcurl.CURLE_OK
    # Count unchanged but blocked for another interval
    assert multi.failed[proxy.url] == 3
    assert _request(multi, proxy) is None
    time.sleep(0.4)
    proxy.mode = "allow"
    assert _request(multi, proxy) == 200


def _concurrent(func, count):
    # Call func from count threads released together; returns their results
    barrier = threading.Barrier(count)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        result = func()
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(results) == count
    return results


def _burst(multi, proxy, count=10):
    # Run count concurrent requests; returns (admitted responses, refused count)
    results = _concurrent(lambda: _request(multi, proxy), count)
    admitted = [resp for resp in results if resp is not None]
    return admitted, len(results) - len(admitted)


def test_concurrent_burst_sends_one_probe(proxy, multi):
    multi.set_auth_retry(0.3)
    _block(multi, proxy)
    time.sleep(0.4)

    # Probe still fails: exactly one auth attempt reaches the proxy
    proxy.delay = 1
    admitted, refused = _burst(multi, proxy)
    assert admitted == [401]
    assert refused == 9
    assert proxy.attempts == 4

    # Proxy recovered: one probe, then everyone gets through
    time.sleep(0.4)
    proxy.mode = "allow"
    admitted, refused = _burst(multi, proxy)
    assert admitted == [200]
    assert refused == 9
    assert proxy.attempts == 5

    proxy.delay = 0
    admitted, refused = _burst(multi, proxy)
    assert admitted == [200] * 10
    assert refused == 0
    assert proxy.attempts == 15


def test_probe_slot_thread_safety(proxy, multi):
    # Many threads racing set_proxy() on a retryable proxy: one winner per round
    multi.set_auth_retry(0.01)
    _force_block(multi, proxy)
    for _ in range(20):
        winners = [curl for curl in _concurrent(lambda: _curl(proxy), 16) if curl is not None]
        assert len(winners) == 1
        # Abandoning the probe before do() frees the slot without re-blocking
        winners[0].reset(TARGET)
        assert multi._probe == {}
    assert proxy.attempts == 0


def test_discarded_probe_frees_slot(proxy, multi):
    multi.set_auth_retry(0.01)
    _force_block(multi, proxy)
    probe = _curl(proxy)
    assert probe is not None
    assert _curl(proxy) is None
    del probe
    assert _curl(proxy) is not None


def test_reset_single_proxy(proxy, multi):
    multi.set_auth_retry(10)
    _block(multi, proxy)
    multi.failed["other"] = 5
    multi.reset_proxy_failures(proxy.url)
    assert proxy.url not in multi.failed
    assert multi.failed["other"] == 5
    assert _curl(proxy) is not None


def test_reset_clears_in_flight_probe(proxy, multi):
    multi.set_auth_retry(0.01)
    _force_block(multi, proxy)
    probe = _curl(proxy)
    assert probe is not None
    multi.reset_proxy_failures()
    proxy.mode = "allow"
    assert _request(multi, proxy) == 200
    # The stale probe completing does not disturb the reset state
    assert multi.do(probe)
    assert multi.failed[proxy.url] == 0
    assert multi._probe == {}


def test_auth_retry_options():
    m = mcurl.MCurl(auth_retry=5, auth_retry_max=60)
    assert (m.auth_retry, m.auth_retry_max) == (5, 60)
    m.set_auth_retry(2)
    assert (m.auth_retry, m.auth_retry_max) == (2, 2)
    with pytest.raises(ValueError):
        m.set_auth_retry(-1)
    with pytest.raises(ValueError):
        m.set_auth_retry(10, max_interval=5)
    m.close()

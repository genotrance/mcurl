"""Burst tests for auth_retry: one authentication probe per interval under concurrent load.

Each scenario function returns the observed counts so it can also be run
outside pytest (AUTH_RETRY_ROUNDS repeats every scenario that many times).
"""

import os
import socket
import threading
import time

import pytest

import mcurl

from .test_auth_retry import TARGET, _concurrent, _FakeProxy

ROUNDS = int(os.environ.get("AUTH_RETRY_ROUNDS", "1"))
INTERVAL = 1.5


@pytest.fixture
def proxy():
    server = _FakeProxy()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def _closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(multi, url, easy=False, timeout=None):
    # Returns (response code, curl), or (None, None) if the proxy was refused
    curl = mcurl.Curl(TARGET)
    curl.buffer()
    if timeout is not None:
        curl.set_timeout(timeout)
    if not curl.set_proxy(url):
        return None, None
    curl.set_auth("user", "pass", auth="BASIC")
    curl.is_easy = easy
    multi.do(curl)
    return (curl.resp if curl.errstr else curl.get_response()[1]), curl


def _code(multi, url, easy=False, timeout=None):
    return _request(multi, url, easy, timeout)[0]


def _burst(multi, url, count, easy=False, timeout=None):
    # Returns (response codes of admitted requests, refused count)
    results = _concurrent(lambda: _code(multi, url, easy, timeout), count)
    admitted = [code for code in results if code is not None]
    return admitted, count - len(admitted)


def _latched(url=None, proxy=None, interval=INTERVAL, max_interval=None):
    # A fresh MCurl whose proxy was latched by failure_threshold sequential 407s
    multi = mcurl.MCurl(auth_retry=interval, auth_retry_max=max_interval)
    url = url or proxy.url
    if proxy is not None:
        proxy.mode = "deny"
        proxy.delay = 0
        proxy.attempts = 0
        assert [_code(multi, url) for _ in range(multi.failure_threshold)] == [401] * multi.failure_threshold
    return multi, url


def _wait_interval(multi, url):
    since, interval = multi._blocked[url]
    time.sleep(max(0, since + interval - time.monotonic()) + 0.1)


def scenario_latch(proxy, refused=25):
    # (1) failure_threshold sequential 407s latch the proxy; later requests never reach it
    multi, url = _latched(proxy=proxy)
    try:
        attempts = proxy.attempts
        failed = multi.failed[url]
        codes = [_code(multi, url) for _ in range(refused)]
        return {"attempts": attempts, "failed": failed, "refused": codes.count(None), "after": proxy.attempts}
    finally:
        multi.close()


def scenario_reject_burst(proxy, count, easy, delay):
    # (2) bursts while still rejecting: exactly one upstream attempt per interval
    multi, url = _latched(proxy=proxy)
    try:
        per_interval = []
        for _ in range(2):
            _wait_interval(multi, url)
            proxy.delay = delay
            before = proxy.attempts
            admitted, refused = _burst(multi, url, count, easy)
            # Same interval again: nothing may reach the proxy
            proxy.delay = 0
            again, _ = _burst(multi, url, count, easy)
            per_interval.append((proxy.attempts - before, admitted, refused, len(again)))
        return {"per_interval": per_interval, "probe": multi._probe}
    finally:
        multi.close()


def scenario_accept_burst(proxy, count, easy, delay):
    # (3) burst after the proxy recovered: one probe, then the proxy is unblocked
    multi, url = _latched(proxy=proxy)
    try:
        _wait_interval(multi, url)
        proxy.mode = "allow"
        proxy.delay = delay
        before = proxy.attempts
        admitted, refused = _burst(multi, url, count, easy)
        probe_attempts = proxy.attempts - before
        proxy.delay = 0
        later, later_refused = _burst(multi, url, count, easy)
        return {
            "admitted": admitted,
            "refused": refused,
            "probe_attempts": probe_attempts,
            "failed": multi.failed[url],
            "later": later,
            "later_refused": later_refused,
            "later_attempts": proxy.attempts - before - probe_attempts,
            "blocked": url in multi._blocked,
            "probe": multi._probe,
        }
    finally:
        multi.close()


def scenario_reblock(proxy):
    # Recover, then reject again: latches again after failure_threshold 407s
    multi, url = _latched(proxy=proxy, interval=0.5)
    try:
        _wait_interval(multi, url)
        proxy.mode = "allow"
        assert _code(multi, url) == 200
        assert [_code(multi, url) for _ in range(5)] == [200] * 5
        proxy.mode = "deny"
        before = proxy.attempts
        codes = [_code(multi, url) for _ in range(multi.failure_threshold + 5)]
        latched_attempts = proxy.attempts - before
        _wait_interval(multi, url)
        before = proxy.attempts
        admitted, refused = _burst(multi, url, 20)
        return {
            "codes": codes,
            "latched_attempts": latched_attempts,
            "probe_attempts": proxy.attempts - before,
            "admitted": admitted,
            "refused": refused,
        }
    finally:
        multi.close()


def scenario_probe_error(proxy, kind, easy=False):
    # (4) a probe that times out or fails to connect must not leave the slot stuck
    if kind == "down":
        url = f"http://127.0.0.1:{_closed_port()}"
        multi, _ = _latched(url=url)
        multi.failed[url] = multi.failure_threshold
        assert _code(multi, url) is None
        timeout = None
    else:
        multi, url = _latched(proxy=proxy, interval=1.0)
        proxy.delay = 3
        timeout = 1
    try:
        outcomes = []
        for _ in range(2):
            _wait_interval(multi, url)
            admitted, refused = _burst(multi, url, 20, easy, timeout)
            outcomes.append((admitted, refused, dict(multi._probe), url in multi._blocked))
            # Re-blocked for another interval, not stuck and not open
            assert _code(multi, url, easy, timeout) is None
        failed = multi.failed[url]
        # The proxy comes back: next interval's probe unblocks it
        if kind == "down":
            proxy_up = _FakeProxy(int(url.rsplit(":", 1)[1]))
            threading.Thread(target=proxy_up.serve_forever, daemon=True).start()
            proxy_up.mode = "allow"
        else:
            proxy.mode, proxy.delay = "allow", 0
            proxy_up = None
        try:
            _wait_interval(multi, url)
            admitted, refused = _burst(multi, url, 20, easy)
            recovered = _burst(multi, url, 20, easy)
        finally:
            if proxy_up is not None:
                proxy_up.shutdown()
                proxy_up.server_close()
        return {
            "outcomes": outcomes,
            "failed": failed,
            "recovered": admitted,
            "recovered_refused": refused,
            "later": recovered,
            "probe": multi._probe,
        }
    finally:
        multi.close()


def scenario_default(proxy):
    # (5) auth_retry=0 (default): latch until reset, whatever happens upstream
    multi = mcurl.MCurl()
    url = proxy.url
    try:
        proxy.mode, proxy.delay, proxy.attempts = "deny", 0, 0
        assert (multi.auth_retry, multi.auth_retry_max) == (0, 0)
        # Counts reset on success below the threshold
        assert _code(multi, url) == 401 and _code(multi, url) == 401
        proxy.mode = "allow"
        assert _code(multi, url) == 200 and multi.failed[url] == 0
        proxy.mode = "deny"
        codes = [_code(multi, url) for _ in range(multi.failure_threshold)]
        latched = proxy.attempts
        proxy.mode = "allow"
        time.sleep(1)
        admitted, refused = _burst(multi, url, 50)
        stuck = (admitted, refused, proxy.attempts - latched, multi._probe)
        # Direct failed.clear() (the pre-API way) still unblocks
        multi.failed.clear()
        cleared = _code(multi, url)
        multi.failed[url] = multi.failure_threshold
        blocked = _code(multi, url)
        multi.reset_proxy_failures()
        reset = _code(multi, url)
        return {"codes": codes, "stuck": stuck, "cleared": cleared, "blocked": blocked, "reset": reset}
    finally:
        multi.close()


@pytest.mark.parametrize("rnd", range(ROUNDS))
def test_latch(proxy, rnd):
    result = scenario_latch(proxy)
    assert result == {"attempts": 3, "failed": 3, "refused": 25, "after": 3}


@pytest.mark.parametrize("rnd", range(ROUNDS))
@pytest.mark.parametrize(
    "count, delay, easy",
    [(10, 0, False), (50, 0, False), (200, 0, False), (200, 0.5, False), (50, 0.5, True), (200, 0, True)],
)
def test_reject_burst_one_attempt_per_interval(proxy, count, delay, easy, rnd):
    result = scenario_reject_burst(proxy, count, easy, delay)
    for attempts, admitted, refused, again in result["per_interval"]:
        assert attempts == 1
        assert admitted == [401]
        assert refused == count - 1
        assert again == 0
    assert result["probe"] == {}


@pytest.mark.parametrize("rnd", range(ROUNDS))
@pytest.mark.parametrize("count, easy", [(10, False), (50, False), (200, False), (50, True)])
def test_accept_burst_held_probe(proxy, count, easy, rnd):
    # The probe is still in flight while the whole burst arrives: exactly one request admitted
    result = scenario_accept_burst(proxy, count, easy, 0.75)
    assert result["admitted"] == [200]
    assert result["refused"] == count - 1
    assert result["probe_attempts"] == 1
    assert result["failed"] == 0
    assert result["later"] == [200] * count
    assert result["later_refused"] == 0
    assert result["later_attempts"] == count
    assert not result["blocked"]
    assert result["probe"] == {}


@pytest.mark.parametrize("rnd", range(ROUNDS))
@pytest.mark.parametrize("count", [50, 200])
def test_accept_burst_fast_probe(proxy, count, rnd):
    # A fast probe unblocks mid-burst: every admitted request is a 200 and reached the proxy once
    result = scenario_accept_burst(proxy, count, False, 0)
    assert result["admitted"] and set(result["admitted"]) == {200}
    assert result["probe_attempts"] == len(result["admitted"])
    assert result["failed"] == 0
    assert result["later"] == [200] * count
    assert result["probe"] == {}


@pytest.mark.parametrize("rnd", range(ROUNDS))
def test_recover_then_reblock(proxy, rnd):
    result = scenario_reblock(proxy)
    assert result["codes"][:3] == [401] * 3
    assert result["codes"][3:] == [None] * 5
    assert result["latched_attempts"] == 3
    assert result["probe_attempts"] == 1
    assert result["admitted"] == [401]
    assert result["refused"] == 19


@pytest.mark.parametrize("rnd", range(ROUNDS))
@pytest.mark.parametrize("kind, easy", [("down", False), ("down", True), ("timeout", False), ("timeout", True)])
def test_probe_error_does_not_stick(proxy, kind, easy, rnd):
    result = scenario_probe_error(proxy, kind, easy)
    for admitted, refused, _probe, blocked in result["outcomes"]:
        # One probe per interval, 502/504 for a dead or stalled proxy, slot freed afterwards
        assert len(admitted) == 1 and admitted[0] in (502, 504)
        assert refused == 19
        assert blocked
    assert result["probe"] == {}
    # Errors do not count as auth failures
    assert result["failed"] == 3
    assert result["recovered"] and set(result["recovered"]) == {200}
    assert result["later"] == ([200] * 20, 0)


@pytest.mark.parametrize("rnd", range(ROUNDS))
def test_default_unchanged(proxy, rnd):
    result = scenario_default(proxy)
    assert result["codes"] == [401] * 3
    admitted, refused, attempts, probe = result["stuck"]
    assert (admitted, refused, attempts, probe) == ([], 50, 0, {})
    assert result["cleared"] == 200
    assert result["blocked"] is None
    assert result["reset"] == 200

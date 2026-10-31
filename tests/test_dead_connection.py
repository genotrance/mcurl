"""Local-network regression tests for dead and slow connections."""

import os
import socket
import socketserver
import threading
import time

import pytest

import mcurl

ROUNDS = int(os.environ.get("MULTI_ROUND_ROUNDS", "30"))
WIDTH = int(os.environ.get("MULTI_ROUND_WIDTH", "4"))


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


def _url(address):
    return f"http://{address[0]}:{address[1]}/"


@pytest.mark.slow
def test_stalled_http_body_times_out(local_server):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\npart")
            time.sleep(8)

    address = local_server(Handler)
    curl = mcurl.Curl(_url(address))
    curl.set_low_speed(1, 1)
    curl.buffer()
    multi = mcurl.MCurl()
    started = time.monotonic()
    assert not multi.do(curl)
    elapsed = time.monotonic() - started
    assert curl.cerr == mcurl.libcurl.CURLE_OPERATION_TIMEDOUT
    assert curl.resp == 504
    assert 1 <= elapsed < 7
    multi.close()


@pytest.mark.slow
def test_slow_steady_download_survives_low_speed_guard(local_server):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 140\r\n\r\n")
            for _ in range(14):
                self.request.sendall(b"x" * 10)
                time.sleep(0.5)

    curl = mcurl.Curl(_url(local_server(Handler)))
    curl.set_low_speed(1, 2)
    curl.buffer()
    multi = mcurl.MCurl()
    assert multi.do(curl), curl.errstr
    assert curl.get_data(encoding=None) == b"x" * 140
    multi.close()


@pytest.mark.slow
def test_connect_tunnel_has_no_low_speed_deadline(local_server):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            data = self.request.recv(4)
            self.request.sendall(data)

    address = local_server(Handler)
    curl = mcurl.Curl(f"{address[0]}:{address[1]}", method="CONNECT")
    multi = mcurl.MCurl()
    assert multi.do(curl), curl.errstr
    time.sleep(2)
    tunnel = socket.socket(fileno=socket.dup(curl.sock_fd))
    tunnel.setblocking(True)
    tunnel.sendall(b"ping")
    assert tunnel.recv(4) == b"ping"
    tunnel.close()
    multi.close()


@pytest.mark.slow
def test_silent_proxy_connect_obeys_connect_timeout(local_server):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            time.sleep(5)

    address = local_server(Handler)
    curl = mcurl.Curl("example.test:443", method="CONNECT", connect_timeout=1)
    curl.set_proxy(address[0], address[1])
    curl.set_auth("user", "password", "BASIC")
    multi = mcurl.MCurl()
    started = time.monotonic()
    assert not multi.do(curl)
    elapsed = time.monotonic() - started
    assert curl.cerr == mcurl.libcurl.CURLE_OPERATION_TIMEDOUT
    assert curl.resp == 504
    assert 0.8 <= elapsed < 4
    multi.close()


@pytest.mark.skipif(not hasattr(socket, "TCP_KEEPIDLE"), reason="detailed keepalive settings require Linux")
def test_keepalive_socket_options_and_disable(local_server):
    accepted = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nx")
            accepted.set()
            time.sleep(5)

    curl = mcurl.Curl(_url(local_server(Handler)))
    curl.set_keepalive(7, 3, 2)
    curl.set_low_speed(1, 4)
    curl.buffer()
    multi = mcurl.MCurl()
    worker = threading.Thread(target=multi.do, args=(curl,))
    worker.start()
    assert accepted.wait(2)
    _, fd = curl.get_activesocket()
    sock = socket.socket(fileno=socket.dup(fd))
    assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
    assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE) == 7
    assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL) == 3
    if hasattr(socket, "TCP_KEEPCNT") and hasattr(mcurl.libcurl, "CURLOPT_TCP_KEEPCNT"):
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT) == 2
    sock.close()
    multi.stop(curl)
    worker.join(3)
    multi.close()

    accepted.clear()
    disabled = mcurl.Curl(_url(local_server(Handler)))
    disabled.set_keepalive(0)
    disabled.set_low_speed(1, 4)
    disabled.buffer()
    multi = mcurl.MCurl()
    worker = threading.Thread(target=multi.do, args=(disabled,))
    worker.start()
    assert accepted.wait(2)
    _, fd = disabled.get_activesocket()
    sock = socket.socket(fileno=socket.dup(fd))
    assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 0
    sock.close()
    multi.stop(disabled)
    worker.join(3)
    multi.close()


def test_multi_no_timer_wait_is_bounded(monkeypatch):
    multi = mcurl.MCurl()
    multi.timer = None
    multi.rlist = {10}
    seen = []

    def fake_select(readers, writers, errors, timeout):
        seen.append(timeout)
        return [], [], []

    monkeypatch.setattr(mcurl.select, "select", fake_select)
    multi._perform()
    assert seen == [mcurl.MULTI_SELECT_TIMEOUT]
    multi.close()


def test_multi_no_timer_no_sockets_does_not_sleep(monkeypatch):
    # Nothing can interrupt a sleep, so capping it would stall do() callers
    multi = mcurl.MCurl()
    multi.timer = None
    multi.rlist = set()
    multi.wlist = set()
    slept = []
    monkeypatch.setattr(mcurl.time, "sleep", slept.append)
    multi._perform()
    assert slept == []
    multi.timer = time.monotonic() + 0.25
    multi._perform()
    assert len(slept) == 1
    assert 0 <= slept[0] <= 0.25
    multi.close()


def test_multi_perform_skips_wait_for_finished_handle(monkeypatch):
    # A handle finished by another thread must not wait on unrelated sockets
    multi = mcurl.MCurl()
    multi.timer = None
    multi.rlist = {10}
    curl = mcurl.Curl("http://127.0.0.1/")
    curl.done = True
    monkeypatch.setattr(mcurl.select, "select", lambda *args: pytest.fail("select called"))
    multi._perform(curl)
    multi.close()


class _MultiRoundAuthProxy(socketserver.BaseRequestHandler):
    # Answers CONNECT with 407 on a kept-alive connection until the client
    # retries with credentials, like a multi-round Digest/NTLM handshake
    def handle(self):
        buf = b""
        while True:
            while b"\r\n\r\n" not in buf:
                data = self.request.recv(4096)
                if not data:
                    return
                buf += data
            head, buf = buf.split(b"\r\n\r\n", 1)
            if b"\nproxy-authorization:" not in head.lower():
                self.request.sendall(
                    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                    b'Proxy-Authenticate: Basic realm="fake"\r\nContent-Length: 0\r\n\r\n'
                )
                continue
            self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            data = self.request.recv(4)
            self.request.sendall(data)
            return


@pytest.mark.slow
def test_concurrent_multi_round_connect_auth_is_not_stalled(local_server, monkeypatch):
    # pymcurl 8.23.0.1 slept a full MULTI_SELECT_TIMEOUT whenever a do() loop
    # saw no sockets and no libcurl timer; concurrent tunnels then stalled
    # long enough for TLS servers with short handshake timeouts to give up
    address = local_server(_MultiRoundAuthProxy)
    real_sleep = time.sleep
    long_sleeps = []

    def sleep(seconds):
        if seconds >= mcurl.MULTI_SELECT_TIMEOUT:
            long_sleeps.append(seconds)
        real_sleep(seconds)

    monkeypatch.setattr(mcurl.time, "sleep", sleep)
    multi = mcurl.MCurl()
    errors = []

    def tunnel():
        curl = mcurl.Curl("example.test:443", method="CONNECT")
        curl.set_proxy(address[0], address[1])
        curl.set_auth("user", "password", "BASIC")
        if not multi.do(curl):
            errors.append(curl.errstr)
            return
        # Like px, keep the finished handle in the multi while the tunnel runs
        sock = socket.socket(fileno=socket.dup(curl.sock_fd))
        sock.setblocking(True)
        sock.sendall(b"ping")
        if sock.recv(4) != b"ping":
            errors.append("tunnel echo failed")
        real_sleep(0.05)
        sock.close()
        multi.remove(curl)

    for _ in range(ROUNDS):
        threads = [threading.Thread(target=tunnel) for _ in range(WIDTH)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
    multi.close()
    assert errors == []
    assert long_sleeps == []

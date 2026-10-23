"""Local-network regression tests for dead and slow connections."""

import socket
import socketserver
import threading
import time

import pytest

import mcurl


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

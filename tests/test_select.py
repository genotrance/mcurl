"""Tests for MCurl.select() — bidirectional CONNECT tunnel relay."""

import socket
import threading
import time
from unittest.mock import patch

import mcurl


class TestSelectPartialWrite:
    """Verify that select() handles partial writes without busy-looping."""

    def _make_socketpair(self):
        """Create a connected socket pair."""
        return socket.socketpair()

    def test_partial_write_no_busyloop(self):
        """After a partial send() drains the write queue, select() must not
        spin at 100% CPU.  It should block in select() waiting for real I/O
        and honour the idle timeout.

        This is the bug described in issue #10: `not data` (the just-sent
        chunk, always truthy) was used instead of `not wdata` (the deque),
        so sockets were never removed from wlist after a partial write
        drained.
        """
        # Create two socket pairs to simulate client <-> proxy <-> server
        client_local, client_remote = self._make_socketpair()
        server_local, server_remote = self._make_socketpair()

        m = mcurl.MCurl()

        # Create a minimal Curl stub with just enough for select()
        ec = mcurl.Curl.__new__(mcurl.Curl)
        ec.easyhash = "test"
        ec.sock_fd = server_local.fileno()
        ec.is_connect = False
        ec.is_tunnel = False
        ec.xheaders = None
        ec.method = "CONNECT"
        ec.url = "example.com:443"
        ec.request_version = "HTTP/1.1"

        # Mock get_used_proxy to return (0, False) so we skip the header
        # sending logic
        ec.get_used_proxy = lambda: (0, False)

        # We need to trigger a partial write scenario:
        # 1. Server sends data to client through the tunnel
        # 2. The send() to client_remote returns a short count (partial write)
        # 3. On the next send(), all remaining data is sent
        # 4. After that, the tunnel should idle and the select loop should
        #    block (not spin)

        idle_timeout = 2
        send_call_count = [0]
        original_send = socket.socket.send

        def patched_send(self_sock, data, *args, **kwargs):
            send_call_count[0] += 1
            if send_call_count[0] == 1:
                # First send: partial write — only send half the data
                half = max(1, len(data) // 2)
                return original_send(self_sock, data[:half], *args, **kwargs)
            return original_send(self_sock, data, *args, **kwargs)

        def run_select():
            with patch.object(socket.socket, "send", patched_send):
                m.select(ec, client_remote, idle=idle_timeout)

        t = threading.Thread(target=run_select)
        t.start()

        # Send data from the "server" side into the tunnel
        # This will be received by server_local (curl_sock) and relayed
        # to client_remote with the patched partial send
        time.sleep(0.1)
        server_remote.sendall(b"X" * 100)

        # Wait a moment for the partial write + completion to happen
        time.sleep(0.3)

        # Now close the server side to let the tunnel end cleanly
        server_remote.close()

        # The select loop should terminate — either via the connection
        # close or via idle timeout.  With the bug, it would spin forever
        # and never terminate (timeout would never fire because max_idle
        # is reset every iteration).
        t.join(timeout=idle_timeout + 3)
        assert not t.is_alive(), (
            "select() is still running — busy-loop bug: socket not removed from wlist after partial write drained"
        )

        # Verify data was actually received by the client
        client_local.setblocking(False)
        received = b""
        try:
            while True:
                chunk = client_local.recv(4096)
                if not chunk:
                    break
                received += chunk
        except BlockingIOError:
            pass

        assert len(received) == 100, f"Expected 100 bytes, got {len(received)}"

        # Clean up
        client_local.close()
        client_remote.close()
        server_local.close()
        m.close()

    def test_idle_timeout_honoured_after_partial_write(self):
        """After a partial write fully drains, the idle timeout should fire
        if no more data arrives.  This verifies the fix works end-to-end:
        the loop should exit in approximately `idle` seconds, not hang."""
        client_local, client_remote = self._make_socketpair()
        server_local, server_remote = self._make_socketpair()

        m = mcurl.MCurl()

        ec = mcurl.Curl.__new__(mcurl.Curl)
        ec.easyhash = "test_idle"
        ec.sock_fd = server_local.fileno()
        ec.is_connect = False
        ec.is_tunnel = False
        ec.xheaders = None
        ec.method = "CONNECT"
        ec.url = "example.com:443"
        ec.request_version = "HTTP/1.1"
        ec.get_used_proxy = lambda: (0, False)

        idle_timeout = 2
        original_send = socket.socket.send
        first_send = [True]

        def patched_send(self_sock, data, *args, **kwargs):
            if first_send[0]:
                first_send[0] = False
                half = max(1, len(data) // 2)
                return original_send(self_sock, data[:half], *args, **kwargs)
            return original_send(self_sock, data, *args, **kwargs)

        def run_select():
            with patch.object(socket.socket, "send", patched_send):
                m.select(ec, client_remote, idle=idle_timeout)

        t = threading.Thread(target=run_select)
        start = time.time()
        t.start()

        # Send one burst to trigger the partial write path
        time.sleep(0.1)
        server_remote.sendall(b"Y" * 200)
        time.sleep(0.3)

        # Don't close either side — let idle timeout fire
        t.join(timeout=idle_timeout + 5)
        elapsed = time.time() - start

        assert not t.is_alive(), "select() still running — idle timeout not honoured"
        # Should have exited after roughly idle_timeout seconds (with some
        # margin for the initial data exchange)
        assert elapsed < idle_timeout + 4, f"Took too long: {elapsed:.1f}s"

        client_local.close()
        client_remote.close()
        server_local.close()
        server_remote.close()
        m.close()

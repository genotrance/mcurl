import threading
import time

import pytest

import mcurl


def test_multi_concurrent(httpbin_both):
    # Add multiple handles to one MCurl, all should succeed
    m = mcurl.MCurl()
    handles = []
    for endpoint in ["/get", "/user-agent", "/headers"]:
        ec = mcurl.Curl(httpbin_both.url + endpoint)
        ec.set_insecure(True)
        ec.buffer()
        handles.append(ec)

    for ec in handles:
        ret = m.do(ec)
        assert ret, f"Multi concurrent failed for {ec.url}: {ec.errstr}"

    for ec in handles:
        data = ec.get_data()
        assert len(data) > 0, f"Empty response for {ec.url}"

    m.close()


def test_multi_close(httpbin_both):
    # MCurl.close() should clean up all handles
    m = mcurl.MCurl()
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    m.add(ec)
    assert len(m.handles) == 1
    m.close()
    assert len(m.handles) == 0


def test_multi_do_easy_vs_multi(httpbin_both):
    # MCurl.do() with is_easy=True falls back to easy perform
    m = mcurl.MCurl()
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ec.is_easy = True
    ret = m.do(ec)
    assert ret, f"Easy fallback via multi failed: {ec.errstr}"
    data = ec.get_data()
    assert len(data) > 0
    m.close()


def test_multi_threaded_do(httpbin_both):
    # Multiple threads each call m.do() concurrently on a shared MCurl
    m = mcurl.MCurl()
    num_threads = 4
    results = [None] * num_threads
    errors = [None] * num_threads

    def worker(idx):
        try:
            ec = mcurl.Curl(httpbin_both.url + "/get")
            ec.set_insecure(True)
            ec.buffer()
            ret = m.do(ec)
            assert ret, f"Thread {idx} failed: {ec.errstr}"
            data = ec.get_data()
            assert len(data) > 0, f"Thread {idx} empty response"
            m.remove(ec)
            results[idx] = data
        except Exception as exc:
            errors[idx] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    for i in range(num_threads):
        assert errors[i] is None, f"Thread {i} error: {errors[i]}"
        assert results[i] is not None, f"Thread {i} no result"

    m.close()


def test_multi_threaded_stop(httpbin_both):
    # Threads call do() while another thread calls stop() on a handle mid-flight
    m = mcurl.MCurl()
    errors = [None] * 2

    # Handle that will be stopped
    ec_stop = mcurl.Curl(httpbin_both.url + "/delay/2")
    ec_stop.set_insecure(True)
    ec_stop.buffer()

    def do_worker():
        try:
            m.do(ec_stop)
            m.remove(ec_stop)
        except Exception as exc:
            errors[0] = exc

    def stop_worker():
        try:
            time.sleep(0.2)
            m.stop(ec_stop)
        except Exception as exc:
            errors[1] = exc

    t1 = threading.Thread(target=do_worker)
    t2 = threading.Thread(target=stop_worker)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    for i in range(2):
        assert errors[i] is None, f"Thread {i} error: {errors[i]}"

    # Handle should be done (stopped or completed)
    assert ec_stop.done
    m.close()


def test_multi_threaded_easy_and_multi(httpbin_both):
    # Mix of is_easy=True and normal handles on the same MCurl from different threads
    m = mcurl.MCurl()
    num_threads = 4
    results = [None] * num_threads
    errors = [None] * num_threads

    def worker(idx):
        try:
            ec = mcurl.Curl(httpbin_both.url + "/get")
            ec.set_insecure(True)
            ec.buffer()
            if idx % 2 == 0:
                ec.is_easy = True
            ret = m.do(ec)
            assert ret, f"Thread {idx} failed: {ec.errstr}"
            data = ec.get_data()
            assert len(data) > 0, f"Thread {idx} empty response"
            if not ec.is_easy:
                m.remove(ec)
            results[idx] = data
        except Exception as exc:
            errors[idx] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    for i in range(num_threads):
        assert errors[i] is None, f"Thread {i} error: {errors[i]}"
        assert results[i] is not None, f"Thread {i} no result"

    m.close()


def test_multi_close_with_active(httpbin_both):
    # Call close() while handles are still being do()'d by other threads
    m = mcurl.MCurl()
    errors = [None] * 2

    def do_worker():
        try:
            ec = mcurl.Curl(httpbin_both.url + "/delay/2")
            ec.set_insecure(True)
            ec.buffer()
            m.do(ec)
            m.remove(ec)
        except Exception:  # noqa: S110
            pass

    def close_worker():
        try:
            time.sleep(0.3)
            m.close()
        except Exception as exc:
            errors[1] = exc

    t1 = threading.Thread(target=do_worker)
    t2 = threading.Thread(target=close_worker)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert errors[1] is None, f"close() error: {errors[1]}"


def test_multi_threaded_add_remove(httpbin_both):
    # Rapid concurrent add()/remove() calls to stress locking
    m = mcurl.MCurl()
    num_threads = 6
    errors = [None] * num_threads

    def worker(idx):
        try:
            ec = mcurl.Curl(httpbin_both.url + "/get")
            ec.set_insecure(True)
            ec.buffer()
            m.add(ec)
            time.sleep(0.05)
            m.remove(ec)
        except Exception as exc:
            errors[idx] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    for i in range(num_threads):
        assert errors[i] is None, f"Thread {i} error: {errors[i]}"

    assert len(m.handles) == 0, f"Handles not cleaned up: {len(m.handles)}"
    m.close()


def test_multi_setopt_reserved(httpbin_both):
    # setopt() should reject reserved callback options
    from _libcurl_cffi import lib as libcurl

    m = mcurl.MCurl()
    with pytest.raises(Exception, match="Callback options reserved"):
        m.setopt(libcurl.CURLMOPT_SOCKETFUNCTION, 0)
    m.close()


def test_multi_set_failure_threshold():
    # set_failure_threshold() should accept valid values and reject invalid
    m = mcurl.MCurl()
    m.set_failure_threshold(5)
    assert m.failure_threshold == 5

    with pytest.raises(ValueError, match="at least 1"):
        m.set_failure_threshold(0)

    with pytest.raises(ValueError, match="at least 1"):
        m.set_failure_threshold(-1)

    m.close()

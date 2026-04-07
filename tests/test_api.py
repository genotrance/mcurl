"""Tests for the new mcurl API surface (sections 1-17 of the API expansion spec)."""

import json
import os
import tempfile

import pytest

import mcurl
from mcurl import libcurl

# Section 1: Public re-export of cffi symbols


def test_public_exports():
    # mcurl.libcurl and mcurl.ffi should be accessible
    assert hasattr(mcurl, "libcurl")
    assert hasattr(mcurl, "ffi")
    assert hasattr(mcurl.libcurl, "CURLOPT_URL")
    assert hasattr(mcurl.ffi, "new")


def test_all_exports():
    # __all__ should be defined and contain key symbols
    assert hasattr(mcurl, "__all__")
    for name in ["libcurl", "ffi", "Curl", "MCurl"]:
        assert name in mcurl.__all__


# Section 2: Curl.strerror / MCurl.strerror


def test_curl_strerror():
    result = mcurl.Curl.strerror(libcurl.CURLE_URL_MALFORMAT)
    assert len(result) > 0
    assert mcurl.Curl.strerror(libcurl.CURLE_OK) != ""


def test_mcurl_strerror():
    result = mcurl.MCurl.strerror(0)  # CURLM_OK
    assert len(result) > 0


def test_strerror_used_in_errors(httpbin_both):
    # Perform a request with a bad URL and check the error string uses strerror
    m = mcurl.MCurl()
    ec = mcurl.Curl("not_a_url")
    ec.buffer()
    m.do(ec)
    # errstr should contain the libcurl strerror message
    assert len(ec.errstr) > 0
    m.close()


# Section 3: Generic setopt/getinfo


def test_setopt_getinfo(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    # setopt auto-converts Python int to C long for LONG-type options
    ec.setopt(libcurl.CURLOPT_TIMEOUT, 30)
    ec.set_insecure(True)
    ec.buffer()
    ec.perform()
    ret, url = ec.getinfo(libcurl.CURLINFO_EFFECTIVE_URL)
    assert ret == 0
    assert "/get" in url
    ret, code = ec.getinfo(libcurl.CURLINFO_RESPONSE_CODE)
    assert code == 200
    ret, t = ec.getinfo(libcurl.CURLINFO_TOTAL_TIME)
    assert t > 0


def test_getinfo_string(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ec.perform()
    ret, val = ec.getinfo(libcurl.CURLINFO_EFFECTIVE_URL)
    assert ret == 0
    assert isinstance(val, str)


def test_getinfo_long(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ec.perform()
    ret, val = ec.getinfo(libcurl.CURLINFO_RESPONSE_CODE)
    assert ret == 0
    assert isinstance(val, int)
    assert val == 200


def test_getinfo_double(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ec.perform()
    ret, val = ec.getinfo(libcurl.CURLINFO_TOTAL_TIME)
    assert ret == 0
    assert isinstance(val, float)
    assert val > 0


def test_getinfo_unknown_type():
    ec = mcurl.Curl("http://example.com")
    with pytest.raises(ValueError, match="Unknown CURLINFO type"):
        ec.getinfo(0x700000 | 1)  # Invalid type mask


def test_setopt_auto_string(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/user-agent")
    ec.set_insecure(True)
    # setopt auto-converts Python str to C char* for OBJECTPOINT options
    ec.setopt(libcurl.CURLOPT_USERAGENT, "test-agent-auto")
    ec.buffer()
    ec.perform()
    data = ec.get_data()
    assert "test-agent-auto" in data


def test_setopt_auto_bool(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    # setopt auto-converts Python bool to C long for LONG-type options
    ec.setopt(libcurl.CURLOPT_FOLLOWLOCATION, True)
    ec.buffer()
    ec.perform()
    _ret, code = ec.getinfo(libcurl.CURLINFO_RESPONSE_CODE)
    assert code == 200


# Section 5: set_timeout


def test_set_timeout(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.set_timeout(30)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0


def test_set_timeout_triggers_timeout(httpbin_both):
    # set_timeout(1) on a delayed endpoint should timeout
    ec = mcurl.Curl(httpbin_both.url + "/delay/3")
    ec.set_insecure(True)
    ec.set_timeout(1)
    ec.buffer()
    ret = ec.perform()
    assert ret != 0  # Should fail with timeout


# Section 6: HTTP server auth


def test_set_httpauth(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/basic-auth/user/passwd")
    ec.set_insecure(True)
    ec.set_httpauth("user", "passwd", "BASIC")
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    ret, code = ec.get_response()
    assert code == 200


def test_set_httpauth_wrong_password(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/basic-auth/user/passwd")
    ec.set_insecure(True)
    ec.set_httpauth("user", "wrong", "BASIC")
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    ret, code = ec.get_response()
    assert code == 401


def test_set_bearer_token(httpbin_both):
    # Bearer token test - httpbin echoes headers, verify token is sent
    ec = mcurl.Curl(httpbin_both.url + "/headers")
    ec.set_insecure(True)
    ec.set_bearer_token("test-token-123")
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    data = json.loads(ec.get_data())
    auth_header = data.get("headers", {}).get("Authorization", "")
    assert "Bearer" in auth_header


# Section 7: Cookie methods


def test_set_cookie(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/cookies")
    ec.set_insecure(True)
    ec.set_cookie("name=value; name2=value2")
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    data = json.loads(ec.get_data())
    cookies = data.get("cookies", {})
    assert cookies.get("name") == "value"
    assert cookies.get("name2") == "value2"


def test_cookie_engine_roundtrip(httpbin_both):
    # Enable cookie engine, set a cookie via response, get it back
    ec = mcurl.Curl(httpbin_both.url + "/cookies/set/testcookie/testvalue")
    ec.set_insecure(True)
    ec.set_follow()
    ec.load_cookies("")  # Enable cookie engine
    ec.buffer()
    ret = ec.perform()
    assert ret == 0

    ret, cookies = ec.get_cookies()
    assert ret == 0
    assert any("testcookie" in c for c in cookies)


def test_add_and_clear_cookies(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.load_cookies("")  # Enable engine
    ec.add_cookie("Set-Cookie: mycookie=myvalue; domain=localhost; path=/;")
    _ret, cookies = ec.get_cookies()
    assert any("mycookie" in c for c in cookies)

    ec.clear_cookies()
    _ret, cookies = ec.get_cookies()
    assert not any("mycookie" in c for c in cookies)


def test_remove_cookie(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.load_cookies("")
    ec.add_cookie(".localhost\tTRUE\t/\tFALSE\t0\trmcookie\trmvalue")
    _ret, cookies = ec.get_cookies()
    assert any("rmcookie" in c for c in cookies)

    ec.remove_cookie(".localhost", "/", "rmcookie")
    _ret, cookies = ec.get_cookies()
    assert not any("rmcookie" in c for c in cookies)


def test_save_load_cookies():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        cookie_file = f.name

    try:
        # Save cookies
        ec = mcurl.Curl("http://example.com")
        ec.load_cookies("")
        ec.save_cookies(cookie_file)
        ec.add_cookie(".example.com\tTRUE\t/\tFALSE\t0\tsaved\tvalue")
        # Trigger cleanup to write jar
        del ec

        # Verify file was written
        assert os.path.exists(cookie_file)
    finally:
        os.unlink(cookie_file)


# Section 8: Redirect control


def test_set_maxredirs(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/redirect/3")
    ec.set_insecure(True)
    ec.set_follow()
    ec.set_maxredirs(1)
    ec.buffer()
    ret = ec.perform()
    # Should fail or get non-200 since we limit to 1 redirect but 3 are needed
    ret, code = ec.get_response()
    assert code != 200 or ret != 0


def test_set_maxredirs_unlimited(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/redirect/3")
    ec.set_insecure(True)
    ec.set_follow()
    ec.set_maxredirs(-1)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    ret, code = ec.get_response()
    assert code == 200


def test_set_postredir(httpbin_both):
    # Just verify it doesn't crash
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.set_postredir(libcurl.CURL_REDIR_POST_ALL)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0


# Section 9: Content encoding


def test_set_encoding(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/gzip")
    ec.set_insecure(True)
    ec.set_encoding("")
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    data = json.loads(ec.get_data())
    assert data.get("gzipped") is True


# Section 10: Curl.dup()


def test_dup_basic(httpbin_both):
    template = mcurl.Curl(httpbin_both.url + "/get")
    template.set_insecure(True)
    template.set_timeout(30)

    c1 = template.dup(httpbin_both.url + "/user-agent")
    c1.buffer()
    c1.set_useragent("dup-test")
    ret = c1.perform()
    assert ret == 0
    data = json.loads(c1.get_data())
    assert data["user-agent"] == "dup-test"


def test_dup_preserves_state(httpbin_both):
    template = mcurl.Curl(httpbin_both.url + "/get")
    template.set_insecure(True)
    template.set_encoding("")

    c1 = template.dup()
    assert c1.url == template.url
    assert c1.method == template.method
    c1.buffer()
    ret = c1.perform()
    assert ret == 0


def test_dup_independent(httpbin_both):
    # Verify duped handle is independent
    template = mcurl.Curl(httpbin_both.url + "/get")
    template.set_insecure(True)

    c1 = template.dup(httpbin_both.url + "/headers")
    c2 = template.dup(httpbin_both.url + "/user-agent")

    c1.buffer()
    c1.perform()
    c2.buffer()
    c2.perform()

    d1 = json.loads(c1.get_data())
    d2 = json.loads(c2.get_data())
    assert "headers" in d1
    assert "user-agent" in d2


# Section 11: pause/unpause


def test_pause_unpause():
    # Just verify the methods exist and don't crash on a new handle
    ec = mcurl.Curl("http://example.com")
    # Pause on a non-active handle should return an error code (not crash)
    ret = ec.pause()
    assert isinstance(ret, int)
    ret = ec.unpause()
    assert isinstance(ret, int)


# Section 12: xferinfo callback


def test_set_xferinfo(httpbin_both):
    progress_calls = []

    def progress_fn(dltotal, dlnow, ultotal, ulnow):
        progress_calls.append((dltotal, dlnow, ultotal, ulnow))
        return 0

    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.set_xferinfo(progress_fn)
    ec.buffer()
    ec.is_easy = True
    ec.perform()

    assert len(progress_calls) > 0


# Section 13: seek callback


def test_seek_callback_auto_registered(httpbin_both):
    # buffer() with data should auto-register seek callback
    ec = mcurl.Curl(httpbin_both.url + "/post", "POST")
    ec.set_insecure(True)
    ec.buffer(b"test data")
    ec.set_headers({"Content-Length": "9"})
    ret = ec.perform()
    assert ret == 0


def test_seek_buffer_data_intact(httpbin_both):
    # Verify buffered upload data arrives intact at the server
    payload = b'{"key": "seek_test_value"}'
    ec = mcurl.Curl(httpbin_both.url + "/post", "POST")
    ec.set_insecure(True)
    ec.buffer(payload)
    ec.set_headers({"Content-Type": "application/json", "Content-Length": str(len(payload))})
    ret = ec.perform()
    assert ret == 0
    data = json.loads(ec.get_data())
    assert data["data"] == payload.decode("utf-8")


def test_set_seek_custom():
    seek_calls = []

    def custom_seek(offset, origin):
        seek_calls.append((offset, origin))
        return 0  # CURL_SEEKFUNC_OK

    ec = mcurl.Curl("http://example.com")
    ec.set_seek(custom_seek)
    assert ec._seek_fn is custom_seek


# Section 14: Context manager support


def test_curl_context_manager(httpbin_both):
    with mcurl.Curl(httpbin_both.url + "/get") as c:
        c.set_insecure(True)
        c.buffer()
        c.perform()
        assert c.get_response_code() == 200
    assert c.easy is None


def test_mcurl_context_manager():
    with mcurl.MCurl() as m:
        assert m._multi is not None
    # After close, MCURL is None
    assert mcurl.MCURL is None


# Section 15: unsetopt


def test_unsetopt_long():
    ec = mcurl.Curl("http://example.com")
    ec.setopt(libcurl.CURLOPT_TIMEOUT, mcurl.py2clong(30))
    ec.unsetopt(libcurl.CURLOPT_TIMEOUT)  # Should not crash


def test_unsetopt_string():
    ec = mcurl.Curl("http://example.com")
    ec.setopt(libcurl.CURLOPT_USERAGENT, mcurl.py2cstr("test"))
    ec.unsetopt(libcurl.CURLOPT_USERAGENT)  # Should not crash


def test_unsetopt_function():
    ec = mcurl.Curl("http://example.com")
    ec.unsetopt(libcurl.CURLOPT_WRITEFUNCTION)  # Should not crash


# Section 16: Getinfo convenience methods


def test_get_effective_url(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ec.perform()
    url = ec.get_effective_url()
    assert "/get" in url


def test_get_response_code(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ec.perform()
    assert ec.get_response_code() == 200


def test_get_response_code_404(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/status/404")
    ec.set_insecure(True)
    ec.buffer()
    ec.perform()
    assert ec.get_response_code() == 404


def test_get_content_type(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ec.perform()
    ct = ec.get_content_type()
    assert ct is not None
    assert "json" in ct.lower()


def test_get_content_type_none():
    # Before perform, content type should be None
    ec = mcurl.Curl("http://example.com")
    ct = ec.get_content_type()
    assert ct is None


def test_get_total_time(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ec.perform()
    t = ec.get_total_time()
    assert isinstance(t, float)
    assert t > 0


# Section 17: reset() audit


def test_reset_clears_new_attributes(httpbin_both):
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec._xferinfo_fn = lambda *a: 0
    ec._seek_fn = lambda *a: 0
    ec.is_easy = True

    ec.reset(httpbin_both.url + "/get")

    assert ec._xferinfo_fn is None
    assert ec._seek_fn is None
    assert ec.is_easy is False

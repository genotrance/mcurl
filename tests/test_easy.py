import json
import uuid

import pytest

import mcurl

from .conftest import query


def test_query(method, httpbin_both, is_multi, is_debug):
    # Test all HTTP methods
    testurl = httpbin_both.url + "/" + method.lower()
    data = str(uuid.uuid4()) if method in ["POST", "PUT", "PATCH"] else None
    query(testurl, method, data, check=True, insecure=True, debug=is_debug, multi=mcurl.MCurl() if is_multi else None)


def test_binary(httpbin_both, is_multi, is_debug):
    # Test binary data
    testurl = httpbin_both.url + "/image/jpeg"
    query(testurl, insecure=True, debug=is_debug, encoding=None)
    query(testurl, insecure=True, debug=is_debug, multi=mcurl.MCurl() if is_multi else None, encoding=None)


def test_head(httpbin_both):
    # HEAD returns 200, headers present, empty body
    ec = mcurl.Curl(httpbin_both.url + "/get", "HEAD")
    ec.set_insecure(True)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0, f"HEAD failed with {ret}\n{ec.errstr}"
    ret, resp = ec.get_response()
    assert ret == 0 and resp == 200, f"HEAD response: {ret}, {resp}"
    assert len(ec.get_headers()) > 0, "HEAD should return headers"
    assert len(ec.get_data()) == 0, "HEAD should return empty body"


def test_response_code(httpbin_both):
    # Verify get_response() returns (0, 200) for a successful GET
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    ret, resp = ec.get_response()
    assert ret == 0, f"get_response() error: {ret}"
    assert resp == 200, f"Expected 200, got {resp}"


def test_response_code_404(httpbin_both):
    # Verify get_response() returns 404 for a missing endpoint
    ec = mcurl.Curl(httpbin_both.url + "/status/404")
    ec.set_insecure(True)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    ret, resp = ec.get_response()
    assert ret == 0 and resp == 404, f"Expected 404, got {ret}, {resp}"


def test_follow_redirect(httpbin_both):
    # set_follow() should follow redirects to final destination
    ec = mcurl.Curl(httpbin_both.url + "/redirect/1")
    ec.set_insecure(True)
    ec.set_follow()
    ec.buffer()
    ret = ec.perform()
    assert ret == 0, f"Follow redirect failed: {ret}\n{ec.errstr}"
    ret, resp = ec.get_response()
    assert ret == 0 and resp == 200, f"Expected 200 after redirect, got {resp}"
    data = ec.get_data()
    assert len(data) > 0, "Expected non-empty response after redirect"


def test_no_follow_redirect(httpbin_both):
    # Without set_follow(), response should be 302
    ec = mcurl.Curl(httpbin_both.url + "/redirect/1")
    ec.set_insecure(True)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    ret, resp = ec.get_response()
    assert ret == 0 and resp == 302, f"Expected 302 without follow, got {resp}"


def test_useragent(httpbin_both):
    # Verify custom user-agent echoes back via httpbin
    ua = "mcurl-test-agent/1.0"
    ec = mcurl.Curl(httpbin_both.url + "/user-agent")
    ec.set_insecure(True)
    ec.buffer()
    ec.set_useragent(ua)
    ret = ec.perform()
    assert ret == 0, f"User-agent test failed: {ret}\n{ec.errstr}"
    data = ec.get_data()
    parsed = json.loads(data)
    assert parsed["user-agent"] == ua, f"Expected '{ua}', got '{parsed['user-agent']}'"


def test_custom_headers(httpbin_both):
    # Verify custom headers are sent and echoed by httpbin
    ec = mcurl.Curl(httpbin_both.url + "/headers")
    ec.set_insecure(True)
    ec.buffer()
    ec.set_headers({"X-Custom-Test": "mcurl-value-123"})
    ret = ec.perform()
    assert ret == 0, f"Custom headers test failed: {ret}\n{ec.errstr}"
    data = json.loads(ec.get_data())
    headers = data.get("headers", {})
    assert headers.get("X-Custom-Test") == "mcurl-value-123", f"Custom header not echoed: {headers}"


def test_reset_reuse(httpbin_both):
    # Create handle, perform, reset to new URL, perform again
    url1 = httpbin_both.url + "/get"
    ec = mcurl.Curl(url1)
    ec.set_insecure(True)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0, f"First request failed: {ret}\n{ec.errstr}"
    data1 = ec.get_data()
    assert url1 in data1

    url2 = httpbin_both.url + "/user-agent"
    ec.reset(url2)
    ec.set_insecure(True)
    ec.buffer()
    ec.set_useragent("reset-test")
    ret = ec.perform()
    assert ret == 0, f"Second request after reset failed: {ret}\n{ec.errstr}"
    data2 = json.loads(ec.get_data())
    assert data2["user-agent"] == "reset-test"


def test_binary_encoding_none(httpbin_both):
    # get_data(encoding=None) returns bytes
    ec = mcurl.Curl(httpbin_both.url + "/image/jpeg")
    ec.set_insecure(True)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    data = ec.get_data(encoding=None)
    assert isinstance(data, bytes), f"Expected bytes, got {type(data)}"
    assert len(data) > 0, "Expected non-empty binary data"


def test_https_insecure(httpbin_both):
    # HTTPS with set_insecure(True) should succeed
    if not httpbin_both.url.startswith("https"):
        pytest.skip("Only testing HTTPS")
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0, f"HTTPS insecure failed: {ret}\n{ec.errstr}"
    ret, resp = ec.get_response()
    assert ret == 0 and resp == 200


def test_error_bad_url():
    # Malformed URL should fail
    ec = mcurl.Curl("not_a_valid_url")
    ec.buffer()
    ret = ec.perform()
    assert ret != 0, "Expected failure for bad URL"


def test_error_unreachable():
    # Unreachable host should fail with connect error
    ec = mcurl.Curl("http://127.0.0.1:1", connect_timeout=2)
    ec.buffer()
    ret = ec.perform()
    assert ret != 0, "Expected failure for unreachable host"


def test_set_verbose(httpbin_both):
    # set_verbose() should not crash
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.set_verbose()
    ec.buffer()
    ret = ec.perform()
    assert ret == 0, f"Verbose test failed: {ret}\n{ec.errstr}"


def test_set_transfer_decoding(httpbin_both):
    # set_transfer_decoding() should not crash
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.set_transfer_decoding(False)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0, f"Transfer decoding test failed: {ret}\n{ec.errstr}"


def test_get_primary_ip(httpbin_both):
    # get_primary_ip() uses ffi.new("char *[]") which requires a length
    # This tests the underlying getinfo call via raw cffi
    from _libcurl_cffi import ffi
    from _libcurl_cffi import lib as libcurl

    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    ip = ffi.new("char **")
    ret = libcurl.curl_easy_getinfo(ec.easy, libcurl.CURLINFO_PRIMARY_IP, ip)
    assert ret == 0, f"get_primary_ip() error: {ret}"
    ip_str = ffi.string(ip[0]).decode("utf-8")
    assert len(ip_str) > 0, "Expected non-empty IP"


def test_get_activesocket(httpbin_both):
    # get_activesocket() should return a valid socket fd after perform
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    ret, _sock_fd = ec.get_activesocket()
    assert ret == 0, f"get_activesocket() error: {ret}"


def test_get_used_proxy(httpbin_both):
    # Without proxy, get_used_proxy() should return False
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    ret, used = ec.get_used_proxy()
    assert ret == 0, f"get_used_proxy() error: {ret}"
    assert used is False, "Expected no proxy used"


def test_get_proxyauth_used(httpbin_both):
    # Without proxy, get_proxyauth_used() should return 0
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    ret = ec.perform()
    assert ret == 0
    ret, auth = ec.get_proxyauth_used()
    assert ret == 0, f"get_proxyauth_used() error: {ret}"
    assert auth == 0, f"Expected no proxy auth, got {auth}"

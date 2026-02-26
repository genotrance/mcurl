import json
import sys
import time
import uuid

import pytest
import pytest_httpbin

import mcurl
from _libcurl_cffi import lib as libcurl


def dprint(msg):
    "Print message to stdout and debug file if open"
    offset = 0
    tree = ""
    while True:
        try:
            name = sys._getframe(offset).f_code.co_name
            offset += 1
            if name != "print":
                tree = "/" + name + tree
            if offset > 3:
                break
        except ValueError:
            break
    sys.stdout.write(str(int(time.time())) + ": " + tree + ": " + msg + "\n")


mcurl.dprint = dprint


def query(url, method="GET", data=None, check=False, insecure=False, debug=False, multi=None, encoding="utf-8"):
    ec = mcurl.Curl(url, method)
    ec.set_insecure(insecure)
    ec.set_debug(debug)
    if data is not None:
        ec.buffer(data.encode("utf-8"))
        ec.set_headers({"Content-Length": len(data)})
    else:
        ec.buffer()
    ec.set_useragent("mcurl tester")
    if multi is None:
        print(f"\nTesting {method} {url}")
        ret = ec.perform()
    else:
        print(f"\nTesting {method} {url} multi")
        ret = 0 if multi.do(ec) else 1
    assert ret == 0, f"Failed with error {ret}\n{ec.errstr}"

    ret_data = ec.get_data(encoding=encoding)
    print(f"\n{ec.get_headers()}Response length: {len(ret_data)}")
    if check:
        # Tests against httpbin
        assert url in ret_data, f"Failed: response does not contain {url}:\n{ret_data}"

        if data is not None:
            assert data in ret_data, f"Failed: response does not match {data}:\n{ret_data}"


@pytest.fixture(params=["GET", "POST", "PUT", "DELETE", "PATCH"])
def method(request):
    # All methods to test
    return request.param


@pytest.fixture(params=[False, True])
def is_multi(request):
    # Single or multi
    return request.param


@pytest.fixture(params=[False, True])
def is_debug(request):
    # Enabled or disabled
    return request.param


def test_query(method, httpbin_both, is_multi, is_debug):
    # Test all HTTP methods
    testurl = httpbin_both.url + "/" + method.lower()
    data = str(uuid.uuid4()) if method in [
        "POST", "PUT", "PATCH"] else None
    query(testurl, method, data, check=True, insecure=True, debug=is_debug,
          multi=mcurl.MCurl() if is_multi else None)


def test_binary(httpbin_both, is_multi, is_debug):
    # Test binary data
    testurl = httpbin_both.url + "/image/jpeg"
    query(testurl, insecure=True, debug=is_debug, encoding=None)
    query(testurl, insecure=True, debug=is_debug, multi=mcurl.MCurl()
          if is_multi else None, encoding=None)


def test_check_deps():
    # Check all dependencies are available
    from _libcurl_cffi import lib as libcurl

    features = [
        "CURL_VERSION_SSL",
        "CURL_VERSION_SPNEGO",
        "CURL_VERSION_KERBEROS5",
        "CURL_VERSION_NTLM"
    ]

    if sys.platform == "win32":
        features.append("CURL_VERSION_SSPI")
    else:
        features.append("CURL_VERSION_GSSAPI")

    vinfo = libcurl.curl_version_info(libcurl.CURLVERSION_LAST-1)
    for feature in features:
        bit = getattr(libcurl, feature)
        avail = True if (bit & vinfo.features) > 0 else False
        assert avail, f"Error: {feature} not available in libcurl"

    print("All dependencies available")


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
    assert headers.get("X-Custom-Test") == "mcurl-value-123", \
        f"Custom header not echoed: {headers}"


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


def test_curl_version():
    # curl_version() returns a positive integer
    ver = mcurl.curl_version()
    assert isinstance(ver, int) and ver > 0, f"Invalid version: {ver}"


def test_curl_features():
    # get_curl_features() returns a list containing SSL
    features = mcurl.get_curl_features()
    assert isinstance(features, list) and len(features) > 0, "No features"
    assert "SSL" in features, f"SSL not in features: {features}"


def test_getauth():
    # Unit-test getauth() for various auth string inputs
    assert mcurl.getauth("NONE") == libcurl.CURLAUTH_NONE
    assert mcurl.getauth("ANY") == libcurl.CURLAUTH_ANY
    assert mcurl.getauth("NTLM") == libcurl.CURLAUTH_NTLM
    assert mcurl.getauth("BASIC") == libcurl.CURLAUTH_BASIC
    assert mcurl.getauth("DIGEST") == libcurl.CURLAUTH_DIGEST
    assert mcurl.getauth("NEGOTIATE") == libcurl.CURLAUTH_NEGOTIATE

    # NO prefix = ANY minus the specified method
    nontlm = mcurl.getauth("NONTLM")
    assert nontlm == (libcurl.CURLAUTH_ANY & ~libcurl.CURLAUTH_NTLM)

    # SAFENO prefix = ANYSAFE minus the specified method
    safenontlm = mcurl.getauth("SAFENONTLM")
    assert safenontlm == (libcurl.CURLAUTH_ANYSAFE & ~libcurl.CURLAUTH_NTLM)

    # ONLY prefix = ONLY | method
    onlyntlm = mcurl.getauth("ONLYNTLM")
    assert onlyntlm == (libcurl.CURLAUTH_ONLY | libcurl.CURLAUTH_NTLM)


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

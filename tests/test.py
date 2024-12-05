import sys
import time
import uuid

import pytest
import pytest_httpbin

import mcurl


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

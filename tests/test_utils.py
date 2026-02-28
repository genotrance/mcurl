import io
import platform
import sys

from _libcurl_cffi import ffi
from _libcurl_cffi import lib as libcurl

import mcurl


def test_curl_version():
    # curl_version() returns a positive integer
    ver = mcurl.curl_version()
    assert isinstance(ver, int) and ver > 0, f"Invalid version: {ver}"


def test_curl_features():
    # get_curl_features() returns a list containing SSL
    features = mcurl.get_curl_features()
    assert isinstance(features, list) and len(features) > 0, "No features"
    assert "SSL" in features, f"SSL not in features: {features}"


def test_get_curl_vinfo():
    # get_curl_vinfo() returns a struct with version_num
    vinfo = mcurl.get_curl_vinfo()
    assert vinfo.version_num > 0, "Invalid version_num"


def test_print_curl_version(capsys):
    # print_curl_version() should output version info via dprint
    output = []
    old_dprint = mcurl.dprint
    mcurl.dprint = lambda msg: output.append(msg)
    mcurl.print_curl_version()
    mcurl.dprint = old_dprint
    assert len(output) > 0, "Expected output from print_curl_version()"
    # Should contain Python version
    assert any(platform.python_version() in o for o in output), f"Expected Python version in output: {output}"


def test_check_deps():
    # Check all dependencies are available
    features = ["CURL_VERSION_SSL", "CURL_VERSION_SPNEGO", "CURL_VERSION_KERBEROS5", "CURL_VERSION_NTLM"]

    if sys.platform == "win32":
        features.append("CURL_VERSION_SSPI")
    else:
        features.append("CURL_VERSION_GSSAPI")

    vinfo = libcurl.curl_version_info(libcurl.CURLVERSION_LAST - 1)
    for feature in features:
        bit = getattr(libcurl, feature)
        avail = (bit & vinfo.features) > 0
        assert avail, f"Error: {feature} not available in libcurl"


def test_py2cstr():
    # py2cstr converts string to char*
    result = mcurl.py2cstr("hello")
    assert ffi.string(result) == b"hello"


def test_py2custr():
    # py2custr converts bytes to char*
    result = mcurl.py2custr(b"hello")
    assert ffi.string(result) == b"hello"


def test_py2clong():
    # py2clong converts int to long
    result = mcurl.py2clong(42)
    assert int(ffi.cast("long", result)) == 42


def test_py2cbool():
    # py2cbool converts bool to long (0 or 1)
    assert int(ffi.cast("long", mcurl.py2cbool(True))) == 1
    assert int(ffi.cast("long", mcurl.py2cbool(False))) == 0


def test_cvp2pystr():
    # cvp2pystr converts void* to Python string
    cstr = ffi.new("char[]", b"test-string")
    cvoidp = ffi.cast("void *", cstr)
    result = mcurl.cvp2pystr(cvoidp)
    assert result == "test-string"


def test_gethash():
    # gethash returns a string representation of the easy handle pointer
    easy = libcurl.curl_easy_init()
    h = mcurl.gethash(easy)
    assert isinstance(h, str)
    assert int(h) > 0
    libcurl.curl_easy_cleanup(easy)


def test_sanitized_auth_header():
    # sanitized() should hide authorization header values
    msg = "Authorization: Bearer secret-token-123"
    result = mcurl.sanitized(msg)
    assert "secret-token-123" not in result
    assert "sanitized" in result


def test_sanitized_proxy_auth():
    # sanitized() should hide proxy auth info
    msg = "Proxy-Authorization: Basic dXNlcjpwYXNz"
    result = mcurl.sanitized(msg)
    assert "dXNlcjpwYXNz" not in result
    assert "sanitized" in result


def test_sanitized_authenticate_header():
    # sanitized() should hide authenticate header values
    msg = "WWW-Authenticate: NTLM TlRMTVNTUAABAAAA"
    result = mcurl.sanitized(msg)
    assert "TlRMTVNTUAABAAAA" not in result
    assert "sanitized" in result


def test_sanitized_proxy_auth_using():
    # sanitized() should hide "proxy auth using" messages
    msg = "Proxy auth using ntlm with user foo"
    result = mcurl.sanitized(msg)
    assert "sanitized" in result


def test_sanitized_normal_message():
    # sanitized() should not modify normal messages
    msg = "Connected to httpbin.org"
    result = mcurl.sanitized(msg)
    assert result == msg


def test_yield_msgs():
    # yield_msgs should split multi-line debug messages
    data = ffi.new("char[]", b"line1\r\nline2\r\nline3\r\n")
    msgs = list(mcurl.yield_msgs(data, len(b"line1\r\nline2\r\nline3\r\n")))
    assert msgs == ["line1", "line2", "line3"]


def test_yield_msgs_single():
    # yield_msgs with a single line
    data = ffi.new("char[]", b"single line")
    msgs = list(mcurl.yield_msgs(data, len(b"single line")))
    assert msgs == ["single line"]


def test_yield_msgs_empty():
    # yield_msgs with empty data
    data = ffi.new("char[]", b"   ")
    msgs = list(mcurl.yield_msgs(data, len(b"   ")))
    assert msgs == []


def test_buffer_no_data(httpbin_both):
    # buffer() with no data should setup write/header buffers
    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    assert isinstance(ec.client_wfile, io.BytesIO)
    assert isinstance(ec.client_hfile, io.BytesIO)
    assert ec.client_rfile is None
    ret = ec.perform()
    assert ret == 0


def test_buffer_with_data(httpbin_both):
    # buffer() with data should setup read buffer too
    ec = mcurl.Curl(httpbin_both.url + "/post", "POST")
    ec.set_insecure(True)
    data = b"test-data"
    ec.buffer(data)
    ec.set_headers({"Content-Length": len(data)})
    assert isinstance(ec.client_rfile, io.BytesIO)
    assert isinstance(ec.client_wfile, io.BytesIO)
    assert isinstance(ec.client_hfile, io.BytesIO)
    ret = ec.perform()
    assert ret == 0

from _libcurl_cffi import ffi
from _libcurl_cffi import lib as libcurl


def test_raw_easy_init_cleanup():
    # Basic lifecycle: init, setopt, cleanup
    easy = libcurl.curl_easy_init()
    assert easy != ffi.NULL

    url = ffi.new("char []", b"http://httpbin.org/get")
    ret = libcurl.curl_easy_setopt(easy, libcurl.CURLOPT_URL, url)
    assert ret == libcurl.CURLE_OK

    libcurl.curl_easy_cleanup(easy)


def test_raw_version_info():
    # curl_version_info should return valid data
    vinfo = libcurl.curl_version_info(libcurl.CURLVERSION_LAST - 1)
    assert vinfo != ffi.NULL
    assert vinfo.version_num > 0

    version_str = ffi.string(vinfo.version).decode("utf-8")
    assert len(version_str) > 0


def test_raw_curl_version():
    # curl_version() returns a non-empty string
    version = libcurl.curl_version()
    assert version != ffi.NULL
    version_str = ffi.string(version).decode("utf-8")
    assert "libcurl" in version_str.lower() or len(version_str) > 0


def test_raw_multi_init_cleanup():
    # Basic multi lifecycle
    multi = libcurl.curl_multi_init()
    assert multi != ffi.NULL

    ret = libcurl.curl_multi_cleanup(multi)
    assert ret == 0  # CURLM_OK


def test_raw_easy_setopt_various():
    # Test various setopt calls
    easy = libcurl.curl_easy_init()

    # CURLOPT_VERBOSE
    ret = libcurl.curl_easy_setopt(easy, libcurl.CURLOPT_VERBOSE, ffi.cast("long", 1))
    assert ret == libcurl.CURLE_OK

    # CURLOPT_FOLLOWLOCATION
    ret = libcurl.curl_easy_setopt(easy, libcurl.CURLOPT_FOLLOWLOCATION, ffi.cast("long", 1))
    assert ret == libcurl.CURLE_OK

    # CURLOPT_SSL_VERIFYPEER
    ret = libcurl.curl_easy_setopt(easy, libcurl.CURLOPT_SSL_VERIFYPEER, ffi.cast("long", 0))
    assert ret == libcurl.CURLE_OK

    libcurl.curl_easy_cleanup(easy)


def test_raw_easy_reset():
    # curl_easy_reset should not crash
    easy = libcurl.curl_easy_init()
    url = ffi.new("char []", b"http://example.com")
    libcurl.curl_easy_setopt(easy, libcurl.CURLOPT_URL, url)
    libcurl.curl_easy_reset(easy)
    libcurl.curl_easy_cleanup(easy)


def test_raw_slist():
    # curl_slist_append and curl_slist_free_all
    slist = ffi.NULL
    slist = libcurl.curl_slist_append(slist, ffi.new("char[]", b"X-Custom: value1"))
    assert slist != ffi.NULL
    slist = libcurl.curl_slist_append(slist, ffi.new("char[]", b"X-Another: value2"))
    assert slist != ffi.NULL
    libcurl.curl_slist_free_all(slist)

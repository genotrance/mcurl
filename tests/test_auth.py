from _libcurl_cffi import lib as libcurl

import mcurl


def test_getauth_none():
    # NONE returns CURLAUTH_NONE
    assert mcurl.getauth("NONE") == libcurl.CURLAUTH_NONE


def test_getauth_any():
    assert mcurl.getauth("ANY") == libcurl.CURLAUTH_ANY


def test_getauth_methods():
    # Individual auth methods
    assert mcurl.getauth("NTLM") == libcurl.CURLAUTH_NTLM
    assert mcurl.getauth("BASIC") == libcurl.CURLAUTH_BASIC
    assert mcurl.getauth("DIGEST") == libcurl.CURLAUTH_DIGEST
    assert mcurl.getauth("NEGOTIATE") == libcurl.CURLAUTH_NEGOTIATE


def test_getauth_no_prefix():
    # NO prefix = ANY minus the specified method
    nontlm = mcurl.getauth("NONTLM")
    assert nontlm == (libcurl.CURLAUTH_ANY & ~libcurl.CURLAUTH_NTLM)

    nobasic = mcurl.getauth("NOBASIC")
    assert nobasic == (libcurl.CURLAUTH_ANY & ~libcurl.CURLAUTH_BASIC)


def test_getauth_safeno_prefix():
    # SAFENO prefix = ANYSAFE minus the specified method
    safenontlm = mcurl.getauth("SAFENONTLM")
    assert safenontlm == (libcurl.CURLAUTH_ANYSAFE & ~libcurl.CURLAUTH_NTLM)

    safenobasic = mcurl.getauth("SAFENOBASIC")
    assert safenobasic == (libcurl.CURLAUTH_ANYSAFE & ~libcurl.CURLAUTH_BASIC)


def test_getauth_only_prefix():
    # ONLY prefix = ONLY | method
    onlyntlm = mcurl.getauth("ONLYNTLM")
    assert onlyntlm == (libcurl.CURLAUTH_ONLY | libcurl.CURLAUTH_NTLM)

    onlybasic = mcurl.getauth("ONLYBASIC")
    assert onlybasic == (libcurl.CURLAUTH_ONLY | libcurl.CURLAUTH_BASIC)


def test_set_proxy_failure_threshold(httpbin_both):
    # Verify that set_proxy returns False after threshold is exceeded
    m = mcurl.MCurl()
    m.set_failure_threshold(2)

    # Simulate failures for a fake proxy
    fake_proxy = "fake-proxy.example.com"
    m.failed[fake_proxy] = 2

    ec = mcurl.Curl(httpbin_both.url + "/get")
    ec.set_insecure(True)
    ec.buffer()
    result = ec.set_proxy(fake_proxy)
    assert result is False, "Expected set_proxy to return False after threshold exceeded"

    # Below threshold should still work
    m.failed[fake_proxy] = 1
    ec2 = mcurl.Curl(httpbin_both.url + "/get")
    ec2.set_insecure(True)
    ec2.buffer()
    result2 = ec2.set_proxy(fake_proxy)
    assert result2 is True, "Expected set_proxy to return True below threshold"

    m.close()

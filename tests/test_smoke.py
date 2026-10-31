"""Wheel smoke tests: the short selection cibuildwheel runs on the installed wheel.

Selected with ``-m smoke``. Each test fails if the wheel cannot load libcurl,
perform a real request, or ship the files the platform packaging must bundle.
"""

import os
import sys
import sysconfig

import pytest

import mcurl
from mcurl import ffi, libcurl

pytestmark = pytest.mark.smoke

LIB_SUFFIXES = (".dll", ".dylib", ".so")


def _norm(path):
    "Canonical form for comparing paths (symlinks such as /var vs /private/var, Windows case and 8.3 names)"
    return os.path.normcase(os.path.realpath(path))


def _site_roots():
    return {_norm(sysconfig.get_paths()[k]) for k in ("purelib", "platlib")}


def _installed_extension_dir():
    "Directory of the compiled extension when mcurl runs from an installed wheel, else None"
    import _libcurl_cffi

    extdir = os.path.dirname(os.path.abspath(_libcurl_cffi.__file__))
    return extdir if _norm(extdir) in _site_roots() else None


def test_wheel_imported_from_site_packages():
    # cibuildwheel must test the installed wheel, not the checkout that pytest's import mode may put first on sys.path
    if not os.environ.get("CIBUILDWHEEL"):
        pytest.skip("not running under cibuildwheel")
    import _libcurl_cffi

    roots = _site_roots()
    for mod in (mcurl, _libcurl_cffi):
        where = _norm(os.path.dirname(os.path.abspath(mod.__file__)))
        assert where in roots or os.path.dirname(where) in roots, f"{mod.__name__} imported from {where}, not {roots}"


def test_import_loads_libcurl():
    # Importing mcurl exits the interpreter if cffi or libcurl cannot be loaded
    version = ffi.string(libcurl.curl_version()).decode("utf-8")
    assert version.startswith("libcurl/"), version
    assert mcurl.curl_version() > 0


def test_libcurl_features():
    # TLS and HTTPS must be present in the libcurl that the wheel loads
    features = mcurl.get_curl_features()
    assert "SSL" in features, features
    protocols = set()
    vinfo = mcurl.get_curl_vinfo()
    i = 0
    while vinfo.protocols[i] != ffi.NULL:
        protocols.add(ffi.string(vinfo.protocols[i]).decode("utf-8"))
        i += 1
    assert {"http", "https"} <= protocols, protocols


def test_libcurl_version_matches_pyproject():
    # The wheel's libcurl must match the base version in pyproject.toml
    toml_path = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
    with open(toml_path) as f:
        full = next(line.split("=", 1)[1].strip(' "\n') for line in f if line.startswith("version"))
    actual = ffi.string(mcurl.get_curl_vinfo().version).decode("utf-8")
    assert actual == full[: full.rfind(".")]


def test_real_request(httpbin_both):
    # A real request over loopback, single handle and through MCurl, on HTTP and HTTPS
    url = httpbin_both.url + "/get"
    for multi in (None, mcurl.MCurl()):
        ec = mcurl.Curl(url)
        ec.set_insecure(True)
        ec.buffer()
        ret = ec.perform() if multi is None else (0 if multi.do(ec) else 1)
        assert ret == 0, f"request failed with {ret}\n{ec.errstr}"
        assert ec.get_response() == (0, 200)
        assert url in ec.get_data()
    multi.close()


def test_cacert_bundle_shipped():
    ca = os.path.join(os.path.dirname(mcurl.__file__), "cacert.pem")
    assert os.path.getsize(ca) > 0, "cacert.pem missing from the package"


def test_untrusted_certificate_rejected(httpbin_secure):
    # An untrusted certificate must fail verification, not fail because the TLS backend or CA bundle is missing
    ec = mcurl.Curl(httpbin_secure.url + "/get")
    ec.buffer()
    ret = ec.perform()
    assert ret == libcurl.CURLE_PEER_FAILED_VERIFICATION, f"unexpected result {ret}\n{ec.errstr}"


def test_wheel_bundles_libcurl():
    # auditwheel (Linux), delocate (macOS) and delvewheel (Windows) must have copied libcurl into the wheel
    extdir = _installed_extension_dir()
    if extdir is None:
        assert not os.environ.get("CIBUILDWHEEL"), "cibuildwheel must test the installed wheel, not a source tree"
        pytest.skip("not an installed wheel: libcurl comes from the build environment")
    bundled = []
    for root, dirs, files in os.walk(extdir):
        if root[len(extdir) :].count(os.sep) >= 3:
            dirs[:] = []
        bundled += [
            os.path.join(root, f)
            for f in files
            if "curl" in f.lower() and not f.startswith("_libcurl_cffi") and (f.endswith(LIB_SUFFIXES) or ".so." in f)
        ]
    assert bundled, f"no bundled libcurl found under {extdir} on {sys.platform}"

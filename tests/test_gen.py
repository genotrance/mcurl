import os

import pytest

gen = pytest.importorskip("mcurl.gen")


@pytest.mark.parametrize("subdir", ["lib", "bin"])
def test_find_curlh_uses_jbb_include_sibling(tmp_path, subdir):
    root = tmp_path / "LibCURL"
    (root / subdir).mkdir(parents=True)
    curlh = root / "include" / "curl" / "curl.h"
    curlh.parent.mkdir(parents=True)
    curlh.write_text("")
    assert gen.find_curlh([str(root / subdir)], str(tmp_path / "fallback" / "curl.h")) == str(curlh)


def test_find_curlh_falls_back_to_fixed_path(tmp_path):
    lib = tmp_path / "moved" / "LibCURL" / "lib"
    lib.mkdir(parents=True)
    fallback = tmp_path / "LibCURL" / "include" / "curl" / "curl.h"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("")
    assert gen.find_curlh([str(lib)], str(fallback)) == str(fallback)


def test_find_curlh_error_names_both_paths(tmp_path):
    lib = tmp_path / "LibCURL" / "lib"
    lib.mkdir(parents=True)
    fallback = str(tmp_path / "fallback" / "curl.h")
    derived = os.path.join(str(tmp_path / "LibCURL"), "include", "curl", "curl.h")
    with pytest.raises(RuntimeError) as exc:
        gen.find_curlh([str(lib)], fallback)
    assert derived in str(exc.value)
    assert fallback in str(exc.value)

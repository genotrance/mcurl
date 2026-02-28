import os
import sys

from setuptools import setup

if sys.platform == "win32" and os.name == "nt":
    # On Windows with mingw32, setuptools links -lpythonXY instead of -lpython3
    # when py_limited_api=True. Override get_libraries to fix this.
    # See: https://github.com/pypa/setuptools/issues/4224
    from setuptools.command.build_ext import build_ext

    _original_get_libraries = build_ext.get_libraries

    def _patched_get_libraries(self, ext):
        libs = _original_get_libraries(self, ext)
        # Replace versioned python lib (e.g. python312) with python3 for abi3 builds
        if (
            getattr(ext, "py_limited_api", False)
            and self.compiler
            and getattr(self.compiler, "compiler_type", "") == "mingw32"
        ):
            libs = [lib if not lib.startswith("python") else "python3" for lib in libs]
        return libs

    build_ext.get_libraries = _patched_get_libraries

kwargs = {}
if sys.implementation.name == "cpython":
    import sysconfig as _sc

    _ext_suffix = _sc.get_config_var("EXT_SUFFIX") or ""
    _freethreaded = (
        bool(_sc.get_config_var("Py_GIL_DISABLED"))
        or "t-" in _ext_suffix
        or getattr(sys, "_is_gil_enabled", lambda: True)() is False
    )
    if not _freethreaded:
        kwargs["options"] = {"bdist_wheel": {"py_limited_api": "cp39"}}

setup(cffi_modules=["mcurl/gen.py:ffibuilder"], **kwargs)

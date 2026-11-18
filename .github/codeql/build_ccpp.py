"""Build the mcurl cffi extension for CodeQL's c-cpp analysis.

Compiles mcurl/gen.py's generated _libcurl_cffi.c against the distro
libcurl dev package (resolved with pkg-config) instead of the jbb-downloaded
LibCURL that the real wheel build uses: CodeQL only needs a compilable
translation unit to trace, not the exact shipped ABI. mcurl/gen.py is loaded
by file path (not `import mcurl.gen`) so this doesn't require the
not-yet-built _libcurl_cffi extension that mcurl/__init__.py imports.
"""

import importlib.util
import pathlib
import subprocess
import sys


def pkg_config(variable):
    return subprocess.run(
        ["pkg-config", f"--variable={variable}", "libcurl"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def main():
    spec = importlib.util.spec_from_file_location("mcurl_gen", "mcurl/gen.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    includedir = pkg_config("includedir")
    libdir = pkg_config("libdir")
    curlh = str(pathlib.Path(includedir, "curl", "curl.h"))
    inc = str(pathlib.Path(includedir, "curl"))

    cdef = gen.get_preprocessor(curlh, recurse=True)
    cdef += gen.gen_callbacks(cdef) + gen.CDEF_FOOTER

    builder = gen.cffi_prep(cdef, inc, [libdir])
    builder.compile(verbose=True)


if __name__ == "__main__":
    sys.exit(main())

# Build System

---

## `setup.py`

`setup.py` declares the cffi module and applies a Windows-specific fix. All
package metadata lives in `pyproject.toml`.

On Windows, setuptools links the versioned Python library (e.g. `-lpython312`)
instead of `-lpython3` when `py_limited_api=True` and the compiler is mingw32.
`setup.py` patches `build_ext.get_libraries` to replace the versioned library
with `python3` for abi3 builds. See
[setuptools#4224](https://github.com/pypa/setuptools/issues/4224).

`setup.py` also passes `options={"bdist_wheel": {"py_limited_api": "cp39"}}`
for CPython builds so that setuptools tags the wheel as `cp39-abi3` instead of
rebuilding per CPython version. This is skipped for PyPy and free-threaded
builds.

The `ffibuilder` entry point in `mcurl/gen.py` drives the entire build:

1. Downloads libcurl binaries from
   [genotrance/LibCURL_jll.jl](https://github.com/genotrance/LibCURL_jll.jl)
   via [jbb](https://pypi.org/project/jbb/) (Linux/Windows) or uses Homebrew
   (macOS).
2. Downloads Mozilla CA certificates via jbb.
3. Preprocesses `curl.h` with gcc to extract the C API surface.
4. Generates cffi callback stubs for libcurl callbacks.
5. Calls `cffi.FFI().set_source()` to produce the `_libcurl_cffi` extension
   module.

### Platform-specific behaviour

| Platform | libcurl source | Compiler | Notes |
|----------|---------------|----------|-------|
| Linux (glibc) | jbb / LibCURL_jll.jl | gcc | auditwheel bundles `.so` files |
| Linux (musl) | jbb / LibCURL_jll.jl | gcc | musllinux wheels |
| macOS | Homebrew (`brew install curl`) | clang | delocate bundles dylibs |
| Windows | jbb / LibCURL_jll.jl | mingw32 | delvewheel bundles DLLs |

---

## `pyproject.toml`

### Build backend

The build backend is `setuptools.build_meta` with `cffi`, `jbb`, and
`setuptools` as build-time requirements.

### Wheel tag

cffi's `set_source()` is called with `py_limited_api=True` for regular CPython
builds (set by `_use_stable_abi()` in `gen.py`), and `setup.py` passes
`py_limited_api="cp39"` to `bdist_wheel`, which produces a
`cp39-abi3-<platform>` wheel compatible with any CPython ≥ 3.9.

`py_limited_api` is automatically set to `False` for:
- PyPy (does not honour `Py_LIMITED_API`)
- Free-threaded CPython 3.14t+ (`Py_GIL_DISABLED` is incompatible with
  `Py_LIMITED_API`)

### Windows MinGW-w64

mcurl has always built against MinGW-w64 on Windows because MSVC lacks the
POSIX headers (`sys/socket.h`, `sys/types.h`) that `curl/system.h` requires.

For `cibuildwheel` wheel builds, `--build-option=--compiler=mingw32` is
passed via `config-settings` in `pyproject.toml`.

`setup.py` patches `build_ext.get_libraries` at import time (Windows only) to
replace the versioned `-lpython3XX` with `-lpython3` when the extension uses
`py_limited_api=True` and the compiler is mingw32. This avoids a setuptools
bug where abi3 extensions link to the wrong Python library on Windows.

`gen.py`'s `source_prep()` also adds `lib/` directories alongside the `bin/`
directories returned by jbb, so mingw32's linker can find import libraries
(`.dll.a` / `.a` files).

### macOS rpath

On macOS, `cffi_prep()` embeds `-Wl,-rpath,<lib>` for each Homebrew lib
directory so that the extension can find libcurl at runtime without
`DYLD_LIBRARY_PATH`. This is especially important for PyPy which has a
different Python prefix than CPython.

### cibuildwheel configuration

cibuildwheel is configured in `pyproject.toml` under `[tool.cibuildwheel]`.
The build uses `build[uv]` as the frontend for speed. PyPy and PyPy-EOL
builds are enabled for Linux only.

Several configurations are skipped because they are unsupported or broken:

- **i686 musl** — no demand for 32-bit Alpine wheels.
- **Free-threaded CPython (3.14t)** — deferred because `brotlicffi` (a
  transitive test dependency) has not published a compatible release.
- **macOS PyPy** — cffi 2.x from PyPI overwrites PyPy’s built-in
  `_cffi_backend`, causing silent ABI mismatches that produce
  `CURLE_URL_MALFORMED` on every request. Linux PyPy is unaffected.
- **Windows PyPy** — the mingw32 toolchain used for Windows builds is not
  compatible with PyPy on Windows.

`jsonschema<4.18` is pinned in the Linux-specific `test-requires` to prevent
`rpds-py` (a Rust package with no PyPy wheel) from being pulled in via the
`httpbin → flasgger → jsonschema` dependency chain.

Platform-specific `repair-wheel-command` entries bundle shared libraries into
wheels using auditwheel (Linux), delocate (macOS), and delvewheel (Windows).

---

## Wheel matrix

For CPython, a single `cp39-abi3` wheel is produced per platform/arch,
compatible with any CPython ≥ 3.9. For PyPy, a separate wheel is produced per
PyPy version (Linux only).

| Platform | Arch | CPython wheels | PyPy wheels | Notes |
|----------|------|:--------------:|:-----------:|-------|
| Linux (glibc) | x86_64 | 1 (abi3) | 2 (pypy310, pypy311) | |
| Linux (glibc) | i686 | 1 (abi3) | 2 | |
| Linux (glibc) | aarch64 | 1 (abi3) | 2 | QEMU |
| Linux (musl) | x86_64 | 1 (abi3) | — | no PyPy musl support |
| Linux (musl) | aarch64 | 1 (abi3) | — | QEMU, no PyPy musl support |
| Windows | AMD64 | 1 (abi3) | — | mingw32 incompatible with PyPy |
| macOS | arm64 | 1 (abi3) | — | cffi 2.x ABI conflict on PyPy |

---

## Version scheme

Versions follow `<libcurl_major>.<libcurl_minor>.<libcurl_patch>.<release>`,
e.g. `8.18.0.1`. The first three components track the upstream libcurl version;
the fourth is incremented for mcurl-specific releases against the same libcurl.

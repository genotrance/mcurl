# mcurl

[![Build status](https://img.shields.io/github/actions/workflow/status/genotrance/mcurl/main.yml?branch=main)](https://github.com/genotrance/mcurl/actions/workflows/main.yml?query=branch%3Amain)
[![PyPI version](https://img.shields.io/pypi/v/pymcurl)](https://pypi.org/project/pymcurl/)
[![License](https://img.shields.io/github/license/genotrance/mcurl)](https://github.com/genotrance/mcurl/blob/main/LICENSE.txt)

Python wrapper for [libcurl](https://curl.haxx.se/libcurl/) with a high-level
API for the easy and multi interfaces. Originally created for the
[Px](https://github.com/genotrance/px) proxy server.

## Installation

mcurl can be installed using pip:

```bash
pip install pymcurl
```

Binary [packages](https://pypi.org/project/pymcurl) are provided for the following platforms:
- aarch64-linux-gnu
- aarch64-linux-musl
- arm64-mac
- i686-linux-gnu
- x86_64-linux-gnu
- x86_64-linux-musl
- x86_64-windows

mcurl leverages [cffi](https://pypi.org/project/cffi) to interface with libcurl
and all binary dependencies are sourced from [binarybuilder.org](https://binarybuilder.org/).
auditwheel on Linux, delocate on macOS and delvewheel on Windows are used to bundle
the shared libraries into the wheels.

Thanks to [cffi](https://cffi.readthedocs.io/en/latest/cdef.html#ffibuilder-compile-etc-compiling-out-of-line-modules)
and [Py_LIMITED_API](https://docs.python.org/3/c-api/stable.html#limited-c-api),
the CPython wheel works on any CPython ≥ 3.9. Separate wheels are provided for
PyPy.

## Usage

### Easy interface

```python
import mcurl

mcurl.dprint = print

c = mcurl.Curl('http://httpbin.org/get')
c.set_debug()
c.buffer()
ret = c.perform()
if ret == 0:
    ret, resp = c.get_response()
    headers = c.get_headers()
    data = c.get_data()
    print(f"Response: {resp}\n\n{headers}{data}")
```

### Multi interface

```python
import mcurl

mcurl.dprint = print

m = mcurl.MCurl()

c1 = mcurl.Curl('http://httpbin.org/get')
c1.set_debug()
c1.buffer()
m.add(c1)

data = "test8192".encode("utf-8")
c2 = mcurl.Curl('https://httpbin.org/post', 'POST')
c2.set_debug()
c2.buffer(data=data)
c2.set_headers({"Content-Length": len(data)})
m.add(c2)

ret1 = m.do(c1)
ret2 = m.do(c2)

if ret1:
    print(f"Response: {c1.get_response()}\n\n" +
          f"{c1.get_headers()}{c1.get_data()}")
else:
    print(f"Failed with error: {c1.errstr}")

if ret2:
    print(f"Response: {c2.get_response()}\n\n" +
          f"{c2.get_headers()}{c2.get_data()}")
else:
    print(f"Failed with error: {c2.errstr}")

m.close()
```

### Raw libcurl API

The [libcurl C API](https://curl.se/libcurl/c/) can also be accessed directly:

```python
from _libcurl_cffi import lib as libcurl
from _libcurl_cffi import ffi

url = "http://httpbin.org/get"
curl = ffi.new("char []", url.encode("utf-8"))

easy = libcurl.curl_easy_init()
libcurl.curl_easy_setopt(easy, libcurl.CURLOPT_URL, curl)
cerr = libcurl.curl_easy_perform(easy)
```

## Threading

Each `MCurl` instance manages its own curl multi handle with internal locking for thread-safe concurrent operations. Multiple threads can safely call `add()`, `do()`, `remove()`, and `stop()` on the same `MCurl` instance.

**Recommended patterns:**

- **MCurl per thread** — create a separate `MCurl` in each thread for maximum parallelism:

  ```python
  import threading, mcurl

  def worker(url):
      m = mcurl.MCurl()
      c = mcurl.Curl(url)
      c.buffer()
      m.do(c)
      print(c.get_data())
      m.close()

  threads = [threading.Thread(target=worker, args=(f"http://httpbin.org/get?id={i}",)) for i in range(4)]
  for t in threads: t.start()
  for t in threads: t.join()
  ```

- **Shared MCurl** — multiple threads can share one `MCurl` instance (internally locked):

  ```python
  m = mcurl.MCurl()

  def worker(url):
      c = mcurl.Curl(url)
      c.buffer()
      m.do(c)
      print(c.get_data())
      m.remove(c)

  threads = [threading.Thread(target=worker, args=(f"http://httpbin.org/get?id={i}",)) for i in range(4)]
  for t in threads: t.start()
  for t in threads: t.join()
  m.close()
  ```

## Versioning

Version format is `X.Y.Z.P` where `X.Y.Z` matches the upstream libcurl version and `P` is the wrapper patch (starts at 1 per upstream release). Wheels are built automatically when a new [LibCURL_jll.jl](https://github.com/genotrance/LibCURL_jll.jl) release is detected.

## Documentation

See the [`docs/`](docs/) folder for detailed documentation:

- [**Build system**](docs/build.md) — `pyproject.toml`, `setup.py`, cffi, cibuildwheel, wheel matrix
- [**CI & GitHub Actions**](docs/ci.md) — workflows, deployment flow
- [**API reference**](docs/api.md) — `Curl`, `MCurl`, utility functions
- [**Changelog**](docs/changelog.md) — release history
- [**Testing**](docs/testing.md) — test layout, running tests, fixtures

## Development

Requires a C compiler and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/genotrance/mcurl.git
cd mcurl
make install
make test
```

| Target         | Description                                        |
|----------------|----------------------------------------------------|
| `make install` | Create venv, build C extension, install pre-commit |
| `make test`    | Run tests with coverage                            |
| `make check`   | Run linters and type checking                      |
| `make build`   | Build sdist and wheel                              |
| `make clean`   | Remove build artifacts                             |
| `make env`     | Print LD_LIBRARY_PATH for local development        |

## Contributing

Bug reports and pull requests are welcome at <https://github.com/genotrance/mcurl/issues>.

1. Fork and clone the repository.
2. Run `make install` to set up the venv, build the C extension, and install pre-commit hooks.
3. Create a feature branch, make changes, add tests in `tests/`.
4. Run `make check && make test` — all checks must pass.
5. Open a pull request. CI runs on Ubuntu, Windows, and macOS across Python 3.9–3.14 and PyPy 3.10/3.11.

## Building

mcurl is built using gcc on Linux, clang on macOS and mingw-x64 on Windows. The
shared libraries are downloaded from [binarybuilder.org](https://binarybuilder.org/)
using [jbb](https://pypi.org/project/jbb) for Linux and Windows. Custom libcurl
binaries that include `kerberos` support on Linux and remove the `libssh2` dependency
are [available](https://github.com/genotrance/LibCURL_jll.jl). macOS uses the
libcurl binaries and dependencies installed via Homebrew.

[cibuildwheel](https://cibuildwheel.pypa.io/) is used to build wheels for all
platforms and architectures via GitHub Actions. See [docs/build.md](docs/build.md)
for details.

## Acknowledgments

The modernization of this project — including expanded test suite, CI/CD infrastructure, and comprehensive documentation — was developed with the assistance of LLMs.

## License

MIT

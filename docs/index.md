# mcurl — Documentation

This folder contains the technical documentation for the `pymcurl` Python
package — a wrapper around [libcurl](https://curl.haxx.se/libcurl/) providing
high-level `Curl` (easy) and `MCurl` (multi) interfaces via
[cffi](https://cffi.readthedocs.io/).

## Contents

| File | Description |
|------|-------------|
| [build.md](build.md) | Build system: `pyproject.toml`, `setup.py`, cffi, cibuildwheel |
| [ci.md](ci.md) | GitHub Actions workflows: CI, build wheels, check-upstream |
| [api.md](api.md) | `Curl` and `MCurl` class reference, utility functions |
| [changelog.md](changelog.md) | Release history |
| [testing.md](testing.md) | Test suite layout, running tests, fixtures |

## Quick Reference

- **PyPI name**: `pymcurl`
- **Import**: `import mcurl`
- **Requires**: Python ≥ 3.9
- **Wheel tag**: `cp39-abi3` (one wheel per platform, covers CPython 3.9+)
- **Dependencies**: [cffi](https://pypi.org/project/cffi/)
- **libcurl source**: [genotrance/LibCURL_jll.jl](https://github.com/genotrance/LibCURL_jll.jl) (Linux/Windows), Homebrew (macOS)

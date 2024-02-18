from setuptools import setup

setup(
    cffi_modules=["mcurl/gen.py:ffibuilder"]
)
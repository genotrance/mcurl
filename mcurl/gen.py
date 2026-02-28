import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

import cffi
import jbb

# Platform specific additions
if sys.platform == "linux":
    CDEF_HEADER = """
typedef unsigned socklen_t;
typedef unsigned short sa_family_t;

struct sockaddr {
    sa_family_t sa_family;
    char sa_data[14];
};

typedef long time_t;

typedef struct {
    long %s int fds_bits[%d];
} fd_set;
""" % ("unsigned" if jbb.get_libc() == "musl" else "", 16 if sys.maxsize > 2**32 else 32)

    HEADER = """
#include <sys/socket.h>
"""

elif sys.platform == "win32":
    CDEF_HEADER = """
#define CURL_SOCKET_TIMEOUT 0xffffffffffffffff

typedef long long unsigned int SOCKET;

struct sockaddr {
    unsigned short sa_family;
    char sa_data[14];
};

typedef long long int time_t;

typedef struct fd_set
{
	unsigned int fd_count;
	SOCKET fd_array[64];
} fd_set;
"""
    HEADER = """
#include <winsock2.h>
"""
elif sys.platform == "darwin":
    CDEF_HEADER = """
typedef unsigned socklen_t;
typedef unsigned short sa_family_t;

struct sockaddr {
    sa_family_t sa_family;
    char sa_data[14];
};

typedef long time_t;

typedef struct {
    int fds_bits[32];
} fd_set;
"""

    HEADER = ""

# libcurl header to build
HEADER += """
#include "curl.h"
"""

# Extern for callbacks callbacks with same signature
# header_callback() = write_callback()
# wa_callback() = debug_callback()
CDEF_FOOTER = """
extern "Python" size_t header_callback(char *buffer, size_t size, size_t nitems, void *outstream);
extern "Python" int wa_callback (CURL * handle, curl_infotype type, char *data, size_t size, void *userptr);
"""

FILTERS = ["va_list", "__asm__", "CURL_HAS_DECLSPEC_ATTRIBUTE"]

DEFINES = {}


def code_cleanup(code):
    # Align code
    defines = ""
    codeout = ""
    for line in code.splitlines():
        # Clean up whitespace
        sline = line.strip()

        # Remove empty lines
        if len(sline) == 0:
            continue

        # Remove leading whitespaces
        i = 0
        while line[i] == " ":
            i += 1
        line = line[i:]

        # Separate out #define
        # Some #define refer to enum values which won't work
        if line.startswith("#define"):
            defines += line + "\n"
            continue

        # Reduce newlines
        codeout += line + " "
        if line[-1] == ";":
            codeout += "\n"

    codeout2 = ""
    for line in (defines + codeout).splitlines():
        # Skip lines with FILTERS
        skip = False
        for filt in FILTERS:
            if filt in line:
                skip = True
                break
        if skip:
            continue

        # Remove static void functions
        if line.startswith("static void"):
            continue

        # Resolve #defines
        if line.startswith("#define"):
            spl = line.split(" ", 2)
            if len(spl) == 3 and spl[1].startswith("CURL"):
                # Casting in #define
                if "(unsigned long)" in spl[2]:
                    spl[2] = spl[2].replace("(unsigned long)", "")

                if spl[2] in DEFINES:
                    # define CURL_YYY CURL_XXX
                    val = DEFINES[spl[2]]
                else:
                    if "CURL" in spl[2]:
                        # define CURL_ZZZ (CURL_XXX | CURL_YYY)
                        for key in sorted(DEFINES.keys(), reverse=True):
                            if key in spl[2]:
                                spl[2] = spl[2].replace(key, str(DEFINES[key]))

                    # Replace instances of \d+L with \d+
                    spl[2] = re.sub(r"(\d+)L\b", r"\1", spl[2])

                    # Evaluate value in Python - works for bit shifts, |, etc.
                    try:
                        val = eval(spl[2])
                    except:
                        continue

                    # Workaround for ~(unsigned long)
                    if spl[1].startswith("CURLAUTH_ANY"):
                        if sys.platform == "win32" or sys.maxsize <= 2**32:
                            val += 0xFFFFFFFF + 1
                        else:
                            val += 0xFFFFFFFFFFFFFFFF + 1

                if type(val) in [int, float]:
                    line = f"#define {spl[1]} {val}"
                    DEFINES[spl[1]] = val
                else:
                    continue
            else:
                continue

        codeout2 += line + "\n"

    return codeout2


def gen_callbacks(code):
    # Generate cffi callback definitions for the CDEF from the processed code
    callbacks = ""
    for line in code.splitlines():
        if line.startswith("typedef") and "(*" in line and "_callback" in line:
            callbacks += (
                line
                .replace("typedef", 'extern "Python"')
                .replace("(*curl_", "", 1)
                .replace("(*_curl", "")
                .replace("(*Wcurl_", "", 1)
                .replace("(*_Wcurl", "")
                .replace(")", "", 1)
                + "\n"
            )

    return callbacks


def get_preprocessor(sfile, incs=[], defines=[], recurse=False):
    # Get preprocessed output from the C/C++ compiler
    args = ["gcc"]
    start = False

    sfileName = os.path.basename(sfile)
    pDir = os.path.dirname(sfile)

    includeDirs = []

    args.extend(["-E", "-xc", "-w", "-dD"])

    # Add include directories to args
    for inc in incs:
        args.append(f"-I{inc}")
        includeDirs.append(inc.absolutePath().sanitizePath(noQuote=True))

    # Add #define values if needed
    for hdef in defines:
        args.append(f"-D{hdef}")

    # Remove gcc special calls
    args.extend([
        '"-D__attribute__(x)="',
        "-D__restrict=",
        "-D__restrict__=",
        "-D__extension__=",
        "-D__inline__=inline",
        "-D__inline=inline",
        "-D_Noreturn=",
        f"{sfile}",
    ])

    # Run preprocessor
    p = subprocess.run(" ".join(args), capture_output=True, text=True, shell=True)
    outp = p.stdout.replace("\\\\", os.sep).splitlines()
    if len(p.stderr) != 0:
        print(p.stderr)
        raise Exception("Failed in preprocessing")

    # Include content only from file
    code = ""
    for line in outp:
        # We want to keep blank lines here for comment processing
        if len(line) > 10 and line[0] == "#" and line[1] == " " and '"' in line:
            # # 1 "path/to/file.h" 1
            start = False
            line = line.split('"')[1]
            if sfile == line or (os.path.sep not in line and sfileName == line):
                start = True
            elif recurse:
                if len(pDir) == 0 or pDir in line:
                    start = True
                else:
                    for inc in includeDirs:
                        if line.startswith(inc):
                            start = True
                            if start:
                                break
        elif ": fatal error:" in line:
            raise Exception(
                "Failed in preprocessing, check if `incs` is needed or compiler `mode` is correct (c/cpp)"
                + "\n\nERROR:"
                + line.split(": fatal error:")[1]
            )
        else:
            if start:
                if "#undef" in line:
                    continue
                code += line + "\n"

    # Write preprocessor output to file
    prefile = os.path.basename(sfile).replace(".", "-pre.")
    with open(prefile, "w") as f:
        f.write(code)

    # Clean up for cffi
    code = code_cleanup(code)

    # Write cleaned up code to file
    with open(os.path.basename(sfile), "w") as f:
        f.write(code)

    return code


def get_libcurl_version():
    # Get module version from pyproject.toml
    with open("pyproject.toml") as f:
        toml = f.read()
    for line in toml.splitlines():
        if line.startswith("version"):
            spl = line.split("=", 1)
            if len(spl) == 2:
                version = spl[1].strip(' "')
                break

    # Return version without the last octet
    lastdot = version.rfind(".")
    return version[:lastdot]


def _use_stable_abi():
    # Detect whether we can use the stable/limited API
    # - PyPy does not honour Py_LIMITED_API
    # - Free-threaded CPython is incompatible with Py_LIMITED_API
    import sysconfig as _sc

    _is_cpython = sys.implementation.name == "cpython"
    _ext_suffix = _sc.get_config_var("EXT_SUFFIX") or ""
    _freethreaded = (
        bool(_sc.get_config_var("Py_GIL_DISABLED"))
        or "t-" in _ext_suffix
        or getattr(sys, "_is_gil_enabled", lambda: True)() is False
    )
    return _is_cpython and not _freethreaded


def cffi_prep(cdef, inc, libs):
    # Build with cffi
    ffibuilder = cffi.FFI()
    ffibuilder.cdef(CDEF_HEADER + cdef)
    define_macros = [("CURL_DISABLE_DEPRECATION", None)]
    extra_compile_args = []
    extra_link_args = []
    if sys.platform == "darwin":
        # Embed rpaths for all brew lib dirs so the extension finds
        # libcurl at runtime regardless of the active Python interpreter
        # (CPython, PyPy) without requiring DYLD_LIBRARY_PATH.
        for lib in libs:
            extra_link_args.append(f"-Wl,-rpath,{lib}")
    ffibuilder.set_source(
        "_libcurl_cffi",
        HEADER,
        libraries=["curl"],
        library_dirs=libs,
        include_dirs=[inc],
        define_macros=define_macros,
        extra_compile_args=extra_compile_args,
        extra_link_args=extra_link_args,
        py_limited_api=_use_stable_abi(),
    )
    return ffibuilder


def source_prep():
    # Download libcurl from JBB for Linux and Windows
    key = jbb.get_key()
    outdir = os.path.join(os.environ.get("TMP", tempfile.gettempdir()), "mcurl", key)
    version = get_libcurl_version()
    libs = []
    if sys.platform != "darwin":
        libs.extend(jbb.jbb(f"LibCURL-v{version}", outdir=outdir, project="genotrance", quiet=False))

        # On Windows, jbb returns bin/ dirs but mingw32 linker needs lib/ dirs
        # for import libraries (.dll.a / .a). Add lib/ equivalents.
        if sys.platform == "win32":
            lib_dirs = []
            for d in libs:
                lib_dirs.append(d)
                lib_dir = d.replace(os.sep + "bin", os.sep + "lib")
                if lib_dir != d and os.path.isdir(lib_dir):
                    lib_dirs.append(lib_dir)
            libs = lib_dirs

        # Header file location
        curlh = f"{outdir}{os.sep}LibCURL{os.sep}include{os.sep}curl{os.sep}curl.h"
    else:
        prefix = subprocess.check_output("brew --prefix", shell=True, text=True).strip()
        deps = ["curl"] + subprocess.check_output("brew deps -n --installed curl", shell=True, text=True).splitlines()
        for dep in deps:
            if dep == "ca-certificates":
                continue
            libs.append(prefix + "/opt/" + dep + "/lib")

        curlh = prefix + "/opt/curl/include/curl/curl.h"

    # Include directory
    inc = os.path.dirname(curlh)

    # Download CAcerts
    pemdst = "mcurl/cacert.pem"
    jbb.jbb("mozillaCACerts", outdir=outdir, quiet=False)
    pemsrc = glob.glob(f"{outdir}/**/cacert.pem", recursive=True)[0]
    try:
        shutil.copy(pemsrc, pemdst)
    except shutil.SameFileError:
        pass

    # Preprocess and clean code
    cdef = get_preprocessor(curlh, recurse=True)

    # Generate callback stubs
    cdef += gen_callbacks(cdef) + CDEF_FOOTER

    return cdef, inc, libs


def ffibuilder():
    # Prepare libcurl
    cdef, inc, libs = source_prep()

    builder = cffi_prep(cdef, inc, libs)

    return builder


def main():
    builder = ffibuilder()

    # Build with cffi
    builder.compile(verbose=True)


if __name__ == "__main__":
    main()

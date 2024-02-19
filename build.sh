#! /bin/sh

set -e

ARCH=`uname -m`
OS=`uname -s | tr '[:upper:]' '[:lower:]' | cut -d '/' -f 2`
if [ -d "$HOME/pyvenv" ]; then
    PYTHON=`ls -d $HOME/pyvenv/*/bin/python`
else
    PYTHON="python3"
fi

if [ "$OS" = "darwin" ]; then
    OS="macos"
    KEY="$ARCH-$OS"

    #export DYLD_LIBRARY_PATH="`pwd`/mcurllib/$KEY"
elif [ "$OS" = "linux" ]; then
    OS="linux"
    if [ -z `ldd /bin/ls | grep musl` ]; then
        LIBC="glibc"
    else
        LIBC="musl"
    fi
    KEY="$ARCH-$OS-$LIBC"

    export LD_LIBRARY_PATH="`pwd`/mcurllib/$KEY"
elif [[ "$OS" = "windows"* ]]; then
    OS="windows"
    KEY="$ARCH-$OS"
    PYTHON=`powershell -Command 'dir c:/Users/$Env:USERNAME/scoop/apps/python*/current/python.exe | % FullName'`
fi

if [ -f "/.dockerenv" ] || [ "$OS" = "macos" ] || [ "$OS" = "windows" ]; then
    if [ -f "/.dockerenv" ] && [ -d "$HOME/pyvenv" ]; then
        source $HOME/pyvenv/py3.11*/bin/activate
    fi
    if [ "$OS" = "linux" ]; then
        SCRIPTPATH=`realpath $0`
        cd `dirname $SCRIPTPATH`
    fi

    # Build wheel if not exists or --force
    if [ ! -d "wheel/$KEY" ] || [ "$1" = "--force" ]; then
        rm -rf build pymcurl.egg-info dist wheel/$KEY

        python3 -m pip install build
        if [ "$OS" = "windows" ]; then
            python3 -m build -w . -C="--build-option=build" -C="--build-option=--compiler" -C="--build-option=mingw32" -C="--build-option=bdist_wheel" -C="--build-option=--py-limited-api" -C="--build-option=cp32"
            read -s -n 1 -r -p "Rerun 'gcc -shared' with -lpython3, copy _libcurl_cffi.pyd to wheel and press Enter"
        else
            python3 -m build -w . -C="--build-option=bdist_wheel" -C="--build-option=--py-limited-api" -C="--build-option=cp32"
        fi

        if [ "$OS" = "linux" ]; then
            python3 -m pip install auditwheel
            LD_LIBRARY_PATH="`pwd`/mcurllib/$KEY" python3 -m auditwheel repair -w wheel/$KEY dist/*.whl
        elif [ "$OS" = "windows" ]; then
            python3 -m pip install delvewheel
            python3 -m delvewheel repair --add-path mcurllib/$KEY -w wheel/$KEY dist/*.whl
        elif [ "$OS" = "macos" ]; then
            python3 -m pip install delocate
            delocate-wheel -w wheel/$KEY dist/*.whl
        fi

        rm -rf build dist
    fi

    test() {
        $1 -V

        $1 -m pip install wheel/$KEY/pymcurl-*.whl --force-reinstall

        cd tests
        $1 test.py $HTTPBIN
        cd ..

        $1 -m pip uninstall pymcurl -y
    }

    for py in $PYTHON; do
        test $py
    done
else
    # Enable binfmt_misc for cross-compiling
    docker run --privileged --rm tonistiigi/binfmt --install all

    DOCKERCMD="docker run -it --rm --network host --privileged -v `pwd`:/mcurl"
    for arch in x86_64 aarch64 i686; do
        musl="px_musl"
        glibc="px_glibc"
        if [ ! "$arch" = "x86_64" ]; then
            musl="$musl"_"$arch"
            glibc="$glibc"_"$arch"
        fi

        if [ ! -z $HTTPBIN ]; then
            DOCKERCMD="$DOCKERCMD -e HTTPBIN=$HTTPBIN"
        fi

        if [ ! "$arch" = "i686" ]; then
            # Skip i686 for musl
            echo $musl
            $DOCKERCMD $musl /mcurl/build.sh $1
        fi
        echo $glibc
        $DOCKERCMD $glibc /mcurl/build.sh $1
    done
fi
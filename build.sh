#! /bin/sh

set -e

ARCH=`uname -m`
if [ -z "$PY" ]; then
    echo "PY not set"
    exit
fi

if [ -f "/.dockerenv" ] || [ "$OS" = "darwin" ] || [ "$OS" = "windows" ]; then
    # Setup jbb
    $PY -m pip install jbb
    KEY=`$PY -c "import jbb; print(jbb.get_key())"`

    if [ ! -d "wheel/$KEY" ] || [ "$1" = "--force" ]; then
        # Build wheels if missing or --force
        rm -rf build pymcurl.egg-info dist wheel/$KEY
        $PY -m pip install build cffi setuptools wheel

        if [ "$OS" = "windows" ]; then
            $PY -m build -w . -C="--build-option=build" -C="--build-option=--compiler" -C="--build-option=mingw32" -C="--build-option=bdist_wheel" -C="--build-option=--py-limited-api" -C="--build-option=cp32" | tee build.log

            # Rerun gcc to link with -lpython3
            $(`grep 'gcc -shared' build.log | tail -n 1 | sed 's/-lpython[0-9]\{3\}/-lpython3/'`)
            rm build.log

            # Copy _libcurl_cffi.pyd to wheel
            cd build/lib.win-amd64-*/
            7z a -r ../../dist/pymcurl-*.whl _libcurl_cffi.pyd
            cd ../..
        else
            $PY -m build -w . -C="--build-option=bdist_wheel" -C="--build-option=--py-limited-api" -C="--build-option=cp32"
        fi

        if [ "$OS" = "linux" ] || [ "$OS" = "windows" ]; then
            # Get all "lib" directories under $TMP/mcurl/$KEY/*/
            LIB_DIRS=`$PY -m jbb -d $TMP/mcurl/$KEY -q LibCURL`
        fi
        
        if [ "$OS" = "linux" ]; then
            $PY -m pip install auditwheel
            LD_LIBRARY_PATH=$LIB_DIRS $PY -m auditwheel repair -w wheel/$KEY dist/*.whl
        elif [ "$OS" = "windows" ]; then
            $PY -m pip install delvewheel
            $PY -m delvewheel repair --add-path $LIB_DIRS -w wheel/$KEY dist/*.whl
        elif [ "$OS" = "darwin" ]; then
            $PY -m pip install delocate
            delocate-wheel -w wheel/$KEY dist/*.whl
        fi

        rm -rf build pymcurl.egg-info dist
    elif [ "$1" = "--test" ]; then
        if [ -z $HTTPBIN ]; then
            echo "HTTPBIN not set"
            exit
        fi
        $PY -m pip install tox
        $PY -m tox --installpkg wheel/$KEY/pymcurl-*.whl --workdir $TMP/mcurl
    fi
elif [ "$OS" = "linux" ]; then
    # Check if httpbin_bridge network is up and httpbin docker container is running
    if ! docker network ls | grep httpbin_bridge || ! docker ps | grep -q httpbin; then
        echo "httpbin docker container on httpbin_bridge network not found"
        echo "Please start from tests/httpbin"
        exit 1
    fi

    # Enable binfmt_misc for cross-compiling
    docker run --privileged --rm tonistiigi/binfmt --install all

    # Run builds and tests in containers
    for arch in x86_64 i686 aarch64; do
        for abi in musllinux_1_1_ manylinux2014_; do
            if [ "$abi$arch" = "musllinux_1_1_i686" ]; then
                continue
            fi

            # Set PY to cp312
            docker run -it --rm -w /mcurl -v `pwd`:/mcurl --network httpbin_bridge -e PUID=`id -u` -e PGID=`id -g` \
                -e PY=/opt/python/cp312-cp312/bin/python3 -e OS=$OS -e TMP=$TMP -e HTTPBIN=all:httpbin \
                quay.io/pypa/$abi$arch /mcurl/build.sh $1
        done
    done
fi
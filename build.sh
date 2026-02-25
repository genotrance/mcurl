#!/bin/bash

set -e

WHEELHOUSE="./wheelhouse"
FORCE=0

if [ "$1" == "--force" ]; then
    FORCE=1
    echo "Force mode enabled - will rebuild all wheels"
fi

if [ $FORCE -eq 1 ]; then
    echo "Deleting existing wheels..."
    rm -rf "$WHEELHOUSE"
fi

mkdir -p "$WHEELHOUSE"

echo "Setting up binfmt for cross-platform builds..."
docker run --privileged --rm tonistiigi/binfmt --install all

ARCHS=("x86_64" "i686" "aarch64")

# Check if tmux session 'main' exists, if not create it
if ! tmux has-session -t main 2>/dev/null; then
    echo "Creating new tmux session 'main'..."
    tmux new-session -d -s main -n "build-setup"
fi

for ARCH in "${ARCHS[@]}"; do
    echo ""
    echo "========================================="
    echo "Building for architecture: $ARCH"
    echo "========================================="

    EXISTING_WHEELS=$(find "$WHEELHOUSE" -name "*${ARCH}.whl" 2>/dev/null | wc -l)

    if [ $EXISTING_WHEELS -gt 0 ] && [ $FORCE -eq 0 ]; then
        echo "Skipping $ARCH - found $EXISTING_WHEELS existing wheel(s)"
        continue
    fi

    echo "Starting build for $ARCH in tmux window..."

    # Create a new window in the 'main' session for this architecture
    tmux new-window -t main -n "build-$ARCH" "cd $(pwd) && CIBW_ARCHS=$ARCH cibuildwheel --platform linux; echo 'Build for $ARCH complete. Press enter to close.'; read"
done

echo ""
echo "========================================="
echo "All builds started in tmux session 'main'"
echo "========================================="
echo "Use 'tmux attach -t main' to view the builds"
echo "Use 'tmux list-windows -t main' to see all build windows"

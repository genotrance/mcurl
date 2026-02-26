#!/bin/bash

set -e

WHEELHOUSE="./wheelhouse"
FORCE=0
UPDATE_DOCS=0
ARCH_FILTER=""
VARIANT_FILTER=""

detect_latest_version() {
    echo "========================================="
    echo "Detecting latest LibCURL version..."
    echo "========================================="

    # Get latest release and extract base version
    LATEST_RELEASE=$(curl -s "https://api.github.com/repos/genotrance/LibCURL_jll.jl/releases/latest" | grep '"tag_name":' | sed -E 's/.*"tag_name":\s*"([^"]+)".*/\1/')
    BASE_VERSION=$(echo "$LATEST_RELEASE" | sed -E 's/^LibCURL-v([^+]+)\+.*/\1/')

    if [ -z "$BASE_VERSION" ] || ! [[ "$BASE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Warning: Failed to parse version from $LATEST_RELEASE"
        return 1
    fi
    echo "Latest LibCURL: $BASE_VERSION"

    # Get current version from latest git tag
    LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null | sed -E 's/^v//')
    if [ -z "$LATEST_TAG" ] || ! [[ "$LATEST_TAG" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Error: Invalid or missing git tag"
        return 1
    fi
    echo "Current tag: $LATEST_TAG"

    # Extract base and patch from current version
    CURRENT_BASE=$(echo "$LATEST_TAG" | sed -E 's/(.*)\.[0-9]+$/\1/')
    CURRENT_PATCH=$(echo "$LATEST_TAG" | sed -E 's/.*\.([0-9]+)$/\1/')

    # Determine new version
    if [ "$BASE_VERSION" != "$CURRENT_BASE" ]; then
        NEW_VERSION="${BASE_VERSION}.1"
        echo "Base changed: $CURRENT_BASE → $BASE_VERSION, setting version to $NEW_VERSION"
    elif ! git diff HEAD --quiet 2>/dev/null; then
        NEW_VERSION="${CURRENT_BASE}.$((CURRENT_PATCH + 1))"
        echo "Local changes detected, incrementing to $NEW_VERSION"
    else
        echo "✓ No version update needed"
        echo "========================================="
        return 0
    fi

    # Update pyproject.toml
    cp pyproject.toml pyproject.toml.backup
    sed -i.bak "s/^version = .*/version = \"$NEW_VERSION\"/" pyproject.toml

    # Verify update
    UPDATED=$(grep "^version" pyproject.toml | sed -E 's/.*=\s*"([^"]+)".*/\1/')
    if [ "$UPDATED" != "$NEW_VERSION" ] || ! [[ "$UPDATED" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Error: Version update failed"
        mv pyproject.toml.backup pyproject.toml
        return 1
    fi

    echo "✓ Version updated to $NEW_VERSION"
    rm -f pyproject.toml.backup pyproject.toml.bak
    echo "========================================="
    return 0
}

update_readme_docs() {
    echo "========================================="
    echo "Updating README.md with API documentation"
    echo "========================================="

    # Create temporary venv (replace if exists)
    echo "Creating temporary venv..."
    rm -rf /tmp/mcurl_docs_venv
    uv venv /tmp/mcurl_docs_venv
    source /tmp/mcurl_docs_venv/bin/activate

    # Install pymcurl from wheelhouse using uv
    echo "Installing pymcurl from wheelhouse..."
    uv pip install pymcurl -f "$WHEELHOUSE"

    # Generate help output
    echo "Generating help output..."
    python3 -c "import mcurl; help(mcurl)" > /tmp/mcurl_help_raw.txt

    # Filter out unwanted sections
    python3 << 'PYEOF' > /tmp/mcurl_help.txt
with open('/tmp/mcurl_help_raw.txt', 'r') as f:
    lines = f.readlines()

started = False
skip_section = False
skip_lines = 0

for i, line in enumerate(lines):
    # Skip lines if we're in a multi-line signature
    if skip_lines > 0:
        skip_lines -= 1
        continue

    # Start at NAME section
    if line.startswith('NAME'):
        started = True

    if not started:
        continue

    # Stop at DATA or FILE sections (we don't want these)
    if line.startswith('DATA') or line.startswith('FILE'):
        break

    # Skip PACKAGE CONTENTS section
    if line.startswith('PACKAGE CONTENTS'):
        skip_section = True
        continue

    # Skip Data descriptors/attributes sections
    if '----------------------------------------------------------------------' in line and i + 1 < len(lines):
        if 'Data descriptors' in lines[i+1] or 'Data and other attributes' in lines[i+1]:
            skip_section = True
            continue

    # Resume output at next major section or next function definition
    if skip_section:
        if line.strip() and (line.startswith('FUNCTIONS') or line.strip().startswith('class ')):
            skip_section = False
        else:
            continue

    # Collapse multi-line function signatures (Python 3.13+ formats them across multiple lines)
    if ' |  ' in line and '(' in line and line.rstrip().endswith('('):
        # Collect signature parts until closing paren
        sig_parts = [line.rstrip()]
        for j in range(i + 1, len(lines)):
            part = lines[j].strip().lstrip('|').strip()
            sig_parts.append(part)
            if ')' in lines[j]:
                # Join all parts into single line
                collapsed = sig_parts[0]
                for p in sig_parts[1:]:
                    if p and p != ')':
                        collapsed += ' ' + p if not collapsed.endswith('(') else p
                    elif p == ')':
                        collapsed += p
                print(collapsed)
                # Mark lines to skip
                skip_lines = len(sig_parts) - 1
                break
        continue

    print(line, end='')
PYEOF

    # Update README.md
    echo "Updating README.md..."
    HELP_CONTENT=$(cat /tmp/mcurl_help.txt)

    awk -v help="$HELP_CONTENT" '
/^### API reference$/ {
    print
    print ""
    print "```"
    print help
    print "```"
    skip = 1
    next
}
skip && /^```$/ {
    next
}
skip && /^## Building$/ {
    skip = 0
}
!skip {
    print
}
' README.md > README.md.tmp

    mv README.md.tmp README.md
    echo "✓ README.md updated"

    # Update LICENSE.txt copyright year
    CURRENT_YEAR=$(date +%Y)
    if grep -q "Copyright (c) 2024-" LICENSE.txt; then
        sed -i "s/Copyright (c) 2024-[0-9]\{4\}/Copyright (c) 2024-$CURRENT_YEAR/" LICENSE.txt
        echo "✓ LICENSE.txt updated to $CURRENT_YEAR"
    fi

    # Cleanup
    deactivate
    rm -rf /tmp/mcurl_docs_venv /tmp/mcurl_help_raw.txt /tmp/mcurl_help.txt
    echo "========================================="
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --force)
            FORCE=1
            shift
            ;;
        --docs)
            UPDATE_DOCS=1
            shift
            ;;
        --arch)
            ARCH_FILTER="$2"
            shift 2
            ;;
        --variant)
            VARIANT_FILTER="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--force] [--docs] [--arch ARCH1,ARCH2,...] [--variant manylinux|musllinux|manylinux,musllinux]"
            echo "  --force: Rebuild all wheels even if they exist"
            echo "  --docs: Only update documentation and exit"
            echo "  --arch: Comma-separated list of architectures (x86_64, i686, aarch64)"
            echo "  --variant: Comma-separated list of Linux variants (manylinux, musllinux)"
            exit 1
            ;;
    esac
done

# If --docs is specified, only update docs and exit
if [ $UPDATE_DOCS -eq 1 ]; then
    update_readme_docs
    exit 0
fi

# Run version detection
detect_latest_version

if [ $FORCE -eq 1 ]; then
    echo "Force mode enabled - will rebuild all wheels"
    echo "Deleting existing wheels..."
    rm -rf "$WHEELHOUSE"
fi

mkdir -p "$WHEELHOUSE"

echo "Setting up binfmt for cross-platform builds..."
docker run --privileged --rm tonistiigi/binfmt --install all

# Parse architecture filter
if [ -n "$ARCH_FILTER" ]; then
    IFS=',' read -ra ARCHS <<< "$ARCH_FILTER"
    echo "Building for specified architectures: ${ARCHS[*]}"
else
    ARCHS=("x86_64" "i686" "aarch64")
fi

# Parse variant filter and set CIBW_BUILD
if [ -n "$VARIANT_FILTER" ]; then
    BUILD_PATTERNS=()
    IFS=',' read -ra VARIANTS <<< "$VARIANT_FILTER"
    for VARIANT in "${VARIANTS[@]}"; do
        case "$VARIANT" in
            manylinux)
                BUILD_PATTERNS+=("*-manylinux_*")
                ;;
            musllinux)
                BUILD_PATTERNS+=("*-musllinux_*")
                ;;
            *)
                echo "Error: Unknown variant '$VARIANT'. Use 'manylinux' or 'musllinux'"
                exit 1
                ;;
        esac
    done
    # Join patterns with space for CIBW_BUILD
    CIBW_BUILD=$(IFS=' '; echo "${BUILD_PATTERNS[*]}")
    export CIBW_BUILD
    echo "Building for variants: ${VARIANTS[*]}"
    echo "CIBW_BUILD pattern: $CIBW_BUILD"
fi

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
    if [ -n "$CIBW_BUILD" ]; then
        tmux new-window -t main -n "build-$ARCH" "cd $(pwd) && CIBW_ARCHS=$ARCH CIBW_BUILD='$CIBW_BUILD' cibuildwheel --platform linux; echo 'Build for $ARCH complete. Press enter to close.'; read"
    else
        tmux new-window -t main -n "build-$ARCH" "cd $(pwd) && CIBW_ARCHS=$ARCH cibuildwheel --platform linux; echo 'Build for $ARCH complete. Press enter to close.'; read"
    fi
done

echo ""
echo "========================================="
echo "All builds started in tmux session 'main'"
echo "========================================="
echo "Use 'tmux attach -t main' to view the builds"
echo "Use 'tmux list-windows -t main' to see all build windows"

#!/usr/bin/env bash
# Ensure Homebrew curl equals the libcurl base version declared in pyproject.toml.
# Avoids a broad `brew update`: the installed version is checked first, the current
# formula version is read from the Homebrew formula API, and curl is upgraded or
# installed only when the installed version is missing or differs. The final
# assertion fails rather than letting a build or test use a mismatched libcurl.
#
# Release mode (--release or BREW_CURL_RELEASE=1, used only by build.yml) also
# refreshes the API metadata and upgrades curl's installed dependencies when they
# are outdated, so a release wheel never bundles stale openssl/nghttp2/etc from
# the runner image even when curl itself is already the expected version.
set -euo pipefail

RELEASE=${BREW_CURL_RELEASE:-0}
[ "${1:-}" = "--release" ] && RELEASE=1

export HOMEBREW_NO_AUTO_UPDATE=1
export HOMEBREW_NO_INSTALL_CLEANUP=1
export HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1
export HOMEBREW_NO_ENV_HINTS=1

EXPECTED=$(python3 -c "import pathlib, re; m = re.search(r'^version = \"([0-9]+\.[0-9]+\.[0-9]+)\.', pathlib.Path('pyproject.toml').read_text(), re.M); print(m.group(1))")

installed() {
  "$(brew --prefix)/opt/curl/bin/curl" --version 2>/dev/null | sed -n '1s/curl \([^ ]*\).*/\1/p' || true
}

INSTALLED=$(installed)
echo "Expected curl ${EXPECTED}, installed ${INSTALLED:-none}"

if [ "$INSTALLED" != "$EXPECTED" ]; then
  LATEST=$(curl -fsSL --retry 3 https://formulae.brew.sh/api/formula/curl.json \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["versions"]["stable"])' 2>/dev/null) || LATEST=""
  if [ -z "$LATEST" ]; then
    echo "::error::Could not read the curl version from the Homebrew formula API"
    exit 1
  fi
  echo "Homebrew formula API reports curl ${LATEST}"
  if [ "$LATEST" != "$EXPECTED" ]; then
    echo "::error::Homebrew curl ${LATEST} does not match pyproject libcurl ${EXPECTED}"
    exit 1
  fi
  # Refresh the formula API metadata (this still fetches Homebrew/brew itself but
  # skips the core tap git update in API mode), then change curl alone.
  brew update --auto-update || brew update
  REFRESHED=1
  if [ -n "$INSTALLED" ]; then
    # A dependency-link warning can make a successful upgrade exit nonzero; the
    # assertion below is the authority.
    brew upgrade curl || true
  else
    brew install curl || true
  fi
  INSTALLED=$(installed)
fi

if [ "$RELEASE" = 1 ]; then
  [ "${REFRESHED:-0}" = 1 ] || brew update --auto-update || brew update
  # Upgrade curl's installed dependencies when outdated (curl itself is asserted below).
  # shellcheck disable=SC2046
  OUTDATED=$(brew outdated --formula --quiet $(brew deps --installed curl || true) 2>/dev/null || true)
  if [ -n "$OUTDATED" ]; then
    echo "Upgrading outdated curl dependencies: $(echo $OUTDATED)"
    # shellcheck disable=SC2086
    brew upgrade $OUTDATED || true
  fi
  INSTALLED=$(installed)
fi

test "$INSTALLED" = "$EXPECTED" || {
  echo "::error::Homebrew curl ${INSTALLED:-none} does not match pyproject libcurl ${EXPECTED}"
  exit 1
}
echo "Homebrew curl ${INSTALLED} matches pyproject"

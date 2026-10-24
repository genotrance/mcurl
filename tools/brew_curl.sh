#!/usr/bin/env bash
# Ensure Homebrew curl equals the libcurl base version declared in pyproject.toml.
# Avoids a broad `brew update`: the installed version is checked first, the current
# formula version is read from the Homebrew formula API, and curl is upgraded or
# installed only when the installed version is missing or differs. The final
# assertion fails rather than letting a build or test use a mismatched libcurl.
set -euo pipefail

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
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["versions"]["stable"])')
  echo "Homebrew formula API reports curl ${LATEST}"
  if [ "$LATEST" != "$EXPECTED" ]; then
    echo "::error::Homebrew curl ${LATEST} does not match pyproject libcurl ${EXPECTED}"
    exit 1
  fi
  # Refresh only the formula API metadata (no git tap update), then change curl alone.
  brew update --auto-update || brew update
  if [ -n "$INSTALLED" ]; then
    # A dependency-link warning can make a successful upgrade exit nonzero; the
    # assertion below is the authority.
    brew upgrade curl || true
  else
    brew install curl || true
  fi
  INSTALLED=$(installed)
fi

test "$INSTALLED" = "$EXPECTED" || {
  echo "::error::Homebrew curl ${INSTALLED:-none} does not match pyproject libcurl ${EXPECTED}"
  exit 1
}
echo "Homebrew curl ${INSTALLED} matches pyproject"

#!/usr/bin/env python3
"""Calculate the upstream-update proposal shared by the workflow and tests."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass

UPSTREAM_TAG_RE = re.compile(r"^LibCURL-v(?P<base>\d+\.\d+\.\d+)\+.+$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class ReleaseDecision:
    current_version: str
    upstream_base: str
    target_version: str

    @property
    def bump_required(self) -> bool:
        return self.current_version != self.target_version

    @property
    def proposal_needed(self) -> bool:
        """Only a new libcurl base needs an upstream update pull request."""
        return self.bump_required


def parse_upstream_tag(tag: str) -> str:
    """Return the libcurl base version from a LibCURL_jll release tag."""
    match = UPSTREAM_TAG_RE.fullmatch(tag)
    if match is None:
        msg = f"invalid LibCURL_jll release tag: {tag!r}"
        raise ValueError(msg)
    return match.group("base")


def decide_release(
    current_version: str,
    upstream_tag: str,
) -> ReleaseDecision:
    """Determine whether devel needs a proposed libcurl version bump."""
    if VERSION_RE.fullmatch(current_version) is None:
        msg = f"invalid pymcurl version: {current_version!r}"
        raise ValueError(msg)

    upstream_base = parse_upstream_tag(upstream_tag)
    target_version = current_version if current_version.rsplit(".", 1)[0] == upstream_base else f"{upstream_base}.1"
    return ReleaseDecision(
        current_version=current_version,
        upstream_base=upstream_base,
        target_version=target_version,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", required=True, help="Version from pyproject.toml")
    parser.add_argument("--upstream-tag", required=True, help="Latest LibCURL_jll release tag")
    args = parser.parse_args()

    decision = decide_release(args.current, args.upstream_tag)
    # This is deliberately GITHUB_OUTPUT-compatible. Values are validated
    # versions or fixed words, so the workflow can append it directly.
    print(f"base={decision.upstream_base}")
    print(f"target={decision.target_version}")
    print(f"bump={'true' if decision.bump_required else 'false'}")
    print(f"proposal_needed={'true' if decision.proposal_needed else 'false'}")


if __name__ == "__main__":
    main()

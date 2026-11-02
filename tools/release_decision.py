#!/usr/bin/env python3
"""Calculate the release state shared by the weekly release workflow and tests."""

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
    tag_exists: bool
    pypi_exists: bool | None

    @property
    def bump_required(self) -> bool:
        return self.current_version != self.target_version

    @property
    def release_needed(self) -> bool:
        # A tag is the durable completion marker: build.yml creates it only
        # after PyPI succeeds, so it is safe to retry every earlier failure.
        return not self.tag_exists

    @property
    def state(self) -> str:
        if self.tag_exists and self.pypi_exists is False:
            return "tag_exists_pypi_missing"
        if self.tag_exists:
            return "released"
        if self.pypi_exists is True:
            return "pypi_exists_tag_missing"
        return "release_needed"


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
    *,
    tag_exists: bool,
    pypi_exists: bool | None = None,
) -> ReleaseDecision:
    """Determine whether main needs a committed bump and release build."""
    if VERSION_RE.fullmatch(current_version) is None:
        msg = f"invalid pymcurl version: {current_version!r}"
        raise ValueError(msg)

    upstream_base = parse_upstream_tag(upstream_tag)
    target_version = current_version if current_version.rsplit(".", 1)[0] == upstream_base else f"{upstream_base}.1"
    return ReleaseDecision(
        current_version=current_version,
        upstream_base=upstream_base,
        target_version=target_version,
        tag_exists=tag_exists,
        pypi_exists=pypi_exists,
    )


def parse_optional_bool(value: str) -> bool | None:
    if value == "unknown":
        return None
    if value in {"true", "false"}:
        return value == "true"
    msg = f"expected true, false, or unknown, got {value!r}"
    raise argparse.ArgumentTypeError(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", required=True, help="Version from pyproject.toml")
    parser.add_argument("--upstream-tag", required=True, help="Latest LibCURL_jll release tag")
    parser.add_argument("--tag-exists", choices=("true", "false"), required=True)
    parser.add_argument("--pypi-exists", default="unknown", type=parse_optional_bool)
    args = parser.parse_args()

    decision = decide_release(
        args.current,
        args.upstream_tag,
        tag_exists=args.tag_exists == "true",
        pypi_exists=args.pypi_exists,
    )
    # This is deliberately GITHUB_OUTPUT-compatible. Values are validated
    # versions or fixed words, so the workflow can append it directly.
    print(f"base={decision.upstream_base}")
    print(f"target={decision.target_version}")
    print(f"bump={'true' if decision.bump_required else 'false'}")
    print(f"release_needed={'true' if decision.release_needed else 'false'}")
    print(f"state={decision.state}")


if __name__ == "__main__":
    main()

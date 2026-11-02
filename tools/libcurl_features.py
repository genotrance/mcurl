#!/usr/bin/env python3
"""Compare libcurl ``curl-config --features`` output between two releases."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

# px uses these for proxy authentication, TLS, proxying, name handling, and
# HTTP/2. A removal needs review before a devel-to-main release merge.
PX_REQUIRED_FEATURES = frozenset({
    "NTLM",
    "SPNEGO",
    "GSS-API",
    "Kerberos",
    "SSPI",
    "SSL",
    "HTTPS-proxy",
    "Unicode",
    "AsynchDNS",
    "HTTP2",
})


@dataclass(frozen=True)
class PlatformFeatureChange:
    """Feature differences for one LibCURL_jll platform triplet."""

    triplet: str
    removed: tuple[str, ...]
    added: tuple[str, ...]

    @property
    def px_relevant_removed(self) -> tuple[str, ...]:
        return tuple(feature for feature in self.removed if feature in PX_REQUIRED_FEATURES)


def compare_feature_sets(
    before: Mapping[str, Iterable[str]], after: Mapping[str, Iterable[str]]
) -> tuple[PlatformFeatureChange, ...]:
    """Return sorted per-triplet changes for triplets present in both releases."""
    changes = []
    for triplet in sorted(set(before) & set(after)):
        before_features = set(before[triplet])
        after_features = set(after[triplet])
        changes.append(
            PlatformFeatureChange(
                triplet=triplet,
                removed=tuple(sorted(before_features - after_features)),
                added=tuple(sorted(after_features - before_features)),
            )
        )
    return tuple(changes)


def render_markdown(changes: Iterable[PlatformFeatureChange]) -> str:
    """Render a pull-request-ready feature comparison with an urgent warning."""
    changes = tuple(changes)
    impacted = [change for change in changes if change.px_relevant_removed]
    sections: list[str] = []
    if impacted:
        lines = [
            "## :warning: px feature compatibility",
            "",
            "The following libcurl features used by px were removed and require review before release:",
        ]
        lines.extend(
            f"- `{change.triplet}`: {', '.join(f'`{feature}`' for feature in change.px_relevant_removed)}"
            for change in impacted
        )
        sections.append("\n".join(lines))

    lines = ["## Libcurl feature comparison", ""]
    if not changes:
        lines.append("Could not compare: the two releases have no matching platform triplets.")
    else:
        for change in changes:
            lines.extend([
                f"### `{change.triplet}`",
                f"- Removed: {', '.join(change.removed) if change.removed else 'none'}",
                f"- Added: {', '.join(change.added) if change.added else 'none'}",
                "",
            ])
    sections.append("\n".join(lines).rstrip())
    return "\n\n".join(sections)


def read_feature_file(path: Path) -> dict[str, tuple[str, ...]]:
    """Read tab-separated ``triplet<TAB>feature ...`` records."""
    result = {}
    for line in path.read_text().splitlines():
        triplet, separator, features = line.partition("\t")
        if not separator or not triplet:
            msg = f"invalid feature record in {path}: {line!r}"
            raise ValueError(msg)
        result[triplet] = tuple(features.split())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-file", type=Path, required=True)
    parser.add_argument("--after-file", type=Path, required=True)
    parser.add_argument("--warnings", action="store_true", help="Print workflow warning lines instead of Markdown")
    args = parser.parse_args()

    changes = compare_feature_sets(read_feature_file(args.before_file), read_feature_file(args.after_file))
    if args.warnings:
        for change in changes:
            if change.px_relevant_removed:
                print(f"px-relevant features removed on {change.triplet}: " + ", ".join(change.px_relevant_removed))
    else:
        print(render_markdown(changes))


if __name__ == "__main__":
    main()

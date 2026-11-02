"""Tests for the LibCURL_jll feature-difference pull-request report."""

import importlib.util
import os
import sys

import pytest

_spec = importlib.util.spec_from_file_location(
    "libcurl_features", os.path.join(os.path.dirname(__file__), "..", "tools", "libcurl_features.py")
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
compare_feature_sets = _module.compare_feature_sets
read_feature_file = _module.read_feature_file
render_markdown = _module.render_markdown


def test_comparison_flags_removed_px_features_at_the_top() -> None:
    changes = compare_feature_sets(
        {"x86_64-linux-gnu": ("SSL", "NTLM", "HTTP2"), "unused": ("SSL",)},
        {"x86_64-linux-gnu": ("SSL", "HTTP2", "IPv6"), "new": ("SSL",)},
    )

    report = render_markdown(changes)

    assert changes[0].removed == ("NTLM",)
    assert changes[0].added == ("IPv6",)
    assert report.startswith("## :warning: px feature compatibility")
    assert "`NTLM`" in report
    assert "- Added: IPv6" in report


def test_comparison_is_sorted_and_reports_non_px_removals_without_warning() -> None:
    changes = compare_feature_sets(
        {"z": ("zlib", "SSL"), "a": ("HTTP2", "alt-svc")},
        {"z": ("SSL", "IPv6"), "a": ("HTTP2", "HSTS")},
    )

    report = render_markdown(changes)

    assert [change.triplet for change in changes] == ["a", "z"]
    assert not report.startswith("## :warning:")
    assert "- Removed: alt-svc" in report
    assert "- Removed: zlib" in report


def test_read_feature_file_rejects_invalid_records(tmp_path) -> None:
    features = tmp_path / "features.txt"
    features.write_text("x86_64-linux-gnu\tSSL HTTP2\n")
    assert read_feature_file(features) == {"x86_64-linux-gnu": ("SSL", "HTTP2")}

    features.write_text("missing-tab\n")
    with pytest.raises(ValueError, match="invalid feature record"):
        read_feature_file(features)

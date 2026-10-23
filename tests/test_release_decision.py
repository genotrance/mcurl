"""Regression cases for the state machine used by the release workflow."""

import pytest

from tools.release_decision import decide_release


def test_same_version_already_released() -> None:
    decision = decide_release(
        "8.23.0.1",
        "LibCURL-v8.23.0+1",
        tag_exists=True,
        pypi_exists=True,
    )

    assert decision.target_version == "8.23.0.1"
    assert not decision.bump_required
    assert not decision.release_needed
    assert decision.state == "released"


def test_new_upstream_base_needs_bump_and_release() -> None:
    decision = decide_release(
        "8.23.0.1",
        "LibCURL-v8.24.0+1",
        tag_exists=False,
        pypi_exists=False,
    )

    assert decision.target_version == "8.24.0.1"
    assert decision.bump_required
    assert decision.release_needed


def test_upstream_suffix_only_bump_does_not_create_new_pymcurl_version() -> None:
    decision = decide_release(
        "8.23.0.1",
        "LibCURL-v8.23.0+2",
        tag_exists=True,
        pypi_exists=True,
    )

    assert decision.target_version == "8.23.0.1"
    assert not decision.bump_required
    assert not decision.release_needed


def test_existing_tag_is_the_completion_marker_when_pypi_is_missing() -> None:
    decision = decide_release(
        "8.23.0.1",
        "LibCURL-v8.23.0+1",
        tag_exists=True,
        pypi_exists=False,
    )

    assert not decision.release_needed
    assert decision.state == "tag_exists_pypi_missing"


def test_pypi_version_without_tag_retries_tag_creation() -> None:
    decision = decide_release(
        "8.23.0.1",
        "LibCURL-v8.23.0+1",
        tag_exists=False,
        pypi_exists=True,
    )

    assert decision.release_needed
    assert not decision.bump_required
    assert decision.state == "pypi_exists_tag_missing"


@pytest.mark.parametrize(
    "current, upstream_tag",
    [
        ("8.23.0", "LibCURL-v8.23.0+1"),
        ("8.23.0.1", "libcurl-8.23.0"),
    ],
)
def test_invalid_versions_are_rejected(current: str, upstream_tag: str) -> None:
    with pytest.raises(ValueError):
        decide_release(current, upstream_tag, tag_exists=False)

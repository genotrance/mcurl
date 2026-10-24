"""The fast, full and smoke selections partition the suite as documented in docs/testing.md."""

import os
import subprocess
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)


def _collect(*args):
    out = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            TESTS,
            "--collect-only",
            "-q",
            "--strict-markers",
            "-p",
            "no:cacheprovider",
            *args,
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    return {line for line in out.stdout.splitlines() if "::" in line}


def test_slow_and_fast_partition_the_full_run():
    full, slow, fast = _collect(), _collect("-m", "slow"), _collect("-m", "not slow")
    assert slow, "no tests are marked slow"
    assert not slow & fast
    assert slow | fast == full


def test_burst_suite_is_slow():
    slow = _collect("-m", "slow")
    burst = {n for n in _collect() if n.startswith("tests/test_auth_retry_burst.py::")}
    assert burst and burst <= slow
    assert any(n.startswith("tests/test_dead_connection.py::") for n in slow)


def test_smoke_selection_is_short_and_fast():
    smoke, slow = _collect("-m", "smoke"), _collect("-m", "slow")
    assert smoke
    assert not smoke & slow
    assert {n.split("::")[0] for n in smoke} == {"tests/test_smoke.py"}
    assert len(smoke) < len(_collect()) // 10

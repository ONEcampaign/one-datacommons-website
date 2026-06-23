"""Verify that importing qre does not transitively import qre.eval.

This is the env-independent invariant: even when the eval extra IS installed,
``import qre`` must not pull in ``qre.eval`` as a side effect.
"""
import subprocess
import sys


def test_core_import_does_not_pull_in_eval():
    code = (
        "import sys, qre; "
        "assert 'qre.eval' not in sys.modules, "
        "'qre.eval was imported transitively from qre'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Core import isolation check failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

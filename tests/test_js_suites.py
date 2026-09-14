"""The interface's own test suites, run as part of the Python gate.

`pytest` collected Python only, so the sixteen node suites under `tests/` were
run by hand, one at a time, and two of them had been failing unnoticed - long
enough that the breakage was invisible in six consecutive commits. Both were
the same shape: a test reaches into `web/shell.html` by matching a literal
string, the shell moved, and `String.replace` quietly returned the string
unchanged rather than raising.

A suite nobody runs is not a gate. This makes one command run everything, so a
JS suite that rots shows up in the same place as a Python one that does.

The whole set costs about ten seconds. The one suite that talks to the live
Supabase project is opt-in: it writes rows to the real database and cleans up
after itself, which is fine to do deliberately and not fine to do on every
routine test run. Set SOPP_LIVE=1 to include it. It is skipped loudly rather
than silently so that it cannot rot the way these two did.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

# Suites that reach the live Supabase project rather than a stub.
LIVE = {"test_sync_e2e.js"}

SUITES = sorted(p.name for p in (ROOT / "tests").glob("test_*.js"))


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("suite", SUITES)
def test_js_suite_passes(suite: str):
    if suite in LIVE:
        if not (ROOT / "config" / "supabase.json").exists():
            pytest.skip("needs config/supabase.json")
        if os.environ.get("SOPP_LIVE") != "1":
            pytest.skip("writes to the live project; set SOPP_LIVE=1 to run it")

    out = subprocess.run([NODE, str(ROOT / "tests" / suite)], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8",
                         timeout=300)
    if out.returncode != 0:
        tail = (out.stdout or "")[-2500:] + (out.stderr or "")[-2500:]
        raise AssertionError(f"{suite} failed (exit {out.returncode}):\n{tail}")


def test_every_js_suite_is_accounted_for():
    """A new suite dropped into tests/ is picked up without being registered.

    The point of the module is that nothing runs by hand any more, so an empty
    or shrinking collection is itself the failure worth catching.
    """
    assert len(SUITES) >= 16, f"only found {len(SUITES)} JS suites: {SUITES}"
    assert LIVE <= set(SUITES), f"LIVE names a suite that no longer exists: {LIVE}"

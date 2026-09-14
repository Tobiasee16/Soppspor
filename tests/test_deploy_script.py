"""deploy.sh must not publish the wrong map.

There are two hosted builds and they look identical from outside: same name,
same page, same icons. One covers Norway from tiles; the other covers
Kristiansand and nothing else. `deploy.sh` used to run `build_hosted.py` with
no flag and push `dist/` unconditionally, so asking it to publish gave testers
in Oslo a country with no zones in it and no error explaining why - the one
failure that costs a whole round of testing rather than a bug report.

These are static checks on the script. Running it for real would refetch the
weather and rebuild 190 MB, so what is pinned here is the wiring: which build
is the default, that the directory is chosen once and then used everywhere, and
that no step has a hardcoded `dist` that would disagree with it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SH = ROOT / "deploy.sh"


@pytest.fixture(scope="module")
def script() -> str:
    assert SH.exists(), "deploy.sh is gone"
    return SH.read_text(encoding="utf-8")


def test_norway_is_the_default(script):
    assert re.search(r'BUILD="\$\{SOPP_BUILD:-norge\}"', script), \
        "the default build is no longer Norway"


def test_the_two_builds_map_to_the_right_flag_and_directory(script):
    norge = re.search(r"norge\)\s*\n\s*FLAG=\((--national)\); OUT=\"(dist_norge)\"", script)
    assert norge, "the norge case no longer passes --national and dist_norge"
    kommune = re.search(r"kommune\)\s*\n\s*FLAG=\(\);\s*OUT=\"(dist)\"", script)
    assert kommune, "the kommune case no longer selects dist"


def test_an_unknown_build_name_stops_rather_than_guesses(script):
    assert re.search(r"SOPP_BUILD must be", script), \
        "an unrecognised SOPP_BUILD should fail, not fall through to a default"


@pytest.mark.parametrize("step", ["--project-name", "--dir"])
def test_no_publish_step_hardcodes_a_directory(script, step):
    """The whole bug in one line: `wrangler pages deploy dist`."""
    for line in script.splitlines():
        if step in line and ("wrangler" in line or "netlify" in line):
            assert '"$OUT"' in line, f"{line.strip()!r} does not publish $OUT"


def test_the_bundle_check_runs_against_what_was_built(script):
    assert 'node tests/test_hosted_build.js "$OUT"' in script, \
        "the bundle check would validate a directory other than the one built"


def test_it_says_which_ground_the_build_covers_before_publishing(script):
    """A build that says Kristiansand when you meant Norway announces itself
    nowhere else, so the script reads the area label out of the payload."""
    assert "area_label" in script
    i, j = script.index("area_label"), script.index("--cloudflare)")
    assert i < j, "the area is reported after the deploy rather than before it"


def test_the_checker_accepts_a_directory(script):
    """The script passes one; the checker has to take it."""
    js = (ROOT / "tests" / "test_hosted_build.js").read_text(encoding="utf-8")
    assert "process.argv[2]" in js

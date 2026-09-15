"""The conditions layer's wiring: the build, the route and the schedule.

These three were written together and can drift apart silently, which is the
whole reason this file exists. The route reads a key out of KV, the workflow
writes one, and the build decides whether to ship a file that would make both
of them irrelevant. Nothing at runtime complains if they disagree - the app
treats a missing conditions layer as a normal state and draws the map without
it, so the failure looks exactly like weather nobody has fetched yet.

Precedence runs the other way from how this was first written: Cloudflare Pages
routes a request to the **function** before the static asset. That mistake was
deployed - the route's old "no binding" case returned 503 and took the live
conditions layer down - and the fix was to make the route fall through to the
asset server whenever KV cannot answer. So shipping both is now harmless, and
`SOPP_CONDITIONS` is a question of what to ship rather than of whether the app
works. See FINDINGS 26c.
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
FUNC = ROOT / "functions" / "conditions.json.js"
FLOW = ROOT / ".github" / "workflows" / "conditions.yml"
BUILD = ROOT / "scripts" / "build_hosted.py"


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert FLOW.exists(), "the scheduled conditions refresh is gone"
    return yaml.safe_load(FLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def func_src() -> str:
    assert FUNC.exists(), "the conditions route is gone"
    return FUNC.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# the route

def test_the_filename_is_the_route():
    """Pages derives the path from the filename, so renaming this file moves
    the endpoint and nothing else mentions the new address."""
    assert FUNC.name == "conditions.json.js", (
        f"the route is served at /{FUNC.name[:-3]}, but the app fetches "
        '"conditions.json"')


def test_the_route_is_an_es_module_with_both_handlers(func_src):
    """Pages Functions must be ESM. A CommonJS file compiles and exports
    nothing, which deploys cleanly and 404s on every request."""
    assert "export async function onRequestGet" in func_src
    assert "export async function onRequestHead" in func_src, (
        "HEAD is how the scheduled job reads the served day; without a handler "
        "it falls through to the static asset router and 404s while GET is 200")


# --------------------------------------------------------------------------
# the build must not shadow the route

def _kv_branch() -> ast.If:
    """The `if national and CONDITIONS_FROM_KV:` branch, as a syntax tree."""
    tree = ast.parse(BUILD.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if {"national", "CONDITIONS_FROM_KV"} <= names:
            return node
    raise AssertionError(
        "build_hosted.py no longer has a branch for serving conditions from KV")


def test_the_kv_build_writes_no_conditions_file():
    """`SOPP_CONDITIONS=kv` means "KV is the source", so the build should not
    also ship 1.19 MB that the route will override on every request. Not a
    correctness requirement since the route learned to fall through - it is
    payload size, and a second copy of the weather that can silently disagree
    with the first."""
    branch = _kv_branch()
    # Only the branch body. `ast.walk` on an If also descends into its elif
    # chain, where the static build legitimately does copy the file - which is
    # what this test caught on its first run.
    calls = [n for stmt in branch.body for n in ast.walk(stmt)
             if isinstance(n, ast.Call)]
    assert calls, "the KV branch does nothing at all"
    for call in calls:
        name = ast.unparse(call.func)
        assert "copyfile" not in name and "write_text" not in name, (
            f"the KV branch calls {name}, which would ship a static file that "
            "shadows functions/conditions.json.js")


def test_the_static_build_still_writes_one():
    """With no KV namespace the route falls through to the asset server, so
    this file is the whole conditions layer. It is also the fallback whenever
    KV is empty or unreachable, which makes it load-bearing rather than
    legacy."""
    src = BUILD.read_text(encoding="utf-8")
    assert 'shutil.copyfile(NAT_COND, out / "conditions.json")' in src, (
        "the national build can no longer ship conditions.json at all, so "
        "SOPP_CONDITIONS=static has nothing to fall back to")


@pytest.mark.parametrize("value,expected", [
    (None, False), ("static", False), ("kv", True), ("KV", True), ("", False),
])
def test_the_switch_defaults_to_the_old_behaviour(value, expected, monkeypatch):
    """Default static, because the KV namespace has to exist and be populated
    before the file stops being written, not after."""
    if value is None:
        monkeypatch.delenv("SOPP_CONDITIONS", raising=False)
    else:
        monkeypatch.setenv("SOPP_CONDITIONS", value)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        mod = importlib.import_module("build_hosted")
        mod = importlib.reload(mod)
        assert mod.CONDITIONS_FROM_KV is expected
    finally:
        sys.path.remove(str(ROOT / "scripts"))


# --------------------------------------------------------------------------
# the workflow and the route have to agree

def _run_steps(workflow: dict) -> str:
    return "\n".join(s["run"] for s in workflow["jobs"]["refresh"]["steps"]
                     if "run" in s)


def test_the_workflow_writes_the_key_the_route_reads(workflow, func_src):
    """Two string literals in two files, and nothing fails if they differ -
    the route keeps answering 404 and the job keeps reporting a successful
    write."""
    runs = _run_steps(workflow)
    assert "kv key put norge" in runs, \
        "the workflow no longer writes the key 'norge'"
    assert 'getWithMetadata("norge"' in func_src, \
        "the route no longer reads the key 'norge'"


def test_the_workflow_reads_the_header_the_route_sets(workflow, func_src):
    """The job decides whether to write by reading this header off the live
    site. Renaming it in the route makes every run think nothing is served,
    which rewrites KV twice a day forever."""
    runs = _run_steps(workflow)
    assert "x-conditions-last-day" in func_src, \
        "the route no longer publishes the served day in a header"
    assert "x-conditions-last-day" in runs, \
        "the workflow no longer reads the served day from a header"


def test_the_workflow_fetches_the_national_grid(workflow):
    """`--national` is the whole point: without it this refreshes Kristiansand
    and writes it to the key the national map reads. That is the deploy.sh bug
    one layer down, and it would be just as invisible."""
    runs = _run_steps(workflow)
    assert "fetch_recent_weather.py --national" in runs, \
        "the scheduled refresh is not fetching the national grid"


def test_the_workflow_never_deploys_the_site(workflow):
    """The reason this design was chosen over rebuilding and republishing. A
    deploy step here puts 196 MB of tiles it does not have at risk."""
    runs = _run_steps(workflow)
    assert "pages deploy" not in runs, (
        "the conditions job now deploys the site; a bad run can take the map "
        "off the internet, which is exactly what serving from KV avoids")


def test_the_write_is_verified_against_what_is_served(workflow):
    """Written is not served: KV is eventually consistent and the route keeps
    a short edge cache. This project's standing habit is that a success code
    is not evidence."""
    names = [s.get("name", "") for s in workflow["jobs"]["refresh"]["steps"]]
    assert any("really being served" in n for n in names), \
        "nothing checks that the write actually reached a reader"


def test_it_runs_on_a_schedule(workflow):
    on = workflow.get("on") or workflow.get(True)   # YAML reads bare `on` as True
    assert "schedule" in on, "the conditions refresh is no longer scheduled"
    assert len(on["schedule"]) >= 1
    assert "workflow_dispatch" in on, \
        "there is no way to run it by hand when MET publishes late"

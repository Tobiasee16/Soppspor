"""The national build has to say true things, not just render.

A tiled build that works perfectly while the Om page describes a model fitted on
Agder and tested on Kristiansand is worse than one that does not build: the map
is right, the account of it is wrong, and nothing on screen looks amiss. That is
the failure this file is for.

Two halves.

* **The prose.** Every sentence in the markup that names a region or quotes a
  number is addressable by id, and the national build replaces it. The scan
  below walks the markup, finds each mention of Kristiansand or Agder, and
  requires it to sit inside an element the build actually overrides. Adding a
  new region-specific sentence without an id fails here rather than reaching a
  reader.
* **The page.** The real payload, the real tiles and the real shell, booted over
  a piece of Norway, because a payload that parses is not the same as a map that
  draws.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_hosted as bh

SHELL = ROOT / "web" / "shell.html"
TILES = ROOT / "data" / "processed" / "zone_tiles"
NODE = shutil.which("node")

needs_run = pytest.mark.skipif(
    not bh.NAT_META.exists(),
    reason="the national run has not been exported; see export_zone_tiles.py")

VOID = {"br", "hr", "img", "input", "meta", "link", "source"}
REGIONS = ("Kristiansand", "Agder", "kommunen", "kommune")

# Two elements the `about` map does not cover, each for its own reason, each
# pinned by a test below rather than taken on trust:
#
# * `coverNote` is markup the tiled build cannot reach. `draw` hands it only
#   null or "zoom", and "zoom" replaces the copy wholesale.
# * `pillMeta` is overwritten by `renderPill` at boot, from the payload's own
#   area label, before anything is on screen.
UNREACHABLE = {"coverNote", "pillMeta"}


def markup() -> str:
    """What a reader sees, as the build produces it: above the script, and
    without the shell's own <title>, which the hosted build strips."""
    text = bh.strip_shell_title(SHELL.read_text(encoding="utf-8"))
    return text[:text.index("<script>\n(function(){")]


def id_spans(html: str) -> dict[str, tuple[int, int]]:
    """`{id: (start, end)}` for every element with an id, by tag matching."""
    out: dict[str, tuple[int, int]] = {}
    for m in re.finditer(r'<(\w+)([^>]*\bid="([^"]+)"[^>]*)>', html):
        tag, eid = m.group(1).lower(), m.group(3)
        if tag in VOID or m.group(2).rstrip().endswith("/"):
            out[eid] = (m.start(), m.end())
            continue
        pat = re.compile(r"</?%s\b" % re.escape(tag))
        depth, i = 1, m.end()
        while depth:
            nxt = pat.search(html, i)
            if not nxt:
                raise AssertionError(f"unclosed <{tag} id={eid}>")
            depth += -1 if html[nxt.start() + 1] == "/" else 1
            i = nxt.end()
        out[eid] = (m.start(), i)
    return out


@pytest.fixture(scope="module")
def built():
    payload_js, meta, keys = bh.national_payload()
    payload = json.loads(payload_js[len("window.SOPP="):-1])
    return payload, meta, keys


@needs_run
class TestThePayload:
    def test_it_declares_tiles_rather_than_carrying_surfaces(self, built):
        payload, meta, _ = built
        assert meta["tiles"]["url"].endswith(".png")
        assert "{sp}" in meta["tiles"]["url"] and "{row}" in meta["tiles"]["url"]
        assert meta["percentile_bytes"] is True
        assert all("data" not in sp for sp in payload["species"])

    def test_every_species_carries_a_lookup_for_every_level(self, built):
        """One per level. The same blur over a coarser grid covers different
        ground, so a level read against another level's distribution would move
        every zone boundary with nothing reporting it."""
        payload, meta, _ = built
        n = len(meta["levels"])
        assert n >= 2, "the pyramid is missing; zooming out has nothing to show"
        for sp in payload["species"]:
            assert len(sp["smoothed_rank_lut"]) == n, sp["key"]
            assert all(len(l) == 256 for l in sp["smoothed_rank_lut"]), sp["key"]
            assert len(sp["tiles"]) == n and all(sp["tiles"]), sp["key"]

    def test_the_levels_get_coarser_and_smaller(self, built):
        _, meta, _ = built
        cells = [lv["cell_m"] for lv in meta["levels"]]
        assert cells == sorted(cells) and len(set(cells)) == len(cells)
        area = [lv["grid"]["w"] * lv["grid"]["h"] for lv in meta["levels"]]
        assert area == sorted(area, reverse=True)

    def test_the_coarsest_level_fits_the_whole_country_on_a_screen(self, built):
        """Otherwise zooming all the way out still shows nothing."""
        _, meta, _ = built
        top = meta["levels"][-1]
        assert top["grid"]["w"] * top["grid"]["h"] < 600_000

    def test_the_null_control_is_not_offered_as_a_mushroom(self, built):
        payload, _, keys = built
        assert not any(sp.get("is_null") for sp in payload["species"])
        assert not any("null" in k for k in keys)

    def test_the_registered_bar_is_applied_not_assumed(self, built):
        """Every shipped species clears the synthetic null on held-out ground."""
        payload, meta, _ = built
        null = meta["null_transfer_auc"]
        assert null is not None
        for sp in payload["species"]:
            lo = float(sp["transfer_ci"].split("-")[0])
            assert lo > null, f"{sp['key']} CI starts at {lo}, null is {null}"

    def test_it_names_where_it_was_tested(self, built):
        _, meta, _ = built
        assert meta["tested_on"] == "Nordland"
        assert meta["area_label"].startswith("Norge")


@needs_run
class TestTheProse:
    def test_the_numbers_come_from_the_reports(self, built):
        """Not typed. A refit changes the sentence without anyone remembering."""
        f = bh.national_facts()
        payload, _, _ = built
        about = payload["about"]
        assert f"{f['features']} tall" in about["ab_s3"]
        assert f"{f['cell_m']}&nbsp;m rute" in about["ab_s3"]
        assert bh.nb(f["fitted"]["Craterellus tubaeformis"]) in about["ab_s1"]
        assert bh.nb(f["pool_thinned"]) in about["ab_s2"]
        assert bh.tall(f["fylker"]) in about["ab_s4"]

    def test_a_layout_change_in_a_report_stops_the_build(self, tmp_path,
                                                        monkeypatch):
        bad = tmp_path / "model_results_norge.md"
        bad.write_text("# Model results\n\nno feature line here\n",
                       encoding="utf-8")
        monkeypatch.setattr(bh, "NAT_MODELS", bad)
        with pytest.raises(SystemExit):
            bh.national_facts()

    def test_every_region_specific_sentence_is_replaced(self, built):
        """The scan. A new mention without an id fails here, not on a phone."""
        payload, _, _ = built
        html = markup()
        covered = set(payload["about"]) | UNREACHABLE
        spans = id_spans(html)
        unknown = covered - set(spans)
        assert not unknown, f"the build overrides ids that do not exist: {unknown}"
        missed = []
        for word in REGIONS:
            for m in re.finditer(word, html):
                at = m.start()
                if not any(a <= at < b for eid, (a, b) in spans.items()
                           if eid in covered):
                    missed.append((word, html.count("\n", 0, at) + 1))
        assert not missed, (
            "region-specific copy with no id, at line(s): "
            + ", ".join(f"{w}:{ln}" for w, ln in missed))

    def test_the_note_really_is_unreachable_in_a_tiled_build(self):
        """First exception, pinned rather than believed."""
        shell = SHELL.read_text(encoding="utf-8")
        assert 'coverNote(TILED?ZT.note:(onScreen?null:"outside"))' in shell
        # Exactly one call site, so the guard above is the only way in.
        assert shell.count("coverNote(TILED") == 1
        assert len(re.findall(r"(?<![=\w])coverNote\(", shell)) == 1
        # ...and the only values it can be handed in a tiled build.
        assert 'ZT.note="zoom"' in shell
        assert "ZT.note=null" in shell

    def test_the_pill_is_overwritten_from_the_payload(self, built):
        """Second exception. The markup says Kristiansand; the boot does not."""
        _, meta, _ = built
        shell = SHELL.read_text(encoding="utf-8")
        assert 'var PILL_META=(D.meta&&D.meta.area_label)' in shell
        assert "meta.textContent=SEL.length===1?PILL_META" in shell
        # renderPill runs during boot, so the markup default is never read.
        assert re.search(r"\nrenderPill\(\);", shell)
        assert meta["area_label"] == "Norge · anslag"

    def test_the_page_describes_the_weather_it_actually_ships(self, built):
        """Whichever way round it is. A page that promises a rainfall layer it
        does not have, or omits one it does, is wrong in the same way."""
        import json as _json
        payload, _, _ = built
        about = payload["about"]
        has_cond = bh.NAT_COND.exists()
        if has_cond:
            cond = _json.loads(bh.NAT_COND.read_text(encoding="utf-8"))
            km = f"{cond['grid']['step'] / 1000:.0f} km"
            assert "seNorge" in about["ab_clocks"]
            assert km in about["ab_clocks"], about["ab_clocks"]
            assert km in about["ab_weak"]
            assert "regnet" in about["ab_lead"]
            assert "ikke med i denne utgaven" not in about["ab_clocks"]
            # It is a judgement laid over a validated surface, and the page has
            # to keep saying so wherever it appears.
            assert "ikke testet" in about["ab_clocks"]
        else:
            assert "ikke med i denne utgaven" in about["ab_clocks"]
            assert "regnet" not in about["ab_lead"]

    def test_the_context_layers_are_declared_if_they_ship(self, built):
        payload, _, _ = built
        if "ctx" not in payload:
            pytest.skip("no context tiles exported")
        keys = [L["key"] for L in payload["ctx"]["layers"]]
        assert "elevation" in keys and "ar50" in keys
        # The national model has no TWI, so neither can the card.
        assert "twi" not in keys
        assert "fuktighetsindeks" in payload["about"]["ab_layers"]
        assert "Uten fuktighetsindeks" in payload["about"]["ab_layers"]

    def test_it_says_which_ground_the_model_never_saw(self, built):
        """The national map is in-sample everywhere but Nordland, unlike Phase
        0's. That is a real weakness and the page has to carry it."""
        about = payload_about(built)
        assert "eneste delen av dette kartet modellen aldri har sett" in about["ab_s4"]
        assert "Bare Nordland er holdt unna" in about["ab_weak"]

    def test_the_ordering_result_is_the_national_one(self, built):
        """Agder's registered ordering failed and Norway's held. Both stand."""
        about = payload_about(built)
        assert "slo det til" in about["ab_order"]
        assert "ikke</b> til da modellen bare hadde" in about["ab_order"]
        assert "Det betyr ikke at modellen ble bedre" in about["ab_order"]


def payload_about(built):
    return built[0]["about"]


@needs_run
@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestItActuallyDraws:
    """The real payload and the real tiles, over real ground."""

    OSLO = [262000.0, 6650000.0]

    @pytest.fixture(scope="class")
    def run(self, tmp_path_factory):
        payload_js, _, _ = bh.national_payload()
        payload = json.loads(payload_js[len("window.SOPP="):-1])
        # Point the driver's fetch at the exporter's output instead of copying
        # 140 MB of tiles per test run.
        payload["meta"]["tiles"]["url"] = "{sp}/{z}/{row}_{col}.png"
        tmp = tmp_path_factory.mktemp("natpage")
        (tmp / "payload.json").write_text(json.dumps(payload), encoding="utf-8")
        plan = {"tilesRoot": str(TILES), "bounds": bh.NORWAY, "steps": [
            {"name": "oslo", "view": self.OSLO + [30]},
            {"name": "panned", "view": [self.OSLO[0] + 12000, self.OSLO[1], 30]},
            {"name": "country", "view": [500000.0, 7100000.0, 900]},
            {"name": "whole", "view": [500000.0, 7100000.0, 3000]},
        ]}
        (tmp / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        out = subprocess.run(
            [NODE, str(ROOT / "tests" / "national_boot.js"), str(tmp)],
            capture_output=True, text=True, encoding="utf-8", timeout=900)
        if out.returncode != 0:
            raise AssertionError(f"node failed: {out.stderr[-1500:]}")
        got = json.loads(out.stdout)
        assert got["boot"], got.get("error")
        got["by_name"] = {s["name"]: s for s in got["steps"]}
        return got

    def test_it_opens_on_ground_with_zones_in_it(self, run):
        """It opened on the middle of the bounding box once, which for a
        country shaped like Norway is the Norwegian Sea: a first screen with
        no zones on it, no explanation, and nothing wrong-looking about it."""
        op = run["opened"]
        assert op["settled"] and op["blank"] is False
        assert op["zones"][0] == 3, op["zones"]
        cells = op["win"][2] * op["win"][3]
        assert cells > 0
        assert op["scored"] / cells > 0.5, (
            f"only {op['scored']} of {cells} opening cells are scored ground")

    def test_it_does_not_offer_a_switch_that_governs_nothing(self, run):
        """Turrutebasen is packed into the page, which works for one kommune
        and not for the mainland, so this build has no vector routes. A switch
        that silently does nothing says the feature is there and broken."""
        assert run["trailsRowHidden"] is True

    def test_it_boots_and_settles(self, run):
        assert run["tiled"] is True
        assert all(s["settled"] for s in run["steps"]), \
            [s["name"] for s in run["steps"] if not s["settled"]]

    def test_there_are_zones_over_oslo(self, run):
        st = run["by_name"]["oslo"]
        assert st["blank"] is False
        assert st["zones"][0] == 3, st["zones"]

    def test_panning_loads_the_next_window(self, run):
        a, b = run["by_name"]["oslo"]["win"], run["by_name"]["panned"]["win"]
        assert a != b
        assert run["by_name"]["panned"]["zones"][0] == 3

    def test_zooming_out_drops_to_a_coarser_level(self, run):
        st = run["by_name"]["country"]
        assert st["z"] > 0, "it stayed on the finest level and gave up"
        assert st["blank"] is False
        assert st["zones"][0] == 3, st["zones"]

    def test_the_whole_country_still_has_zones_on_it(self, run):
        """The thing a beginner does first: zoom out to see where to go."""
        st = run["by_name"]["whole"]
        assert st["blank"] is False, "the whole country draws nothing"
        assert st["zones"][0] == 3, st["zones"]
        assert st["note"]["hidden"] is True

    def test_it_only_asked_for_tiles_that_exist(self, run):
        missing = [u for u in run["asked"]
                   if u.endswith(".png") and not (TILES / u).exists()]
        assert not missing, missing[:5]


class TestTheManifestDescribesTheBuildItShipsWith:
    """The manifest is what the launcher and the install prompt show, so its
    description is product copy like any other - and it was being copied into
    both builds unchanged.

    That made it wrong for the national one in two separate ways. It promised
    "merkede stier", which that build does not ship at all, and it said the
    zone estimate was worked out for Kristiansand, which stopped being true
    when the pyramid went national. A build describing a different build is
    the same defect as a switch that governs nothing, one layer further out
    where a test was less likely to look.

    Needs no exported run: the description is a function of the build flag.
    """

    def test_the_national_build_does_not_promise_marked_routes(self):
        desc = json.loads(bh.manifest_text(national=True))["description"].lower()
        assert "merkede stier" not in desc, desc

    def test_the_national_build_does_not_claim_one_kommune(self):
        desc = json.loads(bh.manifest_text(national=True))["description"]
        assert "Kristiansand" not in desc, desc
        assert "hele Norge" in desc, desc

    def test_the_agder_build_still_describes_itself(self):
        desc = json.loads(bh.manifest_text(national=False))["description"]
        assert "Kristiansand" in desc, desc
        assert "merkede stier" in desc.lower(), desc

    def test_both_say_it_is_an_estimate(self):
        """The one claim neither build may drop, whatever else changes."""
        for national in (False, True):
            desc = json.loads(bh.manifest_text(national))["description"].lower()
            assert "anslag" in desc and "ikke en fasit" in desc, desc

    def test_the_name_survives_the_rewrite(self):
        """It is rebuilt rather than copied now, so the rest must come through."""
        for national in (False, True):
            m = json.loads(bh.manifest_text(national))
            assert m["name"] == "Soppspor"
            assert m["start_url"] == "./" and m["display"] == "standalone"
            assert len(m["icons"]) == 3

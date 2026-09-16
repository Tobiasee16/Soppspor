"""config/species.yaml has to survive being added to.

The file is edited by hand, every entry is six near-identical lines, and the one
field any code reads is `artskart_taxon_id`. Two mistakes are easy to make and
invisible afterwards.

A **duplicated taxon id** fits the same records twice under two names and draws
two maps of one mushroom. It is not hypothetical: Artskart returns the same id,
40466, for both `Lactarius utilis` and `Lactarius trivialis`, so a plausible
pair of entries taken from a search would have collided. `src/species.py`
already refuses duplicate *keys*; nothing looked at the ids.

A **missing field** does not raise here. It raises hours into a national run,
or worse, produces a species that trains and then has no name to draw in the
picker.

So this is a shape check over the config, run in a second, rather than a
discovery made at the far end of a six-hour job.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import species as species_cfg

REQUIRED = ("key", "scientific_name", "vernacular_no", "vernacular_en",
            "artskart_taxon_id", "habitat_signal")


@pytest.fixture(scope="module")
def spp() -> dict:
    return species_cfg.load()


@pytest.fixture(scope="module")
def modelled(spp: dict) -> list[dict]:
    return species_cfg.modelled(spp)


class TestEveryEntryIsComplete:
    def test_there_are_species_to_model(self, modelled):
        assert len(modelled) >= 8

    @pytest.mark.parametrize("field", REQUIRED)
    def test_every_species_has_it(self, modelled, field):
        missing = [sp.get("key", "?") for sp in modelled if not sp.get(field)]
        assert not missing, f"{field} missing from: {missing}"

    def test_the_key_matches_the_scientific_name(self, modelled):
        """The key names the download file, so a mismatch trains one species on
        another's records without anything looking wrong."""
        for sp in modelled:
            expected = sp["scientific_name"].lower().replace(" ", "_")
            assert sp["key"] == expected, (
                f"{sp['key']} should be {expected} for {sp['scientific_name']}")

    def test_keys_are_plain_identifiers(self, modelled):
        """They become filenames, payload keys and localStorage values."""
        for sp in modelled:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", sp["key"]), sp["key"]


class TestNothingIsDuplicated:
    def test_no_two_species_share_a_taxon_id(self, modelled):
        """The failure this file was written for.

        Artskart hands back one id, 40466, for both Lactarius utilis and
        Lactarius trivialis - they are not separable in the source. Two entries
        built from that search would fit identical records twice and draw two
        maps of the same mushroom, each labelled as a different species.
        """
        seen: dict[int, str] = {}
        for sp in modelled:
            tid = sp["artskart_taxon_id"]
            assert tid not in seen, (
                f"{sp['key']} and {seen[tid]} share Artskart taxon {tid} - "
                f"they are the same records under two names")
            seen[tid] = sp["key"]

    def test_no_two_species_share_a_norwegian_name(self, modelled):
        """The picker shows these, and two identical rows are unusable."""
        names = [sp["vernacular_no"].lower() for sp in modelled]
        dupes = sorted({n for n in names if names.count(n) > 1})
        assert not dupes, dupes


class TestTheRegisteredTestIsNotDiluted:
    def test_the_registered_three_are_still_exactly_three(self, spp):
        """Their ordering was written down before any model existed. Adding a
        fourth would silently redefine what was predicted, which is the one
        thing the pre-registration exists to prevent."""
        reg = species_cfg.registered(spp)
        assert [sp["key"] for sp in reg] == [
            "craterellus_tubaeformis", "cantharellus_cibarius", "amanita_muscaria"]

    def test_only_the_registered_three_carry_a_predicted_rank(self, spp):
        for sp in species_cfg.additional(spp):
            assert "predicted_rank" not in sp, (
                f"{sp['key']} carries a predicted_rank; it was added after the "
                f"ordering test was run and cannot be part of it")
        assert sorted(sp["predicted_rank"] for sp in species_cfg.registered(spp)) \
            == [1, 2, 3]

    def test_the_null_is_never_a_modelled_species(self, spp, modelled):
        """CLAUDE.md hard constraint 4: it is never mixed with a real set."""
        null_key = spp["null_control"]["key"]
        assert null_key not in [sp["key"] for sp in modelled]
        assert "NOT REAL DATA" in spp["null_control"]["label"]


class TestTheExclusionsStaySpecific:
    """Species looked at and deliberately left out are recorded in a comment.

    That record is the thing that stops a question being reopened every season,
    so it has to say which numbers it was decided on - and the extent moved from
    Agder to the whole country in September 2026, which silently invalidated the
    reasons written against Kristiansand counts.
    """

    @pytest.fixture(scope="class")
    def text(self) -> str:
        return (ROOT / "config" / "species.yaml").read_text(encoding="utf-8")

    def test_excluded_species_are_named_with_a_reason(self, text):
        block = text[text.index("Deliberately excluded"):text.index("additional_species:")]
        assert "Hydnum rufescens" in block
        # The reason has to be the taxonomic one, not a stale record count:
        # nationally it has more records than species that already ship.
        assert "Hydnum repandum" in block, (
            "the reason for leaving H. rufescens out is that the source conflates "
            "it with H. repandum, which ships - say so where it is excluded")

    def test_no_exclusion_rests_on_the_old_extent(self, text):
        """Every live reason is argued from the extent that decides today.

        Not a ban on the word: the block should say that the old reasoning was
        argued from Kristiansand counts and has expired, because that is the
        record which stops the question being reopened. What must not survive is
        the stale claim itself, or an exclusion with no held-out number against
        it.
        """
        block = text[text.index("Deliberately excluded"):text.index("additional_species:")]
        assert "0 in Kristiansand" not in block, (
            "the old Kristiansand reason is still standing as a live argument")
        assert "Nordland" in block, (
            "no exclusion cites the held-out extent that actually decides")
        # One dated recheck, so a reader knows which numbers these are.
        assert "2026-09-16" in block


class TestEverySpeciesHasItsOwnColour:
    """The picker and the map colour species by key, from SPCOL in the shell.

    A species with no entry falls through to SPFALLBACK, which **cycles**. That
    is not a hypothetical: the national build shipped eight species against
    seven SPCOL entries, so blek piggsopp drew in traktkantarell's blue on the
    live site - same dot in the picker, same colour on the map, no error
    anywhere. Adding six more species would have made it seven collisions.

    The list of species lives in this config and the colours live in the shell,
    so nothing but a test spanning both can catch the mismatch.
    """

    @pytest.fixture(scope="class")
    def spcol(self) -> dict:
        text = (ROOT / "web" / "shell.html").read_text(encoding="utf-8")
        block = text[text.index("var SPCOL="):text.index("var SPFALLBACK=")]
        return dict(re.findall(r'(\w+):\s*"(#[0-9A-Fa-f]{6})"', block))

    def test_every_modelled_species_has_a_colour(self, modelled, spcol):
        missing = [sp["key"] for sp in modelled if sp["key"] not in spcol]
        assert not missing, (
            f"no colour for {missing}; they will fall through to SPFALLBACK, "
            f"which cycles and will hand them another species' colour")

    def test_no_two_species_share_a_colour(self, spcol):
        seen: dict[str, str] = {}
        for key, hexc in spcol.items():
            assert hexc not in seen, f"{key} and {seen[hexc]} are both {hexc}"
            seen[hexc] = key

    def test_the_palette_carries_no_species_that_left_the_build(self, modelled, spcol):
        """A stale entry is harmless on the map and misleading in the file."""
        keys = {sp["key"] for sp in modelled}
        assert set(spcol) <= keys, f"SPCOL names species not in the config: {set(spcol) - keys}"


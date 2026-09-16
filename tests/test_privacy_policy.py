"""The privacy policy has to keep agreeing with the code it describes.

Spots are stored as readable rows, protected by row-level security and by a
policy of not looking at them. That was a deliberate decision, and it moves the
weight onto the two things that make such a promise credible to somebody who
cannot audit it: a working delete-everything path, and a plain statement of what
is held. The delete path has tests. This is the other half.

A privacy policy fails silently. Nothing crashes when it starts describing a
different product - when a third party is added and the list of who sees what is
not, when a retention window is changed in SQL and not in the prose, when the
page stops being copied into the build and the link in settings 404s. So the
checks here deliberately tie the prose to the thing it claims about: the hosts
the app actually contacts, the interval `purge_tombstones()` actually uses, the
length limit the database actually enforces.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY = ROOT / "web" / "personvern.html"

CONTROLLER = "Tobias Engedal Ek"
SUPPORT = "support@soppspor.no"

# Every external host the app can contact, and the party the policy has to name
# for it. A host with no entry here fails the test rather than passing quietly:
# adding a third party is exactly the change that must not happen without the
# policy following it.
PARTIES = [
    (re.compile(r"(?:^|\.)kartverket\.no$|(?:^|\.)geonorge\.no$"), "Kartverket"),
    (re.compile(r"(?:^|\.)supabase\.(?:co|com)$"), "Supabase"),
]


@pytest.fixture(scope="module")
def policy() -> str:
    """The page as written, for checks about its structure."""
    assert POLICY.exists(), "web/personvern.html is gone; settings links to it"
    return POLICY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prose(policy: str) -> str:
    """The page with its whitespace collapsed, for checks about its wording.

    Prose gets rewrapped. A phrase check that fails because an editor moved a
    line break is a test that trains people to ignore it.
    """
    return re.sub(r"\s+", " ", policy)


def contacted_hosts() -> set[str]:
    """Hosts the shipped app can reach, from the sources that decide them."""
    hosts: set[str] = set()
    for rel in ("web/shell.html", "web/sw.js"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        hosts |= set(re.findall(r"https://([a-zA-Z0-9.-]+)", text))
        # sw.js lists cacheable tile hosts as bare strings, not URLs.
        for m in re.finditer(r"TILE_HOSTS\s*=\s*\[([^\]]*)\]", text):
            hosts |= set(re.findall(r'"([a-zA-Z0-9.-]+)"', m.group(1)))
    cfg = ROOT / "config" / "supabase.json"
    if cfg.exists():
        url = json.loads(cfg.read_text(encoding="utf-8")).get("url", "")
        m = re.match(r"https://([a-zA-Z0-9.-]+)", url)
        if m:
            hosts.add(m.group(1))
    return hosts


class TestItNamesTheRightPeople:
    def test_it_names_the_data_controller(self, prose):
        assert CONTROLLER in prose

    def test_it_gives_the_service_address(self, prose):
        assert SUPPORT in prose

    def test_it_ships_no_other_address(self, prose):
        """A personal address in a policy is a personal address on the web."""
        found = set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", prose))
        assert found == {SUPPORT}, found


class TestItDescribesTheAppThatExists:
    def test_every_third_party_the_app_contacts_is_named(self, prose):
        """The check that matters: adding a host without updating the policy."""
        for host in sorted(contacted_hosts()):
            party = next((n for rx, n in PARTIES if rx.search(host)), None)
            assert party, (
                f"{host} is contacted by the app and this test does not know "
                "who it belongs to - name the party and add it to PARTIES")
            assert party in prose, f"{host} is contacted but {party} is unnamed"

    def test_it_says_what_the_search_field_sends(self, prose):
        """A map tile reveals where you are looking; a search box reveals what
        you were looking for, in your own words. The policy named Kartverket
        already - it serves the basemap - so the host check above stayed green
        when search was added and could not have caught this on its own."""
        shell = (ROOT / "web" / "shell.html").read_text(encoding="utf-8")
        if 'id="findQ"' not in shell:
            pytest.skip("this build has no search field")
        assert "søk" in prose.lower(), "the policy does not mention search at all"
        assert "det du skriver" in prose.lower(), (
            "the policy does not say that what you type is sent to Kartverket")

    def test_the_region_matches_what_was_checked(self, prose):
        """Recorded in backend/DECISION.md after being verified in the console."""
        decision = (ROOT / "backend" / "DECISION.md").read_text(encoding="utf-8")
        assert "eu-west-1" in decision, "the recorded region moved; check the policy"
        assert "eu-west-1" in prose

    def test_the_retention_window_matches_the_function(self, prose):
        """`purge_tombstones()` decides this, not the prose."""
        schema = (ROOT / "backend" / "schema.sql").read_text(encoding="utf-8")
        m = re.search(r"purge_tombstones\(older_than interval default '(\d+) (\w+)'",
                      schema)
        assert m, "purge_tombstones() no longer declares a default interval"
        assert m.group(2).startswith("day"), m.group(0)
        assert f"{m.group(1)} dager" in prose, (
            f"schema purges after {m.group(1)} days; the policy says otherwise")

    def test_the_note_limit_matches_the_constraint(self, prose):
        schema = (ROOT / "backend" / "schema.sql").read_text(encoding="utf-8")
        m = re.search(r"char_length\(note\) <= (\d+)", schema)
        assert m, "the note length constraint is gone"
        assert f"{m.group(1)} tegn" in prose

    def test_it_repeats_the_two_permanent_safety_lines(self, prose):
        """Never says whether something is edible, never identifies from a photo.

        These are the two constraints the product may never relax, so the one
        document a careful reader will actually open should carry them.
        """
        assert "spiselig" in prose
        assert "bilder" in prose or "bilde" in prose

    def test_it_says_the_map_is_an_estimate(self, prose):
        assert "anslag" in prose and "ikke en fasit" in prose


class TestItIsActuallyReachable:
    def test_the_app_links_to_it(self):
        shell = (ROOT / "web" / "shell.html").read_text(encoding="utf-8")
        assert 'href="personvern.html"' in shell

    def test_the_build_copies_it(self):
        build = (ROOT / "scripts" / "build_hosted.py").read_text(encoding="utf-8")
        assert "personvern.html" in build

    def test_it_survives_the_single_page_catch_all(self):
        """It has no rule of its own, and does not need one.

        Static assets are served before `_redirects` is consulted, so a real
        file at /personvern.html wins over `/*  /index.html  200`. That is not
        an assumption: the deployed check fetches the address and asserts the
        policy comes back rather than the app, and the same deploy proved the
        rule for /conditions.json, which likewise has no entry.

        What this pins is the part that can rot silently - that the file is
        still copied into the build, and that the live check still looks.
        """
        build = (ROOT / "scripts" / "build_hosted.py").read_text(encoding="utf-8")
        assert "personvern.html" in build, "the build no longer ships the policy"
        deployed = (ROOT / "scripts" / "check_deployed.py").read_text(encoding="utf-8")
        assert "personvern.html" in deployed,             "nothing checks that the live policy is the policy"
        assert "not the app served by the fallback" in deployed

    def test_it_is_readable_offline(self):
        """People often open it while deciding to delete their account, and
        "you can read what we do with your data, provided you have signal" is
        not much of a commitment."""
        sw = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        m = re.search(r"addAll\(\[([^\]]*)\]\)", sw)
        assert m, "the precache list changed shape"
        assert "personvern.html" in m.group(1)

    def test_it_stands_alone_as_a_page(self, policy):
        """No app, no payload, no service worker needed to read it."""
        assert policy.lstrip().lower().startswith("<!doctype html>")
        assert '<html lang="no">' in policy
        assert "<script" not in policy.lower()


class TestItCanBeNavigated:
    """The page folds into a list of questions, and two things do not fold.

    A privacy policy is opened by somebody with one question - who can see my
    spots, how do I delete this - and a wall of prose is how that question goes
    unanswered. Folding it makes the questions the page's structure. The risk
    the checks below cover is the obvious one: folding away something a reader
    must not have to click to find.
    """

    def test_the_sections_fold(self, policy):
        n = policy.count("<details")
        assert n >= 10, f"only {n} folding sections; the page was a wall again"
        assert policy.count("<summary>") == n, "a section with no heading to tap"
        assert policy.count("</details>") == n

    def test_it_folds_without_script(self, policy):
        """`<details>` is why this works at all.

        The page must render before any of the app has loaded, and
        test_it_stands_alone_as_a_page forbids script outright. A div-and-click
        accordion would also lose keyboard operation and the screen-reader
        announcement that the native element gives for nothing.
        """
        assert "<script" not in policy.lower()
        assert "onclick" not in policy.lower()

    def test_print_does_not_hide_anything(self, policy):
        """On paper there is nothing to tap, so nothing may stay shut."""
        assert "@media print" in policy
        assert "details > :not(summary){display:block}" in policy.replace("\n", "")

    def test_the_things_a_reader_must_not_miss_are_not_folded(self, policy):
        # Collapsed, because the page wraps and "ikke en fasit" spans a line.
        """The summary and the safety reminder stay on the page unopened.

        The two permanent product constraints - never says what is edible,
        never identifies from a photograph - and the framing of the map as an
        estimate are exactly what somebody skimming needs to meet without
        deciding to open anything.
        """
        outside = re.sub(r"\s+", " ",
                         re.sub(r"<details\b.*?</details>", "", policy, flags=re.S))
        assert "spiselig" in outside, "the edibility line is behind a fold"
        assert "bilder" in outside, "the photo-identification line is behind a fold"
        assert "anslag" in outside and "ikke en fasit" in outside
        # The five-point summary, which answers most of the page without a tap.
        assert "aldri" in outside and "slette alt selv" in outside

    def test_every_fold_has_something_in_it(self, policy):
        for m in re.finditer(r"<details\b.*?</details>", policy, flags=re.S):
            body = re.sub(r"<summary>.*?</summary>", "", m.group(0), flags=re.S)
            text = re.sub(r"<[^>]+>", " ", body).strip()
            assert len(text) > 40, f"an empty fold: {m.group(0)[:80]}"

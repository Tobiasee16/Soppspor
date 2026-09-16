"""The Innstillinger page: what order it is in, and what is folded away.

Settings is the page a reader opens with a job in mind - turn sync off, change
the theme, delete everything. It had three walls of explanation sitting between
the switches they described, so the switch you came for was below a paragraph
you were not reading. Two of those now fold.

Ordering matters for the same reason and is exactly the kind of thing a later
edit undoes without noticing: Utseende is a one-tap preference and Personvern
is the longest section on the page, so the short control comes first.

These are checks on the markup rather than on rendering, because the failure
mode is a section quietly moving or a fold quietly being unwrapped, and both
are visible in the file.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHELL = ROOT / "web" / "shell.html"


@pytest.fixture(scope="module")
def settings() -> str:
    """Just the Innstillinger panel."""
    text = SHELL.read_text(encoding="utf-8")
    start = text.index('<div class="panel" id="pInnst">')
    end = text.index("<!-- welcome -->", start)
    return text[start:end]


class TestTheOrder:
    def test_the_sections_are_in_the_intended_order(self, settings):
        assert re.findall(r'class="glabel">([^<]+)<', settings) == [
            "Kart", "Utseende", "Synkronisering", "Personvern"]

    def test_appearance_comes_before_the_account_block(self, settings):
        """Theme is one tap. The account block below it is a status, a form and
        a warning about losing your spots, and Personvern is longer still - so
        both of the quick map preferences sit above all of that."""
        assert settings.index("Utseende") < settings.index("Kun denne telefonen") \
            if "Kun denne telefonen" in settings else True
        assert settings.index("Utseende") < settings.index('id="acct"')
        assert settings.index("Utseende") < settings.index("Personvern")

    def test_the_delete_everything_button_stays_under_privacy(self, settings):
        """It is the one irreversible control on the page and it belongs with
        the text explaining what it destroys, not stranded after a theme
        switcher."""
        assert settings.index("Personvern") < settings.index('id="wipeShow"')


class TestWhatIsFolded:
    def test_the_long_explanations_fold(self, settings):
        assert settings.count('<details class="fold">') == 2
        assert settings.count("<summary>") == 2

    def test_each_fold_says_what_is_inside_it(self, settings):
        for summary in re.findall(r"<summary>([^<]+)</summary>", settings):
            assert len(summary.strip()) > 8, summary

    def test_the_folds_hold_the_prose_and_not_the_controls(self, settings):
        """A switch inside a fold is a switch nobody finds."""
        for block in re.findall(r"<details class=\"fold\">.*?</details>",
                                settings, re.S):
            assert "<input" not in block, "a control was folded away"
            assert 'class="seg"' not in block
            assert "wipeShow" not in block


class TestWhatIsNotFolded:
    def test_the_account_state_stays_visible(self, settings):
        """`acctHelp` carries the warning that an anonymous account cannot be
        recovered - «borte for godt». A reader who has not opened anything has
        to meet that, because it is the reason to add an email address."""
        assert 'id="acctHelp"' in settings
        folded = " ".join(re.findall(r"<details class=\"fold\">.*?</details>",
                                     settings, re.S))
        assert 'id="acctHelp"' not in folded

    def test_the_full_policy_is_still_linked(self, settings):
        assert 'href="personvern.html"' in settings


class TestTheSheetsCanBePulledDown:
    """Not settings, but the same complaint: a control that exists and cannot
    be found. The species sheet was dismissable only by its 36x5 px notch."""

    @pytest.fixture(scope="module")
    def shell(self) -> str:
        return SHELL.read_text(encoding="utf-8")

    def test_the_whole_sheet_is_the_drag_surface(self, shell):
        """Not the notch, and not the header either.

        Both were tried. The notch is 36x5 px; the header is about 44 px tall
        against a 70 px threshold, so the finger leaves it mid-pull. A hand
        reaching to dismiss a sheet lands on its body, which is where the
        listener has to be.
        """
        assert 'sheet.addEventListener("pointerdown"' in shell
        # Only a sheet advertising a handle gets the behaviour.
        assert 'if(!sheet.querySelector(".grab"))return;' in shell

    def test_the_drag_does_not_depend_on_pointer_capture(self, shell):
        """setPointerCapture sits behind a try/catch, so it is one silent
        failure away from a drag that stalls the instant the finger leaves the
        element. Moves are tracked on the window instead."""
        body = shell.split("function makeDismissable(")[1]
        body = body[:body.index("\n}")]
        assert 'window.addEventListener("pointermove"' in body
        assert "setPointerCapture" not in body

    def test_a_pull_at_the_end_of_the_list_does_not_scroll_the_page(self, shell):
        """On iOS that is what makes a dismissal feel like it did nothing."""
        assert re.search(r"\.card\{overscroll-behavior:contain\}", shell)

    def test_the_handle_opts_out_of_browser_touch_handling(self, shell):
        """The notch and header never scroll, so they can refuse outright. The
        body must not, or the species list stops scrolling."""
        assert re.search(r"\.cardTop\{[^}]*touch-action:none", shell)
        assert re.search(r"\.grab\{[^}]*touch-action:none", shell)

    def test_every_sheet_that_has_a_notch_is_dismissable(self, shell):
        """A notch is a promise. A sheet drawing one that cannot be pulled is
        worse than a sheet with no notch."""
        panel = shell[:shell.index("<script>")]
        with_notch = re.findall(r'id="(\w+)"[^>]*>\s*<div class="grab">', panel)
        # The markup puts the id on the sheet and the notch immediately inside.
        for sheet_id in with_notch:
            assert f"makeDismissable({sheet_id}" in shell or \
                   f'makeDismissable(document.getElementById("{sheet_id}")' in shell, \
                   f"{sheet_id} draws a notch but is never made dismissable"
        assert len(with_notch) >= 3, with_notch

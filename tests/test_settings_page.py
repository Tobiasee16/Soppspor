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
            "Kart", "Synkronisering", "Utseende", "Personvern"]

    def test_appearance_comes_before_privacy(self, settings):
        """The short control ahead of the long explanation, not after it."""
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

    def test_the_header_is_a_drag_surface(self, shell):
        assert 'sheet.querySelector(".cardTop")' in shell
        assert 'sheet.querySelector(".grab")' in shell

    def test_the_header_opts_out_of_browser_touch_handling(self, shell):
        """Without this the page scrolls under the finger instead of the sheet
        following it, which reads as the drag not working at all."""
        assert re.search(r"\.cardTop\{[^}]*touch-action:none", shell)

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

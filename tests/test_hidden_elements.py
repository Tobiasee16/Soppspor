"""An element the code hides must actually disappear.

`[hidden]{display:none}` is a user-agent stylesheet rule, and author rules beat
user-agent rules whatever their specificity. So `el.hidden = true` is a no-op on
any element whose class carries a display of its own, and this app hides several
such elements.

It was reported from the phone as "the tursti function does not work": the
national build ships no route geometry, so `rowTrails.hidden = !TR` should have
removed the switch, but `.row{display:flex}` kept it on screen. The reader saw a
switch for marked routes, turned it on, and nothing happened - which is exactly
the state the comment above that line says is worse than having no switch.

Two more rode along with it: the conditions pill could not be hidden when there
was no weather to show, and the delete-everything button stayed visible beneath
its own confirmation box.

The fix is one author-origin rule. This test fails if it is removed while any
element still depends on it.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHELL = ROOT / "web" / "shell.html"


def stylesheet(html: str) -> str:
    css = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def display_selectors(css: str) -> set[str]:
    """Every selector with a display declaration of its own."""
    out = set()
    for sel, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        if re.search(r"(^|;)\s*display\s*:", body):
            out.update(s.strip() for s in sel.split(","))
    return out


def hidden_targets(html: str) -> dict[str, list[str]]:
    """id -> classes, for every element the code toggles with .hidden."""
    id_class = {}
    for m in re.finditer(r'<\w+([^>]*)\bid="([^"]+)"([^>]*)>', html):
        attrs = m.group(1) + m.group(3)
        cm = re.search(r'class="([^"]*)"', attrs)
        id_class[m.group(2)] = cm.group(1).split() if cm else []

    lines = html.split("\n")
    found = {}
    for i, line in enumerate(lines):
        for m in re.finditer(r"(\w+)\.hidden\s*=", line):
            var = m.group(1)
            eid = None
            direct = re.search(r'getElementById\("([^"]+)"\)\.hidden', line)
            if direct:
                eid = direct.group(1)
            else:
                for j in range(i, max(0, i - 40), -1):
                    am = re.search(
                        r'\b%s\s*=\s*document\.getElementById\("([^"]+)"\)' % re.escape(var),
                        lines[j])
                    if am:
                        eid = am.group(1)
                        break
            if eid is not None and eid in id_class:
                found[eid] = id_class[eid]
    return found


def at_risk(html: str) -> dict[str, list[str]]:
    """Hidden-toggled elements that an author display rule would keep visible."""
    css = stylesheet(html)
    disp = display_selectors(css)
    out = {}
    for eid, classes in hidden_targets(html).items():
        hits = [s for s in ("#" + eid, *("." + c for c in classes)) if s in disp]
        if hits:
            out[eid] = hits
    return out


class TestHidingWorks:
    def test_the_author_rule_exists(self):
        css = stylesheet(SHELL.read_text(encoding="utf-8"))
        assert re.search(r"\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important", css), (
            "without an author-origin [hidden] rule, el.hidden=true does nothing "
            "to any element whose class sets display")

    def test_the_rule_is_load_bearing(self):
        """If nothing needed it, removing it would be safe and this test weak."""
        risky = at_risk(SHELL.read_text(encoding="utf-8"))
        assert risky, "no element needs the rule any more - check this test still bites"

    def test_the_switch_that_started_it(self):
        """A build with no routes hides the marked-routes switch. It must obey.

        The condition widened when routes went national: it used to be `!TR`,
        the inline pack, because the mainland build carried no geometry at all.
        It now also has `TR_INDEX`, the list of per-fylke files, and the switch
        is hidden only when neither exists. What is being tested is unchanged -
        a switch that governs nothing must not be on screen - and the element
        still needs the author-origin `[hidden]` rule to beat `.row`.
        """
        html = SHELL.read_text(encoding="utf-8")
        assert "rowTrails" in at_risk(html), (
            "rowTrails no longer carries a display rule; if the settings row "
            "markup changed, this test is watching the wrong element")
        assert re.search(r"row\.hidden\s*=\s*!\(\s*TR\s*\|\|\s*TR_INDEX\s*\)", html), (
            "the switch is no longer hidden when the build has neither an "
            "inline pack nor an index of fylke route files")

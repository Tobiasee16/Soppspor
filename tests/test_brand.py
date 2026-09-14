"""The app is called Soppspor, and says so everywhere a user can see.

The rename from Soppkart touched twenty-one files, and nothing pinned the name
in any of them - the only existing check was that an `apple-mobile-web-app-title`
tag exists, not what it says. A brand half-renamed is worse than one not renamed
at all: the reader sees two products.

Three things deliberately keep the old name, and this file states each so that a
future find-and-replace has to argue with a test rather than with a comment:

  * the `soppkart.` localStorage namespace, because renaming it silently
    destroys every existing user's spots, session and settings;
  * the Cloudflare Pages project in deploy.sh and web/README.md, which names a
    project that already exists in an account;
  * the repository itself, still SoppKompis on GitHub and on disk.

It also pins the service address, in the two places it has to be right: the
privacy copy, where it is the only way a user can ask what is held about them,
and the User-Agent headers, where the Norwegian public APIs we depend on ask to
be told who is calling.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

NAME = "Soppspor"
SUPPORT = "support@soppspor.no"

# Everything here is read by a user, or becomes something a user reads.
SURFACES = [
    "web/shell.html",
    "web/manifest.webmanifest",
    "web/sw.js",
    "scripts/build_hosted.py",
    "scripts/serve_interface.py",
]

# Outward-facing fetchers. These identify us to MET, Kartverket, Geonorge and
# NGU, whose terms ask for an identifiable agent with a way to reach us.
FETCHERS = [
    "src/ingest/senorge.py",
    "src/ingest/env.py",
    "src/ingest/dtm.py",
    "src/ingest/geonorge_download.py",
    "src/ingest/naturbase.py",
    "scripts/fetch_basemap.py",
    "scripts/fetch_trails.py",
    "scripts/preview_map.py",
]

# The five storage keys, which are history rather than branding. The trailing
# dot is the whole distinction and it is worth stating: the namespace always
# carries one, the brand never did, so `soppkart.` is a key reference and a bare
# `Soppkart` is a name that should have been renamed.
NAMESPACE = re.compile(r"soppkart\.")


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", SURFACES)
def test_no_surface_still_carries_the_old_name(rel: str):
    """With the storage namespace set aside, nothing a user sees says Soppkart."""
    text = NAMESPACE.sub("<storage-key>", read(rel))
    stray = re.findall(r"(?i)soppkart|soppkompis", text)
    assert not stray, f"{rel} still carries {sorted(set(stray))}"


def test_the_installed_app_is_named_soppspor():
    manifest = read("web/manifest.webmanifest")
    assert f'"name": "{NAME}"' in manifest
    assert f'"short_name": "{NAME}"' in manifest


def test_the_page_titles_agree_with_the_manifest():
    """Three separate places write a title, and they drifted apart once."""
    assert f"<title>{NAME}" in read("web/shell.html")
    hosted = read("scripts/build_hosted.py")
    assert f"<title>{NAME}</title>" in hosted
    assert f'apple-mobile-web-app-title" content="{NAME}"' in hosted
    assert f'apple-mobile-web-app-title" content="{NAME}"' in read(
        "scripts/serve_interface.py")


def test_the_storage_namespace_did_not_follow_the_rename():
    """Renaming these is the one part of a rename that destroys user data.

    An anonymous account exists only as a refresh token under
    `soppkart.session`, so losing that key loses the account with no error and
    nothing to restore from.
    """
    shell = read("web/shell.html")
    keys = sorted(set(re.findall(r'localStorage\.[gs]etItem\("([^"]+)"', shell)))
    assert keys, "no storage keys found at all - has the accessor changed shape?"
    assert all(k.startswith("soppkart.") for k in keys), keys
    assert "storage keys are a namespace, not a brand" in shell


def test_a_user_can_find_out_who_to_ask():
    """Spots are stored readable and protected by a promise not to look at them.

    That promise is only worth something if a user has a way to ask what is held
    about them, so the address belongs next to the copy that makes the promise.
    """
    shell = read("web/shell.html")
    assert SUPPORT in shell
    privacy = shell[shell.index("Personvern"):shell.index("wipeShow")]
    assert SUPPORT in privacy, "the address is in the file but not in the privacy copy"


@pytest.mark.parametrize("rel", FETCHERS)
def test_outward_requests_say_who_we_are_and_how_to_reach_us(rel: str):
    text = read(rel)
    agents = re.findall(r'"([^"]*(?:User-Agent|soppspor)[^"]*)"', text)
    ua = [a for a in agents if "soppspor" in a]
    assert ua, f"{rel} has no identifiable User-Agent"
    assert all(SUPPORT in a for a in ua), f"{rel}: {ua} names no contact address"


def test_product_copy_carries_no_personal_address():
    """The maintainer's own address is for commit authorship, not for shipping."""
    for rel in SURFACES:
        text = read(rel)
        found = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        bad = [a for a in found
               if a != SUPPORT and not a.startswith(("din@", "your@"))]
        assert not bad, f"{rel} ships {bad}"

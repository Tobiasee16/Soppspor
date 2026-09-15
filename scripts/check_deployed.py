#!/usr/bin/env python
"""Check that what is live is what was built.

`wrangler pages deploy` has been observed exiting 0 after refusing to deploy
anything at all - it printed "The Pages project does not exist", then returned
success. A deploy script that trusts an exit code will happily tell you it
published. So the only trustworthy check is to fetch the site and look.

It answers four questions that have each been wrong at some point in this
project's history:

  * Is the live page the build that was just made, or a stale one? Compared by
    the payload's own `area_label`, because the two builds are otherwise
    indistinguishable from outside - same name, same page, same icons - and
    publishing the Kristiansand map to testers in Oslo shows them a country
    with no zones and no error.
  * Did every tile actually arrive? Sampled from the payload's own manifest,
    because a partial upload is silent - the app simply shows no zones over
    whichever ground is missing, with no error to explain it.
  * Is the privacy policy reachable at its own address, rather than being
    swallowed by the single-page fallback?
  * Does a path a person could arrive at still reach the app?

    python scripts/check_deployed.py https://soppspor.pages.dev dist_norge
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

UA = "soppspor/0.1 (deployment check; support@soppspor.no)"
TIMEOUT = 30

fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    print(("PASS  " if ok else "FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails += 1


def get(url: str, tries: int = 3) -> tuple[int, bytes, str]:
    """Fetch, reporting failures rather than raising them.

    A dropped connection used to end the run with a stack trace, which is the
    least useful thing a deployment check can do: it looks identical to the
    script being broken, and it says nothing about the site. A refused or timed
    out request is now a status of 0, so it prints as a FAIL line beside every
    other answer. Retried a couple of times first, because one flaky packet is
    not a deployment problem.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = ""
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, r.read(2_000_000), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.read(200_000), e.headers.get("Content-Type", "")
        except (urllib.error.URLError, OSError) as e:  # noqa: PERF203
            last = type(e).__name__ + ": " + str(getattr(e, "reason", e))[:60]
    return 0, last.encode("utf-8", "replace"), ""


def area_of(html: str) -> str | None:
    m = re.search(r'"area_label":"([^"]*)"', html)
    return m.group(1) if m else None


def extract_payload(html: str) -> dict:
    """window.SOPP, by brace matching. A regex cannot find the end of it."""
    try:
        j = html.index("{", html.index("window.SOPP="))
    except ValueError:
        return {}
    depth, k, instr, esc = 0, j, False, False
    while k < len(html):
        c = html[k]
        if instr:
            if esc:
                esc = False
            elif c == chr(92):
                esc = True
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    try:
        return json.loads(html[j:k + 1])
    except Exception:
        return {}


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: check_deployed.py <origin> <built-dir>")
    origin = sys.argv[1].rstrip("/")
    out = pathlib.Path(sys.argv[2])

    local = (out / "index.html").read_text(encoding="utf-8")
    want = area_of(local)

    print(f"checking {origin} against {out}/\n")

    status, body, ctype = get(origin + "/")
    html = body.decode("utf-8", "replace")
    check("the site responds", status == 200, f"HTTP {status}")
    check("it is HTML", "text/html" in ctype, ctype)
    got = area_of(html)
    check("the live build covers the same ground as the one just built",
          got is not None and got == want, f"live {got!r} vs built {want!r}")
    check("it is named Soppspor", "<title>Soppspor</title>" in html)

    # Tiles, sampled from the payload's own manifest.
    #
    # The check this replaces asked whether a *missing* tile 404s, on the
    # theory that a failed upload would then show up as the app being served
    # with a 200. Cloudflare Pages will not do that alongside a single-page
    # fallback - three deploys went into establishing it, and the note in
    # build_hosted.py records what was tried - so an absent tile does come
    # back as the app, by decision rather than by accident.
    #
    # Asking about the tiles that are supposed to exist is the better
    # question anyway. It tests an incomplete upload directly instead of
    # through a proxy, and those are the only tiles the app ever requests.
    payload = extract_payload(html)
    tmpl = (payload.get("meta", {}).get("tiles", {}) or {}).get("url", "")
    sampled, bad = 0, []
    if tmpl and payload.get("species"):
        for sp in payload["species"][:3]:
            levels = sp.get("tiles") or []
            if not levels or not levels[0]:
                continue
            ids = levels[0]
            for tid in (ids[0], ids[len(ids) // 2], ids[-1]):
                row, _, col = tid.partition("_")
                url = (tmpl.replace("{sp}", sp["key"]).replace("{z}", "0")
                       .replace("{row}", row).replace("{col}", col))
                st, bd, ct = get(f"{origin}/{url}")
                sampled += 1
                if not (st == 200 and bd[:8] == b"\x89PNG\r\n\x1a\n"):
                    bad.append(f"{url} -> HTTP {st}, {ct}, {len(bd)} bytes")
    check("every sampled zone tile comes back as a PNG",
          sampled > 0 and not bad,
          f"{sampled - len(bad)}/{sampled} ok" + ("; " + bad[0] if bad else ""))

    # The habitat mask. Its absence is the quiet kind of failure: the zones
    # still draw, they simply stop being cut at the shoreline, so the map
    # claims ground the model was never given. Nothing on screen would say so.
    mask = (payload.get("meta", {}) or {}).get("mask") or {}
    mtmpl, mlevels = mask.get("url", ""), mask.get("tiles") or []
    msampled, mbad = 0, []
    if mtmpl and mlevels and mlevels[0]:
        ids = mlevels[0]
        for tid in (ids[0], ids[len(ids) // 2], ids[-1]):
            row, _, col = tid.partition("_")
            url = (mtmpl.replace("{z}", "0")
                   .replace("{row}", row).replace("{col}", col))
            st, bd, ct = get(f"{origin}/{url}")
            msampled += 1
            # Colour type 3 with a tRNS chunk: a palette whose transparency is
            # the payload. A tile that came back as the single-page fallback,
            # or as an 8-bit grey zone tile by mistake, fails both halves.
            ok = (st == 200 and bd[:8] == b"\x89PNG\r\n\x1a\n"
                  and len(bd) > 33 and bd[25] == 3 and b"tRNS" in bd[:200])
            if not ok:
                mbad.append(f"{url} -> HTTP {st}, {ct}, {len(bd)} bytes")
    check("the habitat mask is live and is a transparency mask",
          msampled > 0 and not mbad,
          f"{msampled - len(mbad)}/{msampled} ok" + ("; " + mbad[0] if mbad else ""))

    # And a count, so a partial upload that happened to miss the sample
    # still has somewhere to show up. Mask tiles live under zones/ as well, so
    # both are named here or the total would drift every time one changed.
    zones = out / "zones"
    on_disk = len(list(zones.rglob("*.png"))) if zones.exists() else 0
    expected = sum(len(lv) for sp in payload.get("species", [])
                   for lv in (sp.get("tiles") or []))
    expected += sum(len(lv) for lv in mlevels)
    check("the build holds every tile its payload names",
          expected == 0 or on_disk >= expected,
          f"{on_disk} on disk, {expected} named")

    status, body, ctype = get(origin + "/personvern.html")
    policy = body.decode("utf-8", "replace")
    check("the privacy policy is reachable at its own address", status == 200,
          f"HTTP {status}")
    check("it is the policy, not the app served by the fallback",
          "Personvern i Soppspor" in policy)
    check("it names the data controller", "Tobias Engedal Ek" in policy)

    status, body, _ = get(origin + "/steder")
    check("a path a person could arrive at reaches the app",
          status == 200 and b"window.SOPP=" in body, f"HTTP {status}")

    # A cache-buster, because this check once passed on an edge-cached copy of
    # the *previous* deployment while the live endpoint was answering 503. The
    # conditions route is a Pages Function now, so what it returns depends on
    # runtime state - a KV binding - that no amount of reading the built
    # directory can predict, and an edge copy hides exactly that.
    status, body, ctype = get(origin + f"/conditions.json?deploycheck={int(time.time())}")
    check("the weather is served", status == 200, f"HTTP {status}")
    if status == 200:
        try:
            cond = json.loads(body.decode("utf-8"))
            check("it carries a last day", bool(cond.get("last_day")),
                  str(cond.get("last_day")))
            # And that it is *this* build's day, not whatever was there before.
            # Checking only that some day exists is how a stale layer passes:
            # the day that was already live is still a day.
            built = out / "conditions.json"
            if built.exists():
                mine = json.loads(built.read_text(encoding="utf-8")).get("last_day")
                check("the weather served is the weather built",
                      cond.get("last_day") == mine,
                      f"live {cond.get('last_day')} vs built {mine}")
            else:
                # No file in the build means the layer comes from KV, so the
                # build has nothing to compare against and the route is the
                # only source of truth.
                print("      (no conditions.json in the build - served from KV)")
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            check("the weather parses as JSON", False, str(e)[:80])

    status, _, ctype = get(origin + "/manifest.webmanifest")
    check("the manifest is served", status == 200, f"HTTP {status}")

    print()
    print("ALL CHECKS PASSED" if not fails else f"{fails} FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

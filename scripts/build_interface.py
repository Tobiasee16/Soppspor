"""Assemble the interface: shell + data payload + basemap sheets, one HTML file.

    python scripts/build_interface.py <shell.html> <out.html>

A published artifact runs under a content policy that blocks external images
and network calls, so every byte the page needs has to be inside it. This
inlines the basemap JPEGs as data URIs alongside the suitability payload and
reports the total, because the page has a hard 16 MB ceiling and it is much
better to find that out here than at publish time.
"""
from __future__ import annotations

import base64
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "data" / "processed" / "web_preview" / "payload.js"
BASEMAP = ROOT / "data" / "interim" / "basemap"
COND = ROOT / "data" / "processed" / "conditions.json"
TRAILS = ROOT / "data" / "interim" / "trails.json"
SUPABASE = ROOT / "config" / "supabase.json"


def supabase_config() -> str:
    """Inline the Supabase project URL and anon key, if they have been set.

    The anon key belongs in client code - row-level security is what protects
    the rows, not the key's secrecy. The **service_role** key does not: it
    bypasses RLS entirely, so one in a published page would hand every user's
    private spots to every other user. That is the single worst mistake
    available in this codebase, it is one careless copy-paste away, and it
    would be invisible in review, so it is checked for here rather than warned
    about in a comment.
    """
    if not SUPABASE.exists():
        print("  no config/supabase.json - spots stay on the device "
              "(copy config/supabase.example.json to set it up)", flush=True)
        return "window.SOPP_SUPABASE=null;"

    cfg = json.loads(SUPABASE.read_text(encoding="utf-8"))
    url, anon = (cfg.get("url") or "").strip(), (cfg.get("anon") or "").strip()
    if not url or not anon or "YOUR-PROJECT-REF" in url or anon.startswith("PASTE"):
        raise SystemExit(
            "config/supabase.json still has placeholder values. Fill in the "
            "Project URL and the anon/publishable key, or delete the file to "
            "build without sync.")

    role = _jwt_role(anon)
    if role and role != "anon":
        raise SystemExit(
            f"REFUSING TO BUILD: the key in config/supabase.json is a "
            f"'{role}' key, not an anon key.\n"
            "A service_role key bypasses row-level security. Inlining one here "
            "would publish every user's private spots to every other user.\n"
            "Copy the key labelled 'anon' / 'publishable' instead.")
    if not url.startswith("https://"):
        raise SystemExit(f"Supabase URL must be https://, got {url!r}")

    print(f"  supabase: {url} (anon key, {len(anon)} chars)", flush=True)
    return ("window.SOPP_SUPABASE="
            + json.dumps({"url": url, "anon": anon}, separators=(",", ":")) + ";")


def _jwt_role(token: str) -> str | None:
    """The `role` claim from a Supabase JWT, or None if it is not one.

    Not verified - we are not authenticating anything here, only reading what
    the key says about itself so an obvious mistake can be caught. Newer
    Supabase keys are opaque (`sb_publishable_...` / `sb_secret_...`) rather
    than JWTs, so those are matched by prefix instead.
    """
    if token.startswith("sb_secret_"):
        return "service_role"
    if token.startswith("sb_publishable_"):
        return "anon"
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(pad)).get("role")
    except (ValueError, json.JSONDecodeError):
        return None


LIMIT_MB = 16.0


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: build_interface.py <shell.html> <out.html>")
    shell_path, out_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    if not PAYLOAD.exists():
        raise SystemExit(f"missing {PAYLOAD} - run scripts/export_web_preview.py")

    manifest_path = BASEMAP / "manifest.json"
    base_js = "window.SOPP_BASE={};"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sheets = {}
        for key, m in manifest.items():
            img = BASEMAP / m["file"]
            if not img.exists():
                print(f"  WARNING: {img.name} missing, sheet skipped", flush=True)
                continue
            b64 = base64.b64encode(img.read_bytes()).decode("ascii")
            # Overlays are PNG because they need an alpha channel; backgrounds
            # are JPEG because they do not and it halves them. Labelling a PNG
            # as JPEG makes the browser refuse it outright.
            mime = "image/png" if img.suffix == ".png" else "image/jpeg"
            sheets[key] = {"src": f"data:{mime};base64," + b64,
                           "bbox": m["bbox"], "kind": m.get("kind", "base")}
            print(f"  {key}: {len(b64) / 1e6:.2f} MB inlined", flush=True)
        base_js = "window.SOPP_BASE=" + json.dumps(sheets, separators=(",", ":")) + ";"
    else:
        print("  no basemap manifest - run scripts/fetch_basemap.py", flush=True)

    # Recent weather is inlined separately from the model payload, because it
    # is a different kind of thing on a different clock: the payload changes
    # when a model is refitted, this changes daily. Keeping them apart means a
    # weather refresh is a rebuild, not a re-export of the whole model surface.
    cond_js = "window.SOPP_COND=null;"
    if COND.exists():
        cond = json.loads(COND.read_text(encoding="utf-8"))
        cond_js = "window.SOPP_COND=" + json.dumps(cond, separators=(",", ":")) + ";"
        print(f"  conditions: {cond['last_day']} ({cond['lag_days']}d behind), "
              f"{len(COND.read_bytes()) / 1e3:.0f} kB", flush=True)
    else:
        print("  no conditions.json - run scripts/fetch_recent_weather.py "
              "(the map will show habitat only)", flush=True)

    # Marked routes as packed vector geometry. Small enough to ship, and the
    # only way to get a route line that stays the same thickness at every zoom.
    trails_js = "window.SOPP_TRAILS=null;"
    if TRAILS.exists():
        tr = json.loads(TRAILS.read_text(encoding="utf-8"))
        trails_js = "window.SOPP_TRAILS=" + json.dumps(tr, separators=(",", ":")) + ";"
        print(f"  trails: {tr['lines']} routes, {tr['vertices']} vertices, "
              f"{TRAILS.stat().st_size / 1e3:.0f} kB", flush=True)
    else:
        print("  no trails.json - run scripts/fetch_trails.py", flush=True)

    shell = shell_path.read_text(encoding="utf-8")
    marker = "<script>\n(function(){"
    if marker not in shell:
        raise SystemExit("shell has no '<script>\n(function(){' insertion point")

    payload = PAYLOAD.read_text(encoding="utf-8")
    injected = ("<script>" + payload + "</script>\n"
                "<script>" + base_js + "</script>\n"
                "<script>" + cond_js + "</script>\n"
                "<script>" + trails_js + "</script>\n"
                "<script>" + supabase_config() + "</script>\n" + marker)
    out = shell.replace(marker, injected, 1)
    out_path.write_text(out, encoding="utf-8")

    mb = len(out.encode("utf-8")) / 1e6
    print(f"\nassembled {mb:.2f} MB -> {out_path}")
    if mb > LIMIT_MB:
        raise SystemExit(f"OVER THE {LIMIT_MB:.0f} MB ARTIFACT LIMIT by "
                         f"{mb - LIMIT_MB:.2f} MB - shrink the basemap sheets")
    print(f"{LIMIT_MB - mb:.2f} MB of headroom")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

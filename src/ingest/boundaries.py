"""Fetch administrative boundaries from Kartverket via the Geonorge API.

CLAUDE.md: "Pull both boundaries from Kartverket rather than hardcoding a bbox."

Endpoint verified 2026-09-09. Returns geometry already in the requested CRS,
so we ask for 25833 directly and never reproject.
"""
from __future__ import annotations

import json
import pathlib
import urllib.request

TIMEOUT = 120


def fetch(url: str, dest: pathlib.Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        if r.status != 200:
            raise RuntimeError(f"{url} returned HTTP {r.status}")
        payload = json.loads(r.read().decode("utf-8"))

    geom = payload.get("omrade")
    if geom is None:
        raise RuntimeError(
            f"{url} returned no 'omrade' key. Endpoint may have changed - "
            f"stop and verify rather than working around this. Keys: {list(payload)}"
        )
    crs = (geom.get("crs") or {}).get("properties", {}).get("name")
    feature = {
        "type": "Feature",
        "properties": {
            k: v for k, v in payload.items() if k != "omrade" and not isinstance(v, (dict, list))
        },
        "geometry": {"type": geom["type"], "coordinates": geom["coordinates"]},
    }
    fc = {
        "type": "FeatureCollection",
        "crs_declared_by_source": crs,
        "source_url": url,
        "features": [feature],
    }
    dest.write_text(json.dumps(fc), encoding="utf-8")
    return fc

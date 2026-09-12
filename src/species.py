"""Which species the pipeline runs, and which ones the Phase 0 test judges.

`config/species.yaml` holds two lists and they mean different things.

`species:` is the three-species falsification instrument. Their predicted
ordering was written into FINDINGS before any model existed, and that test has
already been run. Adding a species to that list after the fact would silently
change what was predicted, which is the one thing the pre-registration exists
to prevent - so the ordering test reads `registered()` and nothing else, for
good.

`additional_species:` is everything else the product maps. Same background,
same blocked CV, same transfer test, same null control running alongside; no
`predicted_rank`, and no entry in the ordering verdict.

Everything that *fits* a model wants `modelled()`. Everything that *judges the
registered prediction* wants `registered()`. Keeping the two apart in one place
means a script cannot quietly use the wrong one.
"""
from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "species.yaml"


def load(path: pathlib.Path | None = None) -> dict:
    return yaml.safe_load((path or CONFIG).read_text(encoding="utf-8"))


def registered(spp: dict) -> list[dict]:
    """The three species whose ordering was predicted in advance, in that order."""
    return list(spp["species"])


def additional(spp: dict) -> list[dict]:
    """Species added for the product, which no advance prediction covers."""
    return list(spp.get("additional_species") or [])


def modelled(spp: dict) -> list[dict]:
    """Every real species that gets fitted, mapped and shown. Excludes the null."""
    out = registered(spp) + additional(spp)
    keys = [sp["key"] for sp in out]
    if len(set(keys)) != len(keys):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        raise ValueError(
            f"species.yaml lists {dupes} in both `species:` and "
            f"`additional_species:`. A species in both would be fitted twice and "
            f"would appear twice in the ordering test.")
    return out


def is_registered(spp: dict, scientific_name: str) -> bool:
    return any(sp["scientific_name"] == scientific_name for sp in registered(spp))

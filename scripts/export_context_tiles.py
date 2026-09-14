"""Cut the card's "why this cell" layers into tiles the app can fetch.

    python scripts/export_context_tiles.py

Writes `data/processed/context_tiles/<row>_<col>.png` plus one `meta.json`.

The Kristiansand payload inlines seven context surfaces so that tapping a cell
can answer *why* - "granskog, ~40 ar, 210 moh." - instead of only "82nd
percentile". Over Norway those surfaces are 115 million cells each and cannot
be inlined, so they are tiled on exactly the grid the zone tiles use and
fetched when somebody actually taps.

**One tile holds every layer**, stacked vertically: a 256 x 256 block per
layer, one on top of the next, in a single greyscale PNG. A tap needs all of
them at once and they compress better together than apart, so one request per
tapped neighbourhood beats seven. The app already has a PNG decoder for the
zone tiles and this needs no second one.

Nothing here is a model input. These are the same rasters the model was fitted
on, shown back at the resolution the map draws, so that a score can be read
next to the ground it came from.

**There is no TWI in this set, and that is not an oversight.** Topographic
wetness needs flow accumulation over a whole connected surface, and 1.83
billion cells will not take a priority-flood in 15.7 GB - FINDINGS section 18
measured what dropping it costs before the decision was taken. The national
model does not have the feature either, so a card that showed it would be
describing a different model from the one that drew the zones.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[0]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import numpy as np
import rasterio

import export_zone_tiles as ezt
from run_models import NORGE
from src.ingest.ar50 import CATEGORIES as AR50_CAT
from src.ingest.ar50 import LABELS as AR50_LAB
from src.ingest.ar50 import NORWEGIAN as AR50_NO
from src.ingest.losmasser import CATEGORIES as LOS_CAT
from src.ingest.losmasser import LABELS as LOS_LAB

INTERIM = ROOT / "data" / "interim"
ZONES = ROOT / "data" / "processed" / "zone_tiles"
OUT = ROOT / "data" / "processed" / "context_tiles"
TILE = ezt.TILE

# Norwegian for the substrate classes, as in scripts/export_web_preview.py.
# These are Norwegian map categories and a Norwegian reader recognises them
# faster than any translation.
LOS_NO = {1: "Morene", 2: "Breelvavsetning", 3: "Marin avsetning",
          4: "Elveavsetning", 5: "Skredmateriale", 6: "Torv og myr",
          7: "Tynt organisk dekke", 8: "Fyllmasse", 9: "Bart fjell",
          10: "Vann / uspesifisert"}
TRESLAG_NO = {1: ("Gran", "Spruce"), 2: ("Furu", "Pine"), 3: ("Lauv", "Deciduous")}

# Layer order is the order of the blocks in every tile, and the app reads it
# from meta.json rather than assuming it - but it is also the order the card
# reads them out in, so it is the order a sentence wants: what kind of ground,
# what is growing on it, how old, how dense, what it sits on, how high, how wet
# a place it usually is.
LAYERS = [
    {"key": "ar50", "no": "arealtype", "en": "land cover", "kind": "cat",
     "src": ("grid32", "ar50"), "codes": AR50_CAT,
     "labels": {str(c): [AR50_NO[c], AR50_LAB[c].replace("_", " ")]
                for c in AR50_CAT}},
    {"key": "treslag", "no": "treslag", "en": "tree species", "kind": "cat",
     "src": ("sr16", "TRESLAG"), "codes": [1, 2, 3],
     "labels": {str(k): list(v) for k, v in TRESLAG_NO.items()}},
    {"key": "stand_age_yr", "no": "bestandsalder", "en": "stand age",
     "kind": "num", "src": ("sr16", "TREALDER")},
    {"key": "crown_cover_pct", "no": "kronedekning", "en": "crown cover",
     "kind": "num", "src": ("sr16", "KRONEDEK")},
    {"key": "losmasse", "no": "losmasse", "en": "substrate", "kind": "cat",
     "src": ("grid32", "losmasser"), "codes": LOS_CAT,
     "labels": {str(c): [LOS_NO.get(c, LOS_LAB[c]), LOS_LAB[c]]
                for c in LOS_CAT}},
    {"key": "elevation", "no": "hoyde", "en": "elevation", "kind": "num",
     "src": ("terrain", 1)},
    {"key": "autumn_rain_mm", "no": "hostnedbor", "en": "autumn rain",
     "kind": "num", "src": ("coarse", "climate")},
]


def target_grid() -> dict:
    """The zone grid, read from its own meta rather than recomputed.

    These tiles are indexed by the same cell numbers the zone tiles are, and a
    grid that drifted by one cell would put the reasoning of one place under
    the score of its neighbour - visible to nobody, wrong everywhere.
    """
    meta = json.loads((ZONES / "meta.json").read_text(encoding="utf-8"))
    lv = (meta.get("levels") or [None])[0] or {"grid": meta["grid"]}
    return {"cell_m": meta["cell_m"], "origin_x": meta["origin_x"],
            "origin_y": meta["origin_y"], "w": lv["grid"]["w"],
            "h": lv["grid"]["h"], "tile_px": meta["tile_px"],
            "tiles": [t for sp in meta["species"] for t in
                      (sp["tiles"][0] if isinstance(sp["tiles"][0], list)
                       else sp["tiles"])]}


def read_grid32(path: pathlib.Path, g: dict, kind: str, codes=None, band: int = 1):
    """Aggregate a raster on the 32 m grid down to the zone grid.

    Mean for a measurement, majority for a class. A mean of class codes would
    be a number that names nothing - halfway between bog and bare rock is not a
    substrate - which is the one place where the two aggregations are not a
    matter of taste.

    The target grid has to be *aligned* to the source, not identical to it: a
    whole number of source cells per target cell, starting on a source cell
    boundary. Requiring the same origin as well would be an accident of how the
    national rasters happen to be cut, and would make this untestable on any
    window smaller than the country.
    """
    out = np.zeros((g["h"], g["w"]), dtype="float32" if kind == "num" else "uint8")
    if kind == "num":
        out[:] = np.nan
    with rasterio.open(path) as src:
        t = src.transform
        f = g["cell_m"] / abs(t.a)
        cx = (g["origin_x"] - t.c) / t.a
        cy = (t.f - g["origin_y"]) / -t.e
        if any(abs(v - round(v)) > 1e-6 for v in (f, cx, cy)) or f < 1:
            raise SystemExit(f"{path.name} does not tile the zone grid "
                             f"(factor {f}, offset {cx}, {cy})")
        f, cx, cy = int(round(f)), int(round(cx)), int(round(cy))
        nod = src.nodata
        for r0 in range(0, g["h"], 128):
            r1 = min(r0 + 128, g["h"])
            win = rasterio.windows.Window(cx, cy + r0 * f, g["w"] * f, (r1 - r0) * f)
            blk = src.read(band, window=win, boundless=True,
                           fill_value=nod if nod is not None else 0)
            blk = blk.reshape(r1 - r0, f, g["w"], f)
            if kind == "num":
                a = blk.astype("float32")
                if nod is not None:
                    a[a == nod] = np.nan
                with np.errstate(invalid="ignore"):
                    ok = np.isfinite(a)
                    n = ok.sum(axis=(1, 3))
                    s = np.where(ok, a, 0.0).sum(axis=(1, 3))
                out[r0:r1] = np.where(n > 0, s / np.maximum(n, 1), np.nan)
            else:
                # Ties go to the first code in `codes`, which is stable across
                # runs. An unstable tiebreak would redraw class boundaries on
                # every export for no reason anyone could see.
                best = np.zeros((r1 - r0, g["w"]), dtype="uint8")
                bestn = np.zeros((r1 - r0, g["w"]), dtype="int32")
                for code in codes:
                    n = (blk == code).sum(axis=(1, 3))
                    take = n > bestn
                    best[take] = code
                    bestn[take] = n[take]
                out[r0:r1] = best
    return out


def read_sr16(path: pathlib.Path, g: dict, band: int = 1):
    """Sample SR16 at each cell centre, reading only the rows that carries.

    SR16 is 16 m on its own origin, and the offsets work out exactly: a cell
    centre on the zone grid lands on SR16 row 2332 + 8r and column 8c with no
    rounding at all. So this is nearest-neighbour sampling, which is also what
    the model does - `stack.RasterLayer` samples SR16 by nearest neighbour
    because a class code cannot be interpolated and the numeric bands share its
    missing-data pattern.

    The file is striped one row per block, so reading every eighth row costs
    an eighth of the file rather than all of it.
    """
    with rasterio.open(path) as src:
        t = src.transform
        c0 = (g["origin_x"] + g["cell_m"] / 2 - t.c) / t.a
        r0 = (t.f - (g["origin_y"] - g["cell_m"] / 2)) / -t.e
        step = g["cell_m"] / abs(t.a)
        if abs(c0 - round(c0)) > 1e-6 or abs(r0 - round(r0)) > 1e-6 or \
           abs(step - round(step)) > 1e-6:
            raise SystemExit(f"{path.name} does not line up with the zone grid")
        c0, r0, step = int(round(c0)), int(round(r0)), int(round(step))
        cols = c0 + step * np.arange(g["w"])
        if cols[-1] >= src.width:
            raise SystemExit(f"{path.name} is too narrow for the zone grid")
        out = np.zeros((g["h"], g["w"]), dtype="float32")
        nod = src.nodata
        for r in range(g["h"]):
            sr = r0 + step * r
            if sr < 0 or sr >= src.height:
                out[r] = np.nan
                continue
            row = src.read(band, window=rasterio.windows.Window(0, sr, src.width, 1))
            v = row[0, cols].astype("float32")
            if nod is not None:
                v[v == nod] = np.nan
            out[r] = v
    return out


def read_coarse(path: pathlib.Path, g: dict, band: int = 1):
    """A raster coarser than the zone grid, sampled at cell centres."""
    with rasterio.open(path) as src:
        a = src.read(band).astype("float32")
        if src.nodata is not None:
            a[a == src.nodata] = np.nan
        t = src.transform
        e = g["origin_x"] + (np.arange(g["w"]) + 0.5) * g["cell_m"]
        n = g["origin_y"] - (np.arange(g["h"]) + 0.5) * g["cell_m"]
        cx = np.floor((e - t.c) / t.a).astype("int64")
        cy = np.floor((t.f - n) / -t.e).astype("int64")
        okx, oky = (cx >= 0) & (cx < src.width), (cy >= 0) & (cy < src.height)
        out = np.full((g["h"], g["w"]), np.nan, dtype="float32")
        out[np.ix_(oky, okx)] = a[np.ix_(cy[oky], cx[okx])]
    return out


def to_bytes(a: np.ndarray, kind: str) -> tuple[np.ndarray, float, float]:
    """Byte-encode a layer the way the interface already decodes one.

    `val()` in shell.html reads `min + (b-1)/254*(max-min)` and treats 0 as no
    value, so a class layer is encoded with min 0 and max 254 - which makes the
    byte the code plus one and the decode exact rather than nearly exact.
    """
    if kind == "cat":
        out = np.zeros(a.shape, dtype="uint8")
        keep = a > 0
        out[keep] = np.clip(a[keep].astype("int32") + 1, 1, 255).astype("uint8")
        return out, 0.0, 254.0
    finite = np.isfinite(a)
    if not finite.any():
        return np.zeros(a.shape, dtype="uint8"), 0.0, 1.0
    lo, hi = float(np.nanmin(a)), float(np.nanmax(a))
    if hi <= lo:
        hi = lo + 1.0
    out = np.zeros(a.shape, dtype="uint8")
    out[finite] = np.clip(
        np.round((a[finite] - lo) / (hi - lo) * 254.0) + 1, 1, 255).astype("uint8")
    return out, lo, hi


def main() -> int:
    if not (ZONES / "meta.json").exists():
        raise SystemExit(f"missing {ZONES / 'meta.json'} - run export_zone_tiles.py")
    g = target_grid()
    print(f"zone grid {g['w']:,} x {g['h']:,} at {g['cell_m']:.0f} m", flush=True)

    planes, meta_layers = [], []
    for spec in LAYERS:
        t0 = time.time()
        how, what = spec["src"]
        if how == "grid32":
            path = INTERIM / (NORGE.ar50 if what == "ar50" else NORGE.losmasser)
            arr = read_grid32(path, g, spec["kind"], spec.get("codes"))
        elif how == "sr16":
            path = INTERIM / NORGE.sr16.format(code=what)
            arr = read_sr16(path, g)
        elif how == "terrain":
            path = INTERIM / NORGE.terrain
            arr = read_grid32(path, g, "num", band=what)
        else:
            path = INTERIM / NORGE.climate
            arr = read_coarse(path, g)
        if not path.exists():
            raise SystemExit(f"missing {path}")
        byts, lo, hi = to_bytes(arr, spec["kind"])
        planes.append(byts)
        entry = {k: spec[k] for k in ("key", "no", "en", "kind")}
        entry.update(min=round(lo, 4), max=round(hi, 4))
        if "labels" in spec:
            entry["labels"] = spec["labels"]
        meta_layers.append(entry)
        held = int((byts != 0).sum())
        print(f"  {spec['key']:18s}{held:>13,} cells  "
              f"{lo:9.2f}..{hi:<9.2f} ({time.time()-t0:.0f}s)", flush=True)
        del arr

    OUT.mkdir(parents=True, exist_ok=True)
    want = sorted(set(g["tiles"]))
    print(f"cutting {len(want)} tiles, {len(planes)} layers each", flush=True)
    written, total = [], 0
    for tid in want:
        r, c = (int(x) for x in tid.split("_"))
        stack = np.zeros((TILE * len(planes), TILE), dtype="uint8")
        empty = True
        for i, plane in enumerate(planes):
            block = plane[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE]
            stack[i * TILE:i * TILE + block.shape[0], :block.shape[1]] = block
            empty = empty and not block.any()
        if empty:
            continue
        data = ezt.png_gray(stack)
        (OUT / f"{tid}.png").write_bytes(data)
        written.append(tid)
        total += len(data)

    meta = {"crs": "EPSG:25833", "cell_m": g["cell_m"], "tile_px": TILE,
            "origin_x": g["origin_x"], "origin_y": g["origin_y"],
            "grid": {"w": g["w"], "h": g["h"]},
            "encoding": "one 256-row block per layer, stacked in layer order",
            "layers": meta_layers, "tiles": written}
    (OUT / "meta.json").write_text(json.dumps(meta, separators=(",", ":")),
                                   encoding="utf-8")
    print(f"\n{len(written):,} tiles, {total/1e6:.1f} MB "
          f"({total/max(len(written),1)/1024:.0f} kB each)")
    print(f"-> {(OUT / 'meta.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

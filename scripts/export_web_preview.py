"""Compact data payload for the Phase 2 interface prototype.

    python scripts/export_web_preview.py

Downsamples the 16 m suitability rasters and the feature layers behind them to
a coarse grid, quantises each to a byte, and writes a base64 payload a browser
can hold in memory. Writes `data/processed/web_preview/payload.js`.

**This is a viewing resolution, not a modelling one.** The GeoTIFFs remain the
artefact at 16 m; nothing here feeds a model, and no number in FINDINGS comes
from this file. The prototype exists so the reasoning behind a cell can be
shown to a person, which is the one thing a static PNG cannot do.

Two things it carries deliberately:

* **The feature values behind each cell**, so the interface can show its
  working rather than a bare number. CLAUDE.md's design direction asks for
  exactly this - "score first, tap reveals the reasoning".
* **The suppression mask**, so the interface can say *where it is deliberately
  vague and why*. A blurred area that does not admit it is blurred is worse
  than no suppression at all, because the reader trusts detail that is not
  there.
"""
from __future__ import annotations

import base64
import json
import pathlib
import sys

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
import yaml

import build_maps as bm
import run_models as rm
from src import species as species_cfg
from src.features.stack import sample_layers
from src.model.predict import grid_coords

OUT_DIR = ROOT / "data" / "processed" / "web_preview"
FACTOR = 8                      # 16 m -> 128 m
NODATA_BYTE = 0                 # 1..255 carry data, 0 means "no value"

def species_labels(spp: dict) -> list[tuple[str, str, str, str]]:
    """(key, scientific name, Norwegian, English) for everything to be mapped.

    Read from config rather than restated here. The hardcoded list this replaces
    had drifted into being a second, shorter source of truth: adding a species
    to `config/species.yaml` fitted it, scored it and wrote its GeoTIFF, and
    then silently dropped it from the interface for want of an entry here.

    The null control is deliberately absent. It is a validation device with no
    ecology in it, it is reported in model_results.md and described in the
    app's Om page, and its GeoTIFF is still written by build_maps.py as a Phase
    0 artefact - but a phone has no use for a 236 kB raster it must never
    draw, and shipping one invites it being drawn by accident.
    """
    return [(sp["key"], sp["scientific_name"],
             sp["vernacular_no"][:1].upper() + sp["vernacular_no"][1:],
             sp.get("vernacular_en") or sp["scientific_name"])
            for sp in species_cfg.modelled(spp)]

# Feature layers surfaced in the reasoning panel. Kept short on purpose: the
# design direction is one idea per screen, and a panel listing 31 features is
# a data dump, not an explanation.
CONTEXT = [
    ("elevation", "elevation", "moh.", "m a.s.l.", 0),
    ("twi", "twi", "fuktighetsindeks", "wetness index", 1),
    ("stand_age_yr", "stand_age_yr", "bestandsalder", "stand age", 0),
    ("crown_cover_pct", "crown_cover_pct", "kronedekning", "crown cover", 0),
    ("autumn_rain_mm", "autumn_rain_mm", "høstnedbør", "autumn rainfall", 0),
]
CATEGORICAL = [("treslag", "treslag"), ("losmasse", "losmasse")]


def block_mean(arr: np.ndarray, factor: int) -> np.ndarray:
    """Mean over factor x factor blocks, ignoring NaN."""
    h = (arr.shape[0] // factor) * factor
    w = (arr.shape[1] // factor) * factor
    a = arr[:h, :w].reshape(h // factor, factor, w // factor, factor)
    with np.errstate(invalid="ignore"):
        return np.nanmean(a, axis=(1, 3))


def block_mode(arr: np.ndarray, factor: int, n_classes: int) -> np.ndarray:
    """Most common class over each block. Nearest would just pick a corner."""
    h = (arr.shape[0] // factor) * factor
    w = (arr.shape[1] // factor) * factor
    a = arr[:h, :w].reshape(h // factor, factor, w // factor, factor)
    a = a.transpose(0, 2, 1, 3).reshape(h // factor, w // factor, factor * factor)
    out = np.zeros(a.shape[:2], dtype="float64")
    best = np.zeros(a.shape[:2], dtype="int32")
    for c in range(1, n_classes + 1):
        n = (a == c).sum(axis=2)
        take = n > best
        out[take] = c
        best[take] = n[take]
    out[best == 0] = np.nan
    return out


def quantise(arr: np.ndarray) -> tuple[str, float, float]:
    """Scale finite values into 1..255 and base64 the bytes. 0 means no value."""
    finite = np.isfinite(arr)
    if not finite.any():
        return "", 0.0, 0.0
    lo = float(np.nanmin(arr))
    hi = float(np.nanmax(arr))
    out = np.zeros(arr.shape, dtype="uint8")
    if hi > lo:
        scaled = (arr[finite] - lo) / (hi - lo)
        out[finite] = np.clip(np.round(scaled * 254.0) + 1, 1, 255).astype("uint8")
    else:
        out[finite] = 128
    return base64.b64encode(out.tobytes()).decode("ascii"), lo, hi


def _drop_species_that_failed_validation(payload: dict, null_auc: float | None) -> None:
    """Remove species whose held-out AUC cannot be told apart from the null.

    The rule, stated so it is a rule and not a judgement call made once the
    numbers were in: **a species ships only if the lower bound of its transfer
    bootstrap interval sits above the synthetic null's transfer AUC.** The null
    has no ecology in it by construction, so a species that cannot clear it on
    the held-out extent has not been shown to know anything about Kristiansand.

    This is not the same thing as dropping a species because it scored badly.
    CLAUDE.md forbids that, and it is not happening: every species stays in
    model_results.md, in transferability.md, in FINDINGS and in the GeoTIFF
    artefacts, with its number printed. What it cannot do is appear in a phone
    app, as a blue map of a wood, to someone who is about to spend a Saturday
    walking into it. Those are different bars and the second one is higher.

    Applied automatically rather than by hand so that a future refit cannot
    quietly promote a species back without the rule being met - or leave one
    shipping after its evidence has gone.
    """
    if null_auc is None:
        print("  no null AUC in transferability.md - shipping every species "
              "unchecked", flush=True)
        return
    keep, dropped = [], []
    for sp in payload["species"]:
        ci = sp.get("transfer_ci") or ""
        lo = None
        if "-" in ci:
            try:
                lo = float(ci.split("-")[0])
            except ValueError:
                lo = None
        if lo is not None and lo <= null_auc:
            dropped.append((sp, lo))
        else:
            keep.append(sp)
    for sp, lo in dropped:
        print(f"  DROPPED {sp['key']}: transfer AUC {sp.get('transfer_auc')} "
              f"(CI from {lo:.3f}) does not clear the null's {null_auc:.3f} on "
              f"{sp.get('held_out_n', '?')} held-out records", flush=True)
    payload["species"] = keep
    payload["meta"]["excluded_species"] = [
        {"key": sp["key"], "no": sp["no"], "scientific": sp["scientific"],
         "transfer_auc": sp.get("transfer_auc"), "transfer_ci": sp.get("transfer_ci"),
         "held_out_n": sp.get("held_out_n")}
        for sp, _ in dropped]
    payload["meta"]["null_transfer_auc"] = null_auc


def main() -> int:
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))
    SPECIES = species_labels(spp)
    tifs = {k: bm.MAPS / f"suitability_{k}_maxent_16m.tif" for k, *_ in SPECIES}
    missing = [str(p) for p in tifs.values() if not p.exists()]
    if missing:
        raise SystemExit(f"missing rasters, run scripts/build_maps.py first: {missing}")

    with rasterio.open(next(iter(tifs.values()))) as src:
        transform, height, width = src.transform, src.height, src.width
        tags = src.tags()

    payload: dict = {
        "grid": {},
        "species": [],
        "context": {},
        "categorical": {},
        "meta": {
            "cell_m": FACTOR * 16.0,
            "source_cell_m": 16.0,
            "crs": "EPSG:25833",
            # The grid's own origin, so the interface can report real UTM
            # coordinates instead of pixel indices.
            "origin_x": float(transform.c),
            "origin_y": float(transform.f),
            "model": tags.get("model", ""),
            "fitted_on": tags.get("fitted_on", ""),
            "suppression": tags.get("suppression", ""),
        },
    }

    # Grid geometry comes from one of the exported rasters, so the preview and
    # the GeoTIFFs describe the same ground.
    with rasterio.open(tifs[SPECIES[0][0]]) as src:
        probe = block_mean(src.read(1, masked=True).astype("float64").filled(np.nan),
                           FACTOR)
    gh, gw = int(probe.shape[0]), int(probe.shape[1])
    payload["grid"] = {"h": gh, "w": gw}

    # --- the features behind the score, sampled through the SAME stack ---
    # Not re-read independently: `sample_layers` is what the model saw, and a
    # reasoning panel showing different numbers from the ones that produced the
    # score would be worse than showing none.
    original = rm.INTERIM
    rm.INTERIM = bm.CLIPPED
    stack = rm.layers()
    rm.INTERIM = original

    centres_e, centres_n = [], []
    for r in range(gh):
        east, north, _ = grid_coords(transform, height, width,
                                     slice(r * FACTOR + FACTOR // 2,
                                           r * FACTOR + FACTOR // 2 + 1))
        centres_e.append(east[0, FACTOR // 2::FACTOR][:gw])
        centres_n.append(north[0, FACTOR // 2::FACTOR][:gw])
    coords = list(zip(np.concatenate(centres_e), np.concatenate(centres_n)))
    print(f"sampling {len(coords):,} cell centres through the model stack ...",
          flush=True)
    X, names = sample_layers(stack, coords)
    idx = {n: i for i, n in enumerate(names)}

    # --- suitability, computed here rather than read back from the GeoTIFFs ---
    #
    # The exported rasters are already screened for sensitive habitat, and a
    # blur cannot be undone. The interface needs the unscreened surface so that
    # suppression can be a control the reader can see and reason about rather
    # than an invisible effect baked into the pixels. So the models are refitted
    # on exactly the same split `build_maps.py` uses - Agder minus Kristiansand
    # minus the buffer - and predicted onto these 128 m cell centres.
    #
    # The GeoTIFFs on disk stay screened. This payload carries the raw surface
    # plus the mask, and the page applies the blur itself.
    import json as _json

    from src.features.background import null_and_background
    from src.features.stack import build
    from src.geometry import Boundary
    from src.model.models import MaxentStyle
    from src.validate.harness import _excluded_by_buffer

    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    buffer_m = area["spatial_cv"]["buffer_m"]
    train_cfg, pred_cfg = area["training_extent"], area["prediction_extent"]
    training = Boundary(_json.loads((ROOT / train_cfg["boundary_file"]).read_text(
        encoding="utf-8"))["features"][0]["geometry"], name=train_cfg["name"])
    prediction = Boundary(_json.loads((ROOT / pred_cfg["boundary_file"]).read_text(
        encoding="utf-8"))["features"][0]["geometry"], name=pred_cfg["name"])

    full_stack = rm.layers()
    pool = [{"east": e, "north": n, "precision_m": pr, "id": i}
            for e, n, pr, i in _json.loads(
                (ROOT / "data" / "interim" / "target_group_pool_agder.json"
                 ).read_text(encoding="utf-8"))["pool"]]
    sets = {sp["scientific_name"]: rm.presences(
                sp, training, area["max_coordinate_precision_m"],
                area["reporting_thinning_m"]) for sp in species_cfg.modelled(spp)}
    null_set, bg_set = null_and_background(
        pool, n_null=max(len(v) for v in sets.values()), seed=0)
    sets[spp["null_control"]["label"]] = null_set.coords

    by_name = {sci: (key, no, en) for key, sci, no, en in SPECIES}
    inside_grid = np.array([(e, n) in prediction for e, n in coords])
    print(f"{int(inside_grid.sum()):,} of {len(coords):,} cells inside the kommune",
          flush=True)

    for sci, presence_coords in sets.items():
        if sci not in by_name:      # the null control: fitted and mapped, not shipped
            continue
        key, no, en = by_name[sci]
        d = build(presence_coords, bg_set.coords, full_stack)
        pts = [tuple(c) for c in d.coords]
        y = list(d.y)
        Xd = [list(r) for r in d.X]
        in_pred = [(e, n) in prediction for e, n in pts]
        te = [i for i, f in enumerate(in_pred) if f]
        tr = [i for i, f in enumerate(in_pred) if not f]
        dropped = _excluded_by_buffer(pts, tr, te, buffer_m)
        tr = [i for i in tr if i not in dropped]
        predict = MaxentStyle().fit([Xd[i] for i in tr], [y[i] for i in tr])

        scores = np.full(len(coords), np.nan)
        usable = inside_grid & np.isfinite(X).all(axis=1)
        if usable.any():
            scores[usable] = np.asarray(predict(X[usable].tolist()), dtype="float64")
        grid = scores.reshape(gh, gw)
        b64, lo, hi = quantise(grid)
        payload["species"].append({
            "key": key, "scientific": sci, "no": no, "en": en,
            "data": b64, "min": lo, "max": hi,
            "is_null": False,
        })
        print(f"  {key}: fitted on {len(tr)} rows, range {lo:.3f}-{hi:.3f}",
              flush=True)

    for key, col, label_no, label_en, decimals in CONTEXT:
        if col not in idx:
            continue
        vals = X[:, idx[col]].reshape(gh, gw)
        b64, lo, hi = quantise(vals)
        payload["context"][key] = {"data": b64, "min": lo, "max": hi,
                                   "no": label_no, "en": label_en,
                                   "decimals": decimals}

    # Categorical layers come back one-hot; fold them back to a class index so
    # the panel can name the class instead of listing ten zeroes.
    treslag_names = {1: ("Gran", "Spruce"), 2: ("Furu", "Pine"),
                     3: ("Lauv", "Deciduous")}
    cols = [idx.get(f"treslag_{c}") for c in (1, 2, 3)]
    if all(c is not None for c in cols):
        cls = np.zeros(len(coords))
        for i, c in enumerate(cols, start=1):
            cls[X[:, c] > 0.5] = i
        cls[cls == 0] = np.nan
        b64, lo, hi = quantise(cls.reshape(gh, gw))
        payload["categorical"]["treslag"] = {
            "data": b64, "min": lo, "max": hi,
            "no": "treslag", "en": "tree species",
            "labels": {str(k): v for k, v in treslag_names.items()},
        }

    from src.ingest.losmasser import CATEGORIES as LOS_CAT
    from src.ingest.losmasser import LABELS as LOS_LAB

    # Norwegian for the substrate classes. The interface is bilingual because
    # CLAUDE.md's design direction asks for it, and because these are Norwegian
    # map categories that a Norwegian reader will recognise faster than any
    # translation of them.
    LOS_NO = {1: "Morene", 2: "Breelvavsetning", 3: "Marin avsetning",
              4: "Elveavsetning", 5: "Skredmateriale", 6: "Torv og myr",
              7: "Tynt organisk dekke", 8: "Fyllmasse", 9: "Bart fjell",
              10: "Vann / uspesifisert"}
    los_cols = [idx.get(f"losmasse_{LOS_LAB[c]}") for c in LOS_CAT]
    if all(c is not None for c in los_cols):
        cls = np.zeros(len(coords))
        for c, col in zip(LOS_CAT, los_cols):
            cls[X[:, col] > 0.5] = c
        cls[cls == 0] = np.nan
        b64, lo, hi = quantise(cls.reshape(gh, gw))
        payload["categorical"]["losmasse"] = {
            "data": b64, "min": lo, "max": hi,
            "no": "løsmasse", "en": "substrate",
            "labels": {str(c): [LOS_NO.get(c, LOS_LAB[c]), LOS_LAB[c]]
                       for c in LOS_CAT},
        }

    # AR50 land cover. The reason it is in the panel at all: SR16 maps only
    # productive forest, so without this every non-forest cell reads as a bare
    # "no forest data" and the reasoning stops there. This is what turns that
    # into "myr" or "jordbruksareal".
    from src.ingest.ar50 import CATEGORIES as AR50_CAT
    from src.ingest.ar50 import LABELS as AR50_LAB
    from src.ingest.ar50 import NORWEGIAN as AR50_NO
    ar_cols = [idx.get(f"ar50_{AR50_LAB[c]}") for c in AR50_CAT]
    if all(c is not None for c in ar_cols):
        cls = np.zeros(len(coords))
        for c, col in zip(AR50_CAT, ar_cols):
            cls[X[:, col] > 0.5] = c
        cls[cls == 0] = np.nan
        b64, lo, hi = quantise(cls.reshape(gh, gw))
        payload["categorical"]["ar50"] = {
            "data": b64, "min": lo, "max": hi,
            "no": "arealtype", "en": "land cover",
            "labels": {str(c): [AR50_NO[c], AR50_LAB[c].replace("_", " ")]
                       for c in AR50_CAT},
        }

    # --- how well each species actually transfers ---
    # Parsed from the report rather than typed in here. An accuracy figure
    # hand-copied into an interface is a figure that goes stale the next time
    # the model is refitted, and a stale accuracy claim on a map is worse than
    # none: the reader has no way to know it drifted.
    transfer_md = ROOT / "data" / "processed" / "transferability.md"
    null_auc = None
    if transfer_md.exists():
        wanted = {sci: key for key, sci, *_ in SPECIES}
        null_label = spp["null_control"]["label"]
        # Only the table under `## Result` carries AUCs. The report ends with a
        # second table -- training rows kept per model -- whose rows also start
        # with a species name and also say `maxent-style`, and whose third cell
        # is a row count. Scanning the whole file let that table win, and the
        # interface showed "3051.00 AUC". Anchor on the heading, and refuse
        # anything outside [0, 1] as a second line of defence.
        section = ""
        for line in transfer_md.read_text(encoding="utf-8").splitlines():
            if line.startswith("##"):
                section = line.lstrip("# ").strip().lower()
                continue
            if section != "result":
                continue
            if not line.startswith("|") or "maxent-style" not in line:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 4:
                continue
            try:
                auc = float(cells[2])
            except ValueError:
                continue
            if cells[0] == null_label:
                null_auc = auc
                continue
            key = wanted.get(cells[0])
            if not key:
                continue
            if not 0.0 <= auc <= 1.0:
                raise SystemExit(
                    f"transferability.md: AUC {auc} for {cells[0]} is not a "
                    "probability. The report layout has changed; fix the parser "
                    "rather than shipping the number."
                )
            for sp in payload["species"]:
                if sp["key"] == key:
                    sp["transfer_auc"] = auc
                    sp["transfer_ci"] = cells[3]
        # How many held-out records that AUC rests on. Without it, 0.63 from 53
        # records and 0.63 from 8 records look identical in the picker, and the
        # second is close to meaningless - the species added for the product
        # have far thinner Kristiansand coverage than the original three, so
        # this is the number that stops a reader over-trusting them.
        section = ""
        for line in transfer_md.read_text(encoding="utf-8").splitlines():
            if line.startswith("##"):
                section = line.lstrip("# ").strip().lower()
                continue
            if not section.startswith("held-out record counts"):
                continue
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 3:
                continue
            key = wanted.get(cells[0])
            if not key:
                continue
            try:
                held = int(cells[2])
            except ValueError:
                continue
            for sp in payload["species"]:
                if sp["key"] == key:
                    sp["held_out_n"] = held

        got = [sp["key"] for sp in payload["species"] if "transfer_auc" in sp]
        print(f"transfer AUC attached for: {', '.join(got) or 'nothing'}", flush=True)
        thin_sets = [(sp["key"], sp["held_out_n"]) for sp in payload["species"]
                     if sp.get("held_out_n", 999) < 20]
        for key, n in thin_sets:
            print(f"  NOTE {key}: only {n} held-out records behind its AUC",
                  flush=True)

        _drop_species_that_failed_validation(payload, null_auc)

    # --- where the map is deliberately vague, and why ---
    from src.features.suppress import _polygon_mask
    sensitive = bm.sensitive_polygons()
    mask16 = _polygon_mask((height, width), transform, sensitive,
                           bm.SUPPRESSION_RADIUS_M)
    small_mask = block_mean(mask16.astype("float64"), FACTOR)
    payload["suppressed"] = base64.b64encode(
        (small_mask > 0.5).astype("uint8").tobytes()).decode("ascii")
    payload["meta"]["suppressed_pct"] = float(100.0 * (small_mask > 0.5).mean())
    payload["meta"]["sensitive_sites"] = len(sensitive)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    js = OUT_DIR / "payload.js"
    js.write_text("window.SOPP=" + json.dumps(payload, separators=(",", ":")) + ";",
                  encoding="utf-8")
    print(f"written: {js.relative_to(ROOT)}  ({js.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

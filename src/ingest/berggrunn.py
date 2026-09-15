"""NGU bedrock lime content (kalkinnhold), on the NiN KA ecocline.

CLAUDE.md lists substrate because "calcareous vs. acidic substrate strongly
structures fungal communities". Løsmasser, the layer actually fitted, classifies
by depositional *origin*; this one classifies by chemistry, which is what the
ecology is about. FINDINGS section 30 is what it showed.

Unlike `src/ingest/losmasser.py`, **nothing is grouped here.** NGU has already
placed each rock type on the five-step NiN `KA` scale, so the judgement that
module had to make by hand - which codes belong together - was made upstream by
the data custodian and is simply read. The lookup is a clean function: 109 rock
codes, none mapping to two KA values, checked across the main rock column and
all three subordinate ones.

The layer is fetched and documented but **not wired into any model**. See
FINDINGS 30f for the three reasons, the first of which is that the spread
between species is about a tenth of a standard deviation.

THE TRAP THIS MODULE EXISTS TO GUARD
------------------------------------
The obvious way to ask whether a species prefers lime-rich ground is to compare
its records' mean KA against the target-group background's. Done that way,
*Craterellus cornucopioides* came out at **-0.248** - strongly avoiding lime,
the opposite of what its ecology says, and the largest effect of any species.

It is entirely geography. The species is southern, southern Norway is acid
Precambrian basement, and the national background is dominated by ground the
species never occupies. Matching the background to the same 25 km blocks the
species was recorded in turns that -0.248 into +0.057.

So `block_matched_shift` is the only comparison this module offers, and
`naive_shift` exists solely so a test can show the two disagreeing.
"""
from __future__ import annotations

# The NiN "kalkinnhold" ecocline, as NGU publishes it. Not our scale.
KA_LABELS: dict[int, str] = {
    1: "Svært kalkfattig",
    2: "Temmelig kalkfattig",
    3: "Svak intermediær",
    4: "Litt kalkrik",
    5: "Svært kalkrik",
}
KA_MIN, KA_MAX = 1, 5

# Geonorge catalogue identifiers, resolved 2026-09-16. See data/SOURCES.md.
KALKINNHOLD_UUID = "f5aeef1c-a080-40a9-b509-5e8a2f846928"
KALKINNHOLD_LAYER = "Kalkinnhold_berggrunnN250"
OKOLOGI_WMS = "https://geo.ngu.no/mapserver/BerggrunnOkologiWMS"
BERGGRUNN_WMS = "https://geo.ngu.no/mapserver/BerggrunnWMS3"

# The download is offered in EPSG:32633 only, and as a file geodatabase only.
# Reprojected on ingest like everything else; the project CRS does not move.
KALKINNHOLD_CRS = "EPSG:32633"

# Matching distance for the background comparison. A fylke is far too coarse -
# Innlandet spans acid basement and calcareous valley alike - and below about
# 10 km the blocks start to contain only one bedrock polygon, which matches the
# signal away along with the confound.
DEFAULT_BLOCK_M = 25_000

# Blocks holding fewer background points than this give a mean too noisy to
# subtract, and dropping them is declared rather than silent.
MIN_BACKGROUND_PER_BLOCK = 5


def block_id(east: float, north: float, block_m: float = DEFAULT_BLOCK_M) -> int:
    """A single integer naming the block a coordinate falls in.

    Packed rather than a tuple so the result can be a numpy/pandas key. The
    100000 multiplier is far wider than Norway is in blocks (about 50 at 25 km),
    so easting and northing cannot collide.
    """
    return int(east // block_m) * 100_000 + int(north // block_m)


def naive_shift(species_ka, background_ka) -> float:
    """Mean KA at a species' records minus mean KA at the background's.

    **Do not use this to conclude anything.** It is the comparison that reported
    *C. cornucopioides* avoiding lime-rich ground, an effect that was entirely
    latitude. It is kept, and exported, so `tests/test_berggrunn.py` can plant a
    geographic confound and show this function reporting it as substrate while
    `block_matched_shift` does not.
    """
    sp = [float(v) for v in species_ka]
    bg = [float(v) for v in background_ka]
    if not sp or not bg:
        raise ValueError("both sets must be non-empty")
    return sum(sp) / len(sp) - sum(bg) / len(bg)


def block_matched_shift(species, background, block_m: float = DEFAULT_BLOCK_M,
                        min_background: int = MIN_BACKGROUND_PER_BLOCK):
    """How much richer the rock under a species is than under fungi in general.

    Both arguments are iterables of `(east, north, ka)`. The species' records
    are grouped into blocks; each block's background mean is subtracted from its
    species mean; the per-block differences are averaged weighted by how many
    records of *this species* the block holds. Blocks the background cannot
    speak for are dropped.

    So the question asked is "among records from this neighbourhood, does this
    species sit on richer rock than fungi in general do here?" - which the
    geography cannot answer for, because both sides are drawn from the same
    neighbourhoods in the same proportions.

    Returns `(shift, n_used, n_blocks)`. `shift` is NaN when no block survives,
    which is a real outcome for a species recorded where the background is thin
    and must not be read as zero.
    """
    bg_sum: dict[int, float] = {}
    bg_n: dict[int, int] = {}
    for east, north, ka in background:
        b = block_id(east, north, block_m)
        bg_sum[b] = bg_sum.get(b, 0.0) + float(ka)
        bg_n[b] = bg_n.get(b, 0) + 1

    sp_sum: dict[int, float] = {}
    sp_n: dict[int, int] = {}
    for east, north, ka in species:
        b = block_id(east, north, block_m)
        if bg_n.get(b, 0) < min_background:
            continue
        sp_sum[b] = sp_sum.get(b, 0.0) + float(ka)
        sp_n[b] = sp_n.get(b, 0) + 1

    if not sp_n:
        return float("nan"), 0, 0

    total = sum(sp_n.values())
    shift = sum(
        (sp_sum[b] / sp_n[b] - bg_sum[b] / bg_n[b]) * (sp_n[b] / total)
        for b in sp_n
    )
    return shift, total, len(sp_n)

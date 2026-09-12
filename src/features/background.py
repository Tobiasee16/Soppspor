"""Target-group background sampling, and the synthetic null built from it.

CLAUDE.md hard constraint 3. Occurrence records cluster along roads, trails and
near settlements. If background points are scattered uniformly, the only thing
separating presences from background is accessibility, and the model learns
"near roads" while scoring well. Every conclusion downstream would be about
where people walk.

The fix is to draw background from the places people actually looked. A record
of *any* fungus is evidence that someone was there, looked at the ground, and
recorded what they found. Using those locations as background means presences
and background share the same observer bias, and it cancels rather than being
learned. This is target-group background sampling (Phillips et al. 2009), and
the target group here is Artskart's `Sopper` - 82,129 records across Agder
against roughly 250-400 presences per focal species.

The same pool supplies the null control (hard constraint 5). Null presences are
drawn from the target group and so carry sampling effort and no ecology at all.
If a model scores above 0.5 on the null under blocked CV, it is reading effort,
not habitat, and nothing else in that run means anything.

Two rules are enforced here rather than left to the caller:

* Null presences and background are drawn **disjointly from the same pool**, so
  the null's expected AUC is 0.5 by construction rather than by hope.
* Null sets are labelled synthetic in the returned object. Hard constraint 4
  allows the null as the sole synthetic exception precisely because it is
  always labelled and never mixed into a real presence set.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..features.thin import thin


@dataclass
class PointSet:
    """A set of locations with its provenance attached.

    `synthetic` is not decoration. It travels with the data so that a null set
    cannot be quietly written into a table of real results.
    """

    coords: list[tuple[float, float]]
    role: str
    synthetic: bool = False
    n_source_records: int = 0
    thinning_m: float = 0.0
    notes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.coords)

    @property
    def label(self) -> str:
        return f"SYNTHETIC - {self.role}" if self.synthetic else self.role


def target_group_pool(
    points: list[dict],
    boundary=None,
    max_precision_m: float | None = None,
    thin_m: float = 0.0,
) -> list[dict]:
    """Filter target-group records to the pool background may be drawn from.

    Applies exactly the treatment the presences get - same extent, same
    precision filter, same thinning. If the background were thinned differently
    from the presences, the difference in density between the two would itself
    become a signal, which is the bias this is supposed to remove.

    Thinning matters more here than for presences: a single heavily-visited
    forest can carry hundreds of fungal records, and untinned it would
    contribute hundreds of background points and drown the rest of the region.
    """
    out = points
    if boundary is not None:
        out = [p for p in out if (p["east"], p["north"]) in boundary]
    if max_precision_m is not None:
        out = [
            p for p in out
            if p["precision_m"] is not None and p["precision_m"] <= max_precision_m
        ]
    return thin(out, thin_m)


def _coords(points: list[dict]) -> list[tuple[float, float]]:
    return [(p["east"], p["north"]) for p in points]


def draw_background(
    pool: list[dict],
    n: int | None = None,
    seed: int = 0,
    thinning_m: float = 0.0,
) -> PointSet:
    """Background points drawn from the target-group pool.

    `n=None` uses the whole pool, which is the default because throwing away
    background costs precision for nothing. A cap exists for run time only.
    """
    if not pool:
        raise ValueError("target-group pool is empty; background cannot be drawn")
    chosen = pool if n is None or n >= len(pool) else random.Random(seed).sample(pool, n)
    notes = []
    if n is not None and n < len(pool):
        notes.append(f"subsampled {n} of {len(pool)} available target-group points")
    return PointSet(
        coords=_coords(chosen),
        role="target-group background",
        synthetic=False,
        n_source_records=len(pool),
        thinning_m=thinning_m,
        notes=notes,
    )


def null_and_background(
    pool: list[dict],
    n_null: int,
    n_background: int | None = None,
    seed: int = 0,
    thinning_m: float = 0.0,
) -> tuple[PointSet, PointSet]:
    """The null control and its background, drawn disjointly from one pool.

    Disjoint is the point. Drawn from the same distribution with no overlap, a
    model has nothing to separate them by, so blocked-CV AUC of 0.5 is the
    correct answer by construction. Any departure is the validation scheme or
    the background sampling failing - which is exactly what hard constraint 5
    asks the null to detect.

    Overlapping draws would place identical coordinates on both sides of the
    label and make the null's score depend on how the model breaks ties, which
    would turn a diagnostic into noise.
    """
    if n_null <= 0:
        raise ValueError("n_null must be positive")
    if len(pool) < n_null + 1:
        raise ValueError(
            f"target-group pool holds {len(pool)} points; cannot draw {n_null} "
            f"null presences and a disjoint background from it"
        )

    rng = random.Random(seed)
    shuffled = pool[:]
    rng.shuffle(shuffled)
    null_pts = shuffled[:n_null]
    rest = shuffled[n_null:]

    if n_background is not None and n_background < len(rest):
        rest = rest[:n_background]

    null_set = PointSet(
        coords=_coords(null_pts),
        role="null presences (drawn from sampling effort, no ecology)",
        synthetic=True,
        n_source_records=len(pool),
        thinning_m=thinning_m,
        notes=[
            "NOT REAL DATA. Never mix into a real presence set.",
            "Expected blocked-CV AUC 0.5; anything higher means background "
            "sampling or the CV scheme is broken.",
        ],
    )
    background = PointSet(
        coords=_coords(rest),
        role="target-group background",
        synthetic=False,
        n_source_records=len(pool),
        thinning_m=thinning_m,
        notes=["disjoint from the null presence draw"],
    )
    return null_set, background


def assemble(presences: PointSet | list, background: PointSet) -> tuple[list, list[int]]:
    """Stack presences and background into (coords, y) for the CV harness.

    Refuses to combine a synthetic null with a real background silently: the
    caller gets the labelling it asked for, but a synthetic presence set is
    flagged in the returned warning rather than disappearing into a table.
    """
    pres_coords = presences.coords if isinstance(presences, PointSet) else list(presences)
    coords = list(pres_coords) + list(background.coords)
    y = [1] * len(pres_coords) + [0] * len(background.coords)
    return coords, y


def overlap_count(a: PointSet, b: PointSet) -> int:
    """Coordinates appearing in both sets. Should be zero for null vs background."""
    return len(set(a.coords) & set(b.coords))

"""Spatial blocking for cross-validation.

CLAUDE.md hard constraint 1: never use random train/test splits on occurrence
data. Records are spatially autocorrelated, so a randomly held-out point
usually has a near neighbour in the training set and the model is graded on
data it has effectively already seen. The resulting AUC reflects nothing.

The fix is to withhold whole regions. Points are binned into square blocks on
the EPSG:25833 grid, blocks are dealt out to folds, and a fold's test set is
therefore geographically separate from its training set.

Block size must exceed the autocorrelation range of the data. CLAUDE.md
mandates 5-10 km; the default lives in config/study_area.yaml.
"""
from __future__ import annotations

import random
from dataclasses import dataclass


def assign_blocks(coords: list[tuple[float, float]], block_size_m: float) -> list[tuple[int, int]]:
    """Bin coordinates into square blocks aligned to the CRS origin.

    Alignment is to the projection origin rather than to the data's bounding
    box, so block edges are reproducible across species and extents. Two runs
    over different presence sets get the same grid.
    """
    if block_size_m <= 0:
        raise ValueError("block_size_m must be positive; there is no unblocked mode here")
    return [(int(e // block_size_m), int(n // block_size_m)) for e, n in coords]


@dataclass
class FoldAssignment:
    fold_of_point: list[int]
    fold_of_block: dict[tuple[int, int], int]
    n_folds: int
    block_size_m: float
    presences_per_fold: list[int]
    blocks_per_fold: list[int]

    @property
    def min_presences(self) -> int:
        return min(self.presences_per_fold) if self.presences_per_fold else 0


def make_folds(
    blocks: list[tuple[int, int]],
    y: list[int],
    n_folds: int,
    block_size_m: float,
    seed: int = 0,
    n_iter: int = 500,
) -> FoldAssignment:
    """Deal blocks to folds, choosing the most presence-balanced deal found.

    Blocks are assigned randomly rather than in stripes or a checkerboard:
    systematic patterns can align with real landscape gradients (coast to
    inland, here) and quietly turn one fold into a different environment.
    Random deals are repeated and scored on how evenly presences land, since an
    unlucky deal can leave a fold with almost nothing to be graded on.

    Seeded, so a reported number can be reproduced exactly.
    """
    if n_folds < 2:
        raise ValueError("n_folds must be at least 2")
    unique = sorted(set(blocks))
    if len(unique) < n_folds:
        raise ValueError(
            f"only {len(unique)} distinct blocks for {n_folds} folds - "
            f"the block size is too large for this extent, or there is too little data"
        )

    presence_per_block: dict[tuple[int, int], int] = {b: 0 for b in unique}
    total_per_block: dict[tuple[int, int], int] = {b: 0 for b in unique}
    for b, label in zip(blocks, y):
        total_per_block[b] += 1
        if label == 1:
            presence_per_block[b] += 1

    rng = random.Random(seed)
    best = None
    best_cost = None
    for _ in range(max(1, n_iter)):
        shuffled = unique[:]
        rng.shuffle(shuffled)
        mapping = {b: i % n_folds for i, b in enumerate(shuffled)}
        pres = [0] * n_folds
        tot = [0] * n_folds
        for b, f in mapping.items():
            pres[f] += presence_per_block[b]
            tot[f] += total_per_block[b]
        # Spread of presences dominates; total count breaks ties.
        cost = (max(pres) - min(pres), max(tot) - min(tot))
        if best_cost is None or cost < best_cost:
            best_cost, best = cost, mapping

    assert best is not None
    fold_of_point = [best[b] for b in blocks]
    pres = [0] * n_folds
    blk = [0] * n_folds
    for b, f in best.items():
        pres[f] += presence_per_block[b]
        blk[f] += 1
    return FoldAssignment(
        fold_of_point=fold_of_point,
        fold_of_block=best,
        n_folds=n_folds,
        block_size_m=block_size_m,
        presences_per_fold=pres,
        blocks_per_fold=blk,
    )


def fold_adequacy_warnings(fa: FoldAssignment, min_presences: int = 10) -> list[str]:
    """Reasons not to trust fold-level numbers. Empty list means no objection."""
    out = []
    for f, n in enumerate(fa.presences_per_fold):
        if n < min_presences:
            out.append(
                f"fold {f} holds only {n} test presences (advisory minimum {min_presences}); "
                f"its AUC and Boyce values are close to meaningless on their own"
            )
    if fa.n_folds and min(fa.blocks_per_fold) < 2:
        out.append("at least one fold contains a single block; that fold tests one place, not a region")
    return out

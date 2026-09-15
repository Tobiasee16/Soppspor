# FINDINGS

Running log for Phase 0. Newest section at the bottom. Predictions are recorded
**before** the run they refer to, so a result cannot be rationalised afterwards.

The hypothesis under test:

> A habitat suitability score derived from public Norwegian geodata predicts
> real fungal occurrence meaningfully better than chance.

---

## Pre-registered predictions — recorded 2026-09-09, before any model exists

At the time of writing, no feature stack has been assembled, no model has been
fitted, and no validation harness has been run. Nothing below is a result. This
section exists so the results, when they arrive, can be scored against something
we committed to in advance.

### Predicted ordering

Ranked by expected blocked-CV performance, best first:

| Rank | Species | Reasoning |
|---|---|---|
| 1 | *Craterellus tubaeformis* (traktkantarell) | Tight habitat: moist mossy spruce forest, damp hollows, often near decaying wood. TWI and SR16 tree species should both bite hard |
| 2 | *Cantharellus cibarius* (kantarell) | Generalist across spruce, pine, birch, oak. Real habitat signal but broad |
| 3 | *Amanita muscaria* (rød fluesopp) | Conspicuous and photogenic, over-recorded relative to actual distribution, not strongly habitat-restricted |
| — | Synthetic null | Fake presences drawn from the sampling-effort surface. No ecology at all |

### Predicted null control

Blocked-CV AUC for the synthetic null: **≈ 0.5**.

### Interpretation rules, agreed in advance

These are binding. They are written here so that the temptation to reinterpret
a disappointing result has to be resisted in public.

- **Ordering holds and null sits near 0.5** → real signal. Proceed to Phase 2
  discussion.
- **All three score similarly** → we are modelling recording effort, not
  ecology. The bias correction failed. Fix that before anything else; do not
  interpret the individual numbers.
- **Null scores meaningfully above 0.5** → background sampling is broken.
  Nothing else in that run means anything. Stop and fix.
- **Blocked AUC near 0.5 across the board** → this is a *successful* Phase 0
  outcome. We learned the approach does not work, cheaply. Write it up plainly.
  Do not tune until the numbers look good. Do not drop a species from the table
  because it underperformed.

### What would falsify the hypothesis

The three-species design exists to separate "the model learned fungal ecology"
from "the model learned where people walk." A single species could not tell
those apart. If the effort-bias correction fails, we expect all three to score
similarly and well — a result that would look like success and be worthless.
That is the failure mode we are most exposed to, and the ordering test is the
instrument for catching it.

---

## 1. Record counts — 2026-09-09

**Question:** are there enough records per species, at both extents, to support
a presence-only model at all? Asked before any modelling, per CLAUDE.md.

**Method:** full national pull per taxon from the Artskart
`observations/list` endpoint, deduplicated on record ID, clipped client-side to
Kartverket boundary polygons for Agder fylke and Kristiansand kommune, then
thinned to one record per grid cell at several distances. No model involved.
Reproduce with `python scripts/report_counts.py`. Full table in
[data/processed/record_counts.md](data/processed/record_counts.md).

**Result: all three species clear the advisory minimum comfortably at the
training extent.** Thinned counts, Agder, precision-filtered to ≤ 250 m stated
coordinate uncertainty:

| Species | 250 m | 500 m | 1000 m |
|---|---|---|---|
| *Craterellus tubaeformis* | 302 | 282 | 248 |
| *Cantharellus cibarius* | 501 | 455 | 396 |
| *Amanita muscaria* | 309 | 284 | 260 |

At the prediction extent (Kristiansand), same filter:

| Species | 250 m | 500 m | 1000 m |
|---|---|---|---|
| *Craterellus tubaeformis* | 67 | 63 | 53 |
| *Cantharellus cibarius* | 59 | 54 | 45 |
| *Amanita muscaria* | 68 | 59 | 50 |

**Reading:**

- Agder as the training extent is comfortably supported — 250–500 thinned
  records per species. The concern raised in CLAUDE.md, that Kristiansand alone
  would be too thin, is confirmed and the choice to train on Agder is justified
  by the numbers rather than by assumption.
- Kristiansand as a held-out validation extent sits at 45–68 thinned records per
  species. Workable for evaluation, but **thin enough that per-species AUC
  confidence intervals will be wide**. Report intervals, not point estimates
  alone. Do not read a 0.03 difference between species at this extent as real.
- Spatial block CV at 7.5 km blocks over Agder needs checking against these
  counts: verify that every fold holds enough presences to be meaningful before
  trusting fold-level numbers.

**Caveats carried forward:**

- Counts are of *records*, not independent observations. Effort bias is
  untouched at this stage — that is what target-group background sampling is
  for, and it has not been done yet.
- The precision filter (≤ 250 m) excludes records with no stated precision
  rather than assuming they are good. It removes roughly 10–20% of records
  depending on species.
- Red-listed species are published at restricted precision. None of these three
  are red-listed, so this does not bite here, but it will for any later species.

**No conclusion about the hypothesis follows from this.** Sufficient data is a
precondition, not evidence.

---

## 2. Validation harness — 2026-09-09

**Question:** does the validation scheme actually detect a model that has
learned where people walk rather than fungal ecology? Asked and answered before
any model exists, per the working agreement.

**Method:** the harness accepts any `fit_predict` callable, so it can be run
against synthetic models whose correct answer is known by construction. Two
scenarios, both with 400 presences and 4000 background points over a 100 km
square, evaluated with an over-flexible k-nearest-neighbour model:

- **A — no ecology.** Presences clustered around 40 arbitrary centres, driven
  by nothing measurable. The model is given coordinates as its only features,
  so all it can do is memorise locations. True AUC is 0.5 by construction.
- **B — real signal.** Presences driven by a genuine environmental variable
  with an ~8 km wavelength, and the model is given that variable.

Reproduce with `python scripts/harness_selftest.py`.

### Result: spatial blocking alone is not sufficient

On data containing **no ecology at all**, 7.5 km blocking still returned an AUC
well above chance. The leak is geometric: blocks are dealt to folds at random,
so a test block usually sits directly against training blocks, and a presence
cluster straddling that edge lands partly in each set. Points metres apart end
up on opposite sides of the split.

Adding an exclusion buffer — dropping training points within a fixed distance of
any test point — removes it:

| Buffer | Blocked AUC | Leakage above chance |
|---|---|---|
| 0 m | 0.660 | +0.160 |
| 1000 m | 0.611 | +0.111 |
| 2000 m | 0.553 | +0.053 |
| 4000 m | 0.513 | +0.013 |
| 6000 m | 0.500 | +0.000 |

Random CV on the same no-ecology data returned **AUC 0.913**. That is the number
we would have reported, on data with no signal whatsoever, had we used a random
split. It is a concrete measure of what hard constraint 1 is protecting us from.

**Had we not run this, an unbuffered blocked AUC of 0.66 on a real species would
have looked like a modest but genuine result.** It would have been leakage.

### Result: buffering does not destroy genuine signal

The opposite failure — a scheme so severe that real signal cannot survive it —
was checked in scenario B. A perfect-knowledge model can only reach AUC 0.711
there, because presence is probabilistic; judged against that ceiling rather
than an absolute threshold, blocked CV with a 4 km buffer recovered **77% of the
achievable signal**, and random CV beat it by only +0.002.

That contrast is the useful part. Where the signal is genuinely environmental,
blocking costs almost nothing (+0.002). Where the model has memorised location,
blocking costs it everything (+0.25 inflation removed). The gap between the two
schemes is therefore diagnostic in its own right, and both are reported together.

### Result: 7.5 km blocking is viable at the Agder extent, not within Kristiansand

Answering the open question from section 1. Thinned, precision-filtered
presences per fold, 5 folds — full table in
[data/processed/cv_block_adequacy.md](data/processed/cv_block_adequacy.md):

| Extent | Block size | Presences per fold | Verdict |
|---|---|---|---|
| Agder | 5000 m | 48–83 | OK |
| Agder | 7500 m | 46–83 | OK |
| Agder | 10000 m | 48–84 | OK |
| Kristiansand | 5000–10000 m | 6–17 | too thin to grade per fold |

Agder supports blocked CV comfortably at any block size in the mandated range;
the configured 7.5 km is safe and the result is not sensitive to that choice.

Kristiansand cannot be cross-validated internally — 10 distinct blocks at 10 km
is barely two per fold. **This does not block anything**, because Kristiansand
was never meant to be cross-validated: it is the held-out transferability
extent. Blocked CV runs on Agder; Kristiansand gets a single evaluation against
its pooled ~50 presences. That distinction is now explicit in
`config/study_area.yaml` as `cv_extent`, so the two cannot silently merge.

### Carried forward

- **The 4 km buffer is provisional.** It was calibrated against a synthetic
  cluster scale of 1.2 km sd, which is not evidence about real fungal records.
  Before any result is quoted, the buffer must be re-derived from the empirical
  autocorrelation range of the actual occurrence data — a variogram or Ripley's
  K on the thinned presences would do it.
- The buffer costs training data: at 4 km it dropped roughly 6400 rows across
  five folds in the synthetic run. On the real data this will bite harder for
  the sparser species, and the trade-off between leakage and sample size should
  be reported, not silently resolved.
- Boyce index variance across folds is high (±0.35 in the synthetic runs). Treat
  fold-level Boyce values as indicative only; report the pooled figure.
- None of this validates any environmental data, because none has been fetched.

**No conclusion about the hypothesis follows from this.** A trustworthy
yardstick is a precondition, not evidence.

---

## 3. Spatial dependence in the records - 2026-09-09

**Question:** what is the empirical autocorrelation range of the occurrence
data, so the exclusion buffer can be set from evidence rather than from a
synthetic analogue? Carried forward from section 2.

**Method:** pair correlation function g(r) of the thinned, precision-filtered
presences at the Agder extent, in 1 km bins out to 30 km, against 39 simulated
null patterns inside the real boundary. Two nulls: uniform, and an
inhomogeneous null following the records' own regional density smoothed with a
Gaussian kernel at 20, 30 and 40 km. Reproduce with
`python scripts/derive_buffer.py`. Full tables in
[data/processed/autocorrelation_range.md](data/processed/autocorrelation_range.md).

### Result: the question has no answer, and that is the finding

**There is no autocorrelation range in these records.** g declines smoothly
across every distance we can measure and never reaches a floor:

| Species | g at 1 km | 4 km | 10 km | 20 km | levelled off by 30 km? |
|---|---|---|---|---|---|
| *Craterellus tubaeformis* | 6.5 | 3.7 | 2.2 | 1.3 | no |
| *Cantharellus cibarius* | 5.0 | 3.5 | 2.3 | 1.4 | no |
| *Amanita muscaria* | 4.7 | 3.6 | 2.2 | 1.2 | no |

(30 km kernel null; the pattern is the same at 20 and 40 km, so this is a
property of the records rather than of the bandwidth chosen.)

Against a *uniform* null the far-field g is 1.6-1.9 - at 25-30 km apart, two
records are still nearly twice as likely to co-occur as chance. That much is
the regional concentration of recording effort, and it is what target-group
background sampling exists to correct. But the inhomogeneous null removes
exactly that, and the curve *still* does not flatten. The clumping is
continuous across scales.

The estimator returns "no range" rather than the level it happened to reach at
30 km, which would have been an artefact of where we stopped measuring. Its
competence is verified separately: pointed at the self-test scenario whose
clustering scale is known by construction, it returns 4000 m, and a 4000 m
buffer reduces leakage there to +0.013. It finds a range when one exists.

### Consequence: the buffer is a declared trade-off, not a derived quantity

Since no distance eliminates the dependence, the buffer is bounded by the data
budget instead. Excess dependence remaining against training presences retained:

| Buffer | *C. tubaeformis* | *C. cibarius* | *A. muscaria* |
|---|---|---|---|
| 1000 m | 71% / 99% | 81% / 99% | 60% / 99% |
| 2000 m | 50% / 98% | 63% / 96% | 45% / 98% |
| 4000 m | 34% / 89% | 48% / 86% | 41% / 88% |
| 6000 m | 29% / 78% | 39% / 72% | 27% / 75% |
| 7500 m | 19% / 69% | 33% / 63% | 26% / 65% |

`buffer_m` stays at **4000 m**, now for a stated reason: it is the largest
buffer leaving 85% of training presences in the sparsest species. The
provisional value happens to survive, but its justification has changed
completely, and the honest reading is worse than before rather than better.

**At 4 km, between a third and a half of the short-range spatial dependence
survives into every blocked result we will report.**

### What this obliges us to do

1. **The null control is the load-bearing check, not the buffer.** Hard
   constraint 5 already mandates it. With residual leakage this size, a null
   scoring above 0.5 is the *expected* failure mode and is the first number to
   read in any run.
2. **Report the blocked-versus-random gap for every species.** The absolute
   blocked figure carries known contamination. The gap is the more honest
   quantity, and the harness returns both by construction.
3. **A blocked AUC modestly above 0.5 is not evidence of ecology.** The
   predicted ordering across three species, plus the null at 0.5, remains the
   falsification test. No single species' number can carry a conclusion.
4. **This should be replaced by direct measurement.** Once target-group
   background exists, the synthetic null species can be run at a sweep of
   buffers on real geography, and the buffer set where the null returns to 0.5.
   That measures the leakage instead of bounding it, and it supersedes the
   trade-off argued here.

### Carried forward

- The residual-dependence figures are *floors*, not estimates. Because the
  curve is still falling at 30 km, the far-field level used as the reference is
  an upper bound on the true one.
- 30 km is the limit of what Agder supports; beyond roughly a quarter of a
  region's width, g measures the shape of the region rather than the pattern.
  Whether the curve flattens beyond 30 km is not answerable from this extent.
- Measured on presences only. Target-group background will have its own,
  probably broader, clustering, and this needs re-checking once it exists.

**No conclusion about the hypothesis follows from this.** It bounds how much
trust the eventual numbers can carry, which is not the same as evidence.

---

## 4. Target-group background, and the size of the effort bias - 2026-09-09

**Question:** does drawing background from the distribution of all fungal
records actually remove the accessibility signal, and by how much? Hard
constraint 3 mandates the method; this measures whether it works.

**Method:** all Artskart `Sopper` records for Agder, pulled server-side and
given exactly the treatment the presences get - same extent, same precision
filter, same 1 km thinning. Then a model handed **nothing but coordinates** is
run against uniform background and against target-group background. With no
environmental data at all, anything it scores is knowledge of where records
are. Reproduce with `python scripts/report_background.py`; full tables in
[data/processed/background.md](data/processed/background.md).

### The pool

| Stage | Records |
|---|---|
| Downloaded (`filter.taxongroups=9`, `filter.countys=42`) | 82,129 |
| Inside the Agder polygon | 82,129 |
| Stated precision <= 250 m | 69,391 |
| Thinned to 1000 m | **3,882** |

Thinning removes 94% of the records. That steepness is itself a finding: the
records are packed into a small number of places. Fungal records touch about
17% of Agder's square kilometres, so **roughly four fifths of the county has
never had a single fungus recorded on it.**

### Result: about 0.22 of AUC was accessibility

A coordinates-only model, blocked CV, 4 km buffer:

| Species | vs uniform background | vs target-group background | difference |
|---|---|---|---|
| *Craterellus tubaeformis* | 0.805 | 0.584 | +0.221 |
| *Cantharellus cibarius* | 0.822 | 0.592 | +0.230 |
| *Amanita muscaria* | 0.790 | 0.595 | +0.195 |

**The left column is an AUC near 0.8 produced with no ecological information
whatsoever.** It is what we would have reported, and it would have looked like
a working habitat model. It is entirely a description of where people walk.

Two things in that table deserve to be stated separately.

First, the three species score almost identically against uniform background -
0.79 to 0.82, a spread of 0.03. That is precisely the failure mode written into
the pre-registered interpretation rules: *"All three score similarly, we are
modelling recording effort."* We now know what that failure looks like in this
dataset, on a model built to exhibit it. The diagnostic works.

Second, target-group background does not drive the number to 0.5. It lands at
0.58-0.60, and that residue is **not necessarily leakage**. Two explanations are
confounded and cannot be separated with what we have:

- residual spatial dependence, which section 3 says survives the 4 km buffer;
- genuine geography, since a species can occupy a spatially structured subset
  of the places people visit, and that is real ecology expressed as location.

### A benchmark, registered before any model is fitted

The consequence is that **0.5 is the wrong bar for the environmental models.**
A model with real habitat information must beat what coordinates alone achieve
against the same background, or it has added nothing to "which part of Agder
this is".

Registered now, before the feature stack exists:

| Baseline | Blocked-CV AUC to beat |
|---|---|
| *Craterellus tubaeformis* | 0.584 |
| *Cantharellus cibarius* | 0.592 |
| *Amanita muscaria* | 0.595 |
| Synthetic null (noise floor) | 0.526 |

An environmental model scoring 0.60 is not a 0.10 improvement on chance. It is
a 0.01 improvement on knowing nothing but the coordinates.

### Null control on real geography

300 null presences and 2,000 background points, drawn disjointly from the same
pool (0 shared coordinates, which the code enforces rather than hopes for):

**Blocked-CV AUC for the synthetic null: 0.526 (+/- 0.071 across folds).**

This passes, but it is not exactly 0.5 and the excess should not be waved away.
It is what section 3 predicts: with a third to a half of the short-range spatial
dependence surviving the buffer, a small positive bias in the null is the
expected consequence. Treat **0.53 as the noise floor**, and the wide fold
spread means it is a band rather than a line.

### Carried forward

- The null was measured with coordinates as the only feature. It must be re-run
  against the real feature stack, because environmental variables correlate
  with accessibility in ways coordinates alone do not capture. A null that
  passes here can still fail there, and hard constraint 5 is about the run that
  produces the results.
- Target-group background corrects for *where people looked*. It does not
  correct for what they were willing to write down - a bias toward autumn,
  toward conspicuous fruiting bodies, toward species someone can name. The pool
  is dominated by bracket fungi, which are recordable year-round and stay put.
- The pool is 3,882 points against 250-400 presences per species. Adequate, but
  not so large that background can be treated as unlimited.
- Records with no stated precision are dropped rather than assumed good, which
  discards roughly 15% of the group.

**No conclusion about the hypothesis follows from this.** It establishes what
the models have to beat, which is the opposite of evidence that they will.

---

## 5. Terrain, and two defects that would not have announced themselves - 2026-09-09

Built `data/interim/terrain_agder_32m.tif`: six bands at 32 m over Agder -
elevation, slope, northness, eastness, curvature, TWI - from the Kartverket
1 m LiDAR DTM resampled server-side via WCS.

**Resolution is 32 m, not the 16 m named in CLAUDE.md's definition of done.**
At 16 m the Agder extent is 178 million cells and the priority-flood heap does
not fit comfortably in memory. TWI is a catchment-scale index and 32 m is a
normal resolution for it in the literature; more importantly the *same* raster
serves training and prediction, so there is no resolution mismatch between the
two extents to break the transferability test. The 16 m requirement applies to
the exported suitability maps of Kristiansand, which is a far smaller extent
and remains achievable.

### The county polygon is not the coastline

Clipping the DTM to the Kartverket fylke boundary is not enough. An
administrative boundary runs to the territorial baseline, so 6,548 km2 of open
sea sat inside the mask. Left in, the sea is one flat basin with an enormous
upslope area and no slope, and TWI reads as maximal across all of it: median
11.07 there against 6.19 on real terrain. **The suitability map would have
shown the open sea as the wettest habitat in the county**, and every number in
the run would still have computed without complaint.

Sea is now defined as *near sea level AND connected to the map edge*, so an
inland lake at sea level stays land. Two independent checks that the result is
right rather than merely different:

| check | computed | published / expected |
|---|---|---|
| Agder land area | 16,509 km2 | 16,434 km2 |
| largest upslope area | 3,409 km2 | Otra catchment approx. 3,700 km2, truncated at the county line |

Both land in the right place, and the second is truncated in the direction it
should be: a catchment clipped by an administrative boundary must come out
smaller than the real one, not larger.

### A one-cell NaN halo along the entire coastline

Horn's slope kernel sums all eight neighbours, so a single masked neighbour
produced a NaN slope, and therefore a NaN TWI, on every land cell touching
water. Against Agder's convoluted coast that is a continuous ring of deleted
land following the whole shore.

The count was not what mattered - 28 of 904 thinned presences. What mattered is
that the loss was **uneven across species**: 1.2% of *C. tubaeformis* against
6.9% of *A. muscaria*. The entire Phase 0 design rests on ranking those three
against each other, and a defect that preferentially deletes coastal records
tilts that ranking while every metric still computes cleanly.

Missing neighbours are now filled from the cell's own value, which treats the
boundary as locally flat in that direction. That understates slope on the
outermost ring - a small, bounded, stated error, taken in preference to
silently discarding real observations.

### Coverage after the fix

`scripts/check_raster_coverage.py`, kept as a standing check rather than a
one-off, and it fails the build when per-species loss rates diverge:

| set | points | lost | % | masked cell | partial |
|---|---|---|---|---|---|
| *Craterellus tubaeformis* | 248 | 2 | 0.8% | 2 | 0 |
| *Cantharellus cibarius* | 396 | 5 | 1.3% | 5 | 0 |
| *Amanita muscaria* | 260 | 6 | 2.3% | 6 | 0 |
| target-group background | 3,486 | 73 | 2.1% | 73 | 0 |
| synthetic null (NOT REAL DATA) | 396 | 7 | 1.8% | 7 | 0 |

41 losses before the fix, 13 after; spread across species 5.7 points before,
1.5 after. The `partial` column - some bands resolving and others not - is the
halo signature specifically, and it is now zero everywhere.

The 13 remaining are real and are being dropped knowingly. Every one sits at
0.00-0.45 m in the *raw, unmasked* DTM, so they are shoreline or small-island
records that a 32 m cell cannot represent as land. One coordinate
(106912, 6470176) accounts for three of them, being a single site recorded for
all three species. Snapping them to the nearest land cell would be inventing a
location the observer did not report, so they are dropped and counted here
instead.

### Carried forward

- 32 m is a resolution compromise made for memory, not for ecology. Say so in
  the writeup.
- The coastal ring carries a knowingly flattened slope. Any result that leans
  on coastal cells specifically should be treated with suspicion.
- The sea mask uses a 0.5 m elevation threshold. Small skerries and shoreline
  strips fall below it; 13 real observations were lost that way.

---

## 6. Climate window - registered before the feature exists - 2026-09-09

The climate feature is **autumn rainfall over the fruiting season**. Two
numbers had to be pinned down - which days, which years - and both are exactly
the kind of parameter that can be quietly adjusted afterwards until the model
improves. They are therefore derived once, from the records themselves, and
written down here before anything is fitted. Full derivation:
[data/processed/climate_window.md](data/processed/climate_window.md).

### Observed phenology, Agder records

| set | dated records | 10% | median | 90% |
|---|---|---|---|---|
| *Craterellus tubaeformis* | 380 | 26 Aug | **27 Sep** | 5 Nov |
| *Cantharellus cibarius* | 740 | 10 Jul | **28 Aug** | 11 Oct |
| *Amanita muscaria* | 433 | 23 Aug | **19 Sep** | 14 Oct |
| all fungi (recording effort) | 82,129 | 18 Apr | **8 Sep** | 23 Oct |

The three medians span 30 days and sit 11-19 days off the recording-effort
peak, in both directions. Had the record dates been purely a function of when
people walk in the woods, the three would have clustered on that peak. They do
not. This is a small piece of evidence that the *dates* carry biology; it says
nothing about whether the *locations* do, which is what blocked CV is for.

### The registered feature

```
autumn_rain = mean over years 1991..2020 of
              sum of seNorge_2018 rr over day 221..293 (9 Aug - 20 Oct)
              at 1 km, resampled to the 32 m terrain grid
```

Three choices, each with its reason:

- **One window shared by all three species**, though they demonstrably do not
  fruit together. CLAUDE.md requires the same feature stack across species
  because the ordering test compares them against each other; three different
  climate features would make those three numbers incomparable. The cost is
  that the window fits the middle species best and the tails worst.
- **Each species gets one vote, not each record.** The window is the mean of
  the three species' 10th and 90th day-of-year percentiles. Pooling all 1,553
  records instead gives 30 Jul - 17 Oct, a window set mainly by whichever
  species was recorded most - and CLAUDE.md names *A. muscaria* as
  over-recorded relative to its actual distribution. Letting record counts
  choose would import the very effort bias the target-group background exists
  to remove. The two answers differ by about ten days, which is the expected
  result and not a reason to have skipped the distinction.
- **A climatology, not a year's weather.** Records span 1958-2026. Matching
  each to its own year's rainfall would model "was it wet the week this person
  went for a walk" - a conditions layer, which CLAUDE.md deliberately places in
  Phase 2. The habitat question is whether a place is *reliably* wet in autumn.
  1991-2020 is the WMO standard normal, chosen because it is the standard and
  not because it suits us.

### The risk in this feature, stated before it is fitted

Autumn rainfall over Agder is dominated by a coast-inland and orographic
gradient. **Population is also coastal.** This feature can therefore act as a
proxy for accessibility - precisely the failure mode the target-group
background exists to prevent. Its permutation importance must be reported
separately, and a model that leans on autumn rainfall while the terrain
features contribute nothing should be read as a warning, not a result.

At climatological scale the exact window matters far less than it would for a
daily layer, since the spatial pattern of mean autumn rainfall is similar
across neighbouring windows. That is a testable claim, not an assumption: the
correlation between this window and shifted variants gets reported once the
raster exists.

### The raster, built 2026-09-09

`data/interim/autumn_rain_agder_1km.tif`, 216 x 193 cells at the native 1 km.
Kept at 1 km rather than resampled onto the 32 m terrain grid: storing a 1 km
field at 32 m makes a raster thirty times larger that implies a precision the
data does not have. Resampling happens at the point of use, where it is
visible.

| quantity | min | median | max |
|---|---|---|---|
| mean seasonal total (mm) | 195 | 342 | 783 |
| interannual sd (mm) | 59 | 116 | 318 |

**The interannual spread is kept as a second band, not discarded.** Median
coefficient of variation is 0.34: a typical Agder cell with a 342 mm mean
swings by around +/-116 mm between autumns. That is worth stating plainly,
because it bounds what this feature can ever claim. It says a place is
*reliably* wetter than another place; it says nothing about the autumn any
particular picker will walk into. 2019, checked as a single year, came in at a
median 493 mm - 1.3 standard deviations above the 30-year mean.

### The sensitivity claim was tested, and held

Section 6 above asserted that at climatological scale the exact window matters
far less than it would for a daily layer. Asserting it is not enough, so the
same download accumulated windows shifted two weeks either way:

| window | days | median (mm) | correlation with registered |
|---|---|---|---|
| registered | 221-293 | 342 | 1.0000 |
| earlier | 207-279 | 332 | 0.9977 |
| later | 235-307 | 374 | 0.9971 |

Lowest correlation 0.9971. The *totals* move by about 12%, but the *spatial
pattern* is all but identical, and a model reads the pattern. So the window
choice is not quietly doing the work - which is the thing worth knowing, since
a window that did change the pattern would have been a tuning knob dressed up
as a derivation.

This does not rescue the feature from the accessibility worry above. A stable
spatial pattern that happens to follow the coast-inland gradient is stable and
confounded at the same time. Permutation importance still has to be reported
separately.

### Two implementation checks worth recording

* The multi-window accumulator and the straightforward single-window path were
  run against each other on 2019 and on the leap year 2020: **maximum absolute
  difference 0.0 mm**, identical missing-data masks. The leap year matters
  because day-of-year is carried on a collapsed 365-day axis throughout this
  project, and the file axis is not collapsed. If those two conventions drifted
  apart, the rainfall window would slide a day away from the fruiting window it
  was derived from, in every fourth year, silently.
* Cells that are fill on every day are kept as missing rather than summed to
  zero. Zero autumn rainfall is a claim, and a false one.

---

## 7. First model run - INTERIM, forest structure not yet included - 2026-09-09

**This is not the Phase 0 result.** SR16 was still downloading when this ran,
so the feature stack is terrain and climate only: seven bands, no forest
structure, no substrate, no land cover. It is recorded here because it ran, and
because a run that is only written up when it looks better is not a result but
a selection. Full output: [data/processed/model_results.md](data/processed/model_results.md).

7.5 km blocks, 5 folds, 4 km exclusion buffer, target-group background.
Features: elevation, slope, northness, eastness, curvature, TWI, autumn rain.

| species | maxent-style AUC | lightgbm AUC | registered coords-only baseline |
|---|---|---|---|
| *Craterellus tubaeformis* | 0.657 +/- 0.040 | 0.630 +/- 0.043 | 0.584 |
| *Cantharellus cibarius* | 0.667 +/- 0.031 | 0.654 +/- 0.036 | 0.592 |
| *Amanita muscaria* | 0.654 +/- 0.057 | 0.659 +/- 0.068 | 0.595 |
| synthetic null | **0.501 +/- 0.017** | **0.483 +/- 0.042** | 0.526 |
| constant floor | 0.500 | 0.500 | - |

### The null is clean

0.501 and 0.483, against a ceiling of 0.60 registered before the run. The
background sampling is not visibly broken, which is the necessary condition for
reading anything else. It is not evidence that the species numbers are real.

The constant model returns exactly 0.500, which is the check on the metric
rather than on the model: a scoring pipeline that reported the floor as better
than chance would be faulty in itself.

### The ordering fails, on both models

Predicted: tubaeformis > cibarius > muscaria. Observed:

* maxent-style: cibarius (0.667) > tubaeformis (0.657) > muscaria (0.654)
* lightgbm: muscaria (0.659) > cibarius (0.654) > tubaeformis (0.630)

Not merely the wrong order - **the wrong order in two different wrong ways**,
with a spread of 0.013 across three species that were chosen to differ. The two
models disagree about the ranking entirely, which is what one expects when the
thing being ranked is noise: the fold-to-fold standard deviations (0.03-0.07)
are two to five times the gap between species.

CLAUDE.md's interpretation rule for this is "all three score similarly means we
are modelling recording effort, and the bias correction failed". That rule
cannot be applied literally here, because the null - the direct test of the
bias correction - came back clean at 0.501. The more parsimonious reading is
the boring one: **these seven features do not contain what distinguishes the
three species.** Terrain and rainfall describe where forest is in general, not
which forest. The layer predicted to separate *C. tubaeformis*, namely SR16
tree species and stand structure, is exactly the one not yet in the stack.

That is a prediction, and it is now on record before the SR16 run: if the
ordering is real, adding forest structure should raise *tubaeformis* more than
the other two. If all three stay flat at ~0.66 with SR16 included, the honest
conclusion is that the approach does not work, and CLAUDE.md is explicit that
this is a successful Phase 0 outcome to be written up plainly.

### TWI did essentially nothing

The most pointed result in the table. Permutation importance on a held-out
block, drop in AUC when the feature is shuffled:

| feature | tubaeformis | cibarius | muscaria | null |
|---|---|---|---|---|
| elevation | **+0.180** | **+0.135** | +0.042 | +0.011 |
| curvature | +0.036 | +0.015 | -0.009 | +0.005 |
| slope | +0.025 | -0.000 | +0.027 | +0.022 |
| autumn rain | +0.010 | +0.039 | +0.019 | -0.001 |
| eastness | +0.009 | +0.021 | -0.025 | -0.014 |
| northness | +0.006 | +0.001 | +0.002 | -0.002 |
| **twi** | **+0.005** | +0.010 | -0.000 | -0.002 |

CLAUDE.md named TWI specifically as the derivative expected to bite hardest for
*C. tubaeformis*. It contributes +0.005. Elevation carries the model almost
single-handedly, and does so most strongly for exactly the species with the
tightest stated habitat.

> **Superseded - see section 9e.** The importances in this section were computed
> on a single held-out fold. Recomputed across all five, elevation is strongest
> for *C. cibarius*, not *C. tubaeformis*, and the last sentence above is an
> artefact of which block happened to be held out. TWI's failure is unaffected
> and holds up across every later run.

Three readings, and no way yet to choose between them:

1. **32 m is too coarse for TWI to mean anything ecologically.** A damp mossy
   hollow is a feature of tens of metres, and TWI at 32 m over a whole county
   describes valley bottoms, not hollows. This is the resolution compromise
   from section 5 coming back with a bill attached.
2. **TWI without forest structure is not interpretable.** A wet hollow in
   spruce and a wet hollow in a bog are the same TWI value and completely
   different habitats. SR16 may make TWI useful rather than replacing it.
3. **The prediction was simply wrong.**

Elevation dominating is not by itself suspicious. Agder runs from sea level to
1,491 m, and the upper half of that range is above the forest line, so
elevation separates forest from not-forest before it does anything subtler.

### A caution about the null column in that table

The null's importances are near zero for every feature, and it would be easy to
read that as "these features are free of effort bias". It is not evidence of
that. The null's presences are drawn from the same target-group pool as the
background, so the two are environmentally identical by construction and *no*
feature can separate them. That column is a check that the pipeline does not
leak - which it passes - and nothing more.

### The Boyce index is unstable, and lightgbm's is bad

Worth recording before it gets explained away. LightGBM reaches AUC comparable
to the linear baseline while returning Boyce indices of -0.410, +0.024 and
-0.252 with standard deviations up to 0.593. AUC is a pairwise ranking measure
and tolerates a badly shaped score distribution; Boyce asks whether predicted
suitability rises monotonically with observed presence density, and it does
not. On this feature set the flexible model produces a ranking that is
acceptable in pairs and incoherent as a surface - which matters, because a
suitability *map* is a surface, not a set of pairs.

### Carried forward

- Re-run with SR16 and losmasser. The prediction above is registered.
- The 4 km buffer drops 4,600-5,100 training rows across the five folds. That
  is the declared cost from section 3 showing up as a real number.
- If TWI is still inert with forest structure included, say so in the writeup
  rather than dropping it from the table.

---

## 8. SR16 forest structure added - 2026-09-09

The run section 7 was registered against. The feature stack goes from seven
bands to twenty: the seven terrain and climate features, plus SR16 tree species
as a three-way one-hot with a shared missingness indicator, seven SR16 numeric
variables, and the lower and upper volume bounds carried as features in their
own right. Same blocks, same buffer, same background, same seeds.

**The full output of this particular run no longer exists.** `run_models.py`
wrote to a single fixed path and every run overwrote the last; per-run
archiving under `data/processed/runs/` was only added in section 9. The AUC
and importance figures quoted below were taken from that output while it was
on disk and are the record of it. The nearest surviving artefact is the
losmasser run, [runs/model_results_terrain-climate-sr16-losmasser.md](data/processed/runs/model_results_terrain-climate-sr16-losmasser.md),
which adds eleven substrate columns and moves every species by less than 0.02
(section 9a).

### The registered prediction held, on one of the two models

Section 7 put this on record before the run: *"if the ordering is real, adding
forest structure should raise tubaeformis more than the other two. If all three
stay flat at ~0.66 with SR16 included, the honest conclusion is that the
approach does not work."*

They did not stay flat, and the rise was not even:

| species | maxent before | maxent +SR16 | delta | lightgbm before | lightgbm +SR16 | delta |
|---|---|---|---|---|---|---|
| *Craterellus tubaeformis* | 0.657 | **0.704** | **+0.047** | 0.630 | 0.648 | +0.018 |
| *Cantharellus cibarius* | 0.667 | 0.680 | +0.013 | 0.654 | 0.670 | +0.016 |
| *Amanita muscaria* | 0.654 | 0.655 | +0.001 | 0.659 | 0.652 | -0.007 |
| synthetic null | 0.501 | **0.501** | +0.000 | 0.483 | 0.479 | -0.004 |

On the maxent-style model the gains are ordered exactly as predicted, and
ordered by stated habitat specificity: the species with the tightest habitat
gains most from the layer describing habitat, the generalist gains a little,
the over-recorded conspicuous species gains nothing. The null did not move,
which is what it should do - twenty features of forest structure cannot help
separate presences from background when the two are drawn from the same pool
by construction.

### The ordering test: PASS on maxent, FAIL on lightgbm

* maxent-style: tubaeformis (0.704) > cibarius (0.680) > muscaria (0.655) - **PASS**
* lightgbm: cibarius (0.670) > muscaria (0.652) > tubaeformis (0.648) - **FAIL**

This is the first time any model has reproduced the pre-registered ordering. It
should not be read as more than it is, for three reasons, all of which were
foreseeable and none of which are raised only because the result was good:

1. **The margin is inside the noise.** The tubaeformis-to-cibarius gap is
   0.024; the fold-to-fold standard deviations are 0.030 and 0.038. One fold
   landing differently would reorder the top two. More persuasive than the
   ordering itself are the *deltas* - +0.047 / +0.013 / +0.001 is a cleaner
   monotone signal than the AUCs, because each species is compared against
   itself on the same folds rather than against a different species.
2. **The two models still disagree.** LightGBM puts tubaeformis last while
   maxent puts it first, on identical data. Section 7 read that disagreement as
   the signature of ranking noise, and nothing here retracts that reading for
   lightgbm.
3. **Absolute performance is modest.** 0.704 is a weak-to-moderate AUC. It is
   comfortably above the 0.584 coordinates-only baseline registered in section
   4, and comfortably below anything that would justify a product.

### What carried the gain

Permutation importance, maxent-style, held-out block, *C. tubaeformis*:

| feature | drop in AUC |
|---|---|
| elevation | +0.152 |
| **vol_lower** | **+0.127** |
| **treslag_3** (deciduous) | **+0.078** |
| vol_upper | +0.066 |
| treslag_missing | +0.054 |
| treslag_2 (pine) | +0.031 |
| stand_age_yr | +0.026 |
| **vol_m3ha** (point estimate) | **+0.004** |
| **twi** | **-0.001** |

> **Superseded - see sections 9d and 9e.** This table is single-fold. The
> volume block turned out to contribute nothing at all once refitted without
> the bounds (9d) and recomputed across five folds (9e), where `vol_m3ha` is
> zeroed outright by L1. The reservations stated immediately below were the
> right ones; they simply turned out to be understatements.

Tree species and the volume *bounds* did the work. Two things about that are
worth stating plainly rather than leaving to be noticed.

**The volume point estimate contributes +0.004 while its lower bound
contributes +0.127.** These features are strongly correlated by construction -
SR16 derives them from the same model - and permutation importance under
correlated features assigns credit essentially arbitrarily, because shuffling
one leaves its near-copy intact for the model to lean on. L1 logistic
regression compounds this: it selects one of a correlated group and zeroes the
rest. So this is not evidence that uncertainty bounds are more informative than
estimates. It is evidence that *something in the volume block* matters for
tubaeformis and that this method cannot say which part. Carrying the bounds as
features was justified in the code as propagating SR16's uncertainty, per
CLAUDE.md; it has had the side effect of making the volume importances
individually unreadable. That needs a targeted check - refit with the bounds
dropped and see whether the 0.704 survives - before the writeup claims forest
volume matters at all.

**treslag_missing carries +0.054.** That column means "no SR16 estimate here",
which covers water, farmland, bog, bare rock and ground above the forest line.
A model leaning on it is partly learning "is this productive forest at all",
which is a real ecological fact and also the least interesting one available.

### TWI is still inert, as section 7 committed to reporting

Section 7's carried-forward: *"If TWI is still inert with forest structure
included, say so in the writeup rather than dropping it from the table."*

It is. TWI's permutation importance for *C. tubaeformis* is **-0.001** on maxent
and **+0.010** on lightgbm - unchanged from the +0.005 of the terrain-only run.
Reading 2 from section 7, that SR16 would make TWI interpretable rather than
replace it, is not supported. Reading 1 (32 m is too coarse to resolve a
hollow) and reading 3 (the prediction was simply wrong) remain open, and the
16 m prediction stack for Kristiansand is the only cheap test of reading 1 we
have coming.

CLAUDE.md named TWI as the derivative expected to bite hardest for this species.
It has now failed to do so twice, with and without forest structure. That goes
into the final writeup as a stated negative result.

### Elevation still dominates, and Boyce is still bad

Elevation remains the single largest importance for both models and all three
species. The section 7 reading stands: Agder spans sea level to 1,491 m and the
upper half is above the forest line, so elevation separates forest from
not-forest before it does anything subtler.

Boyce did not improve. Maxent returns -0.107, +0.262 and +0.191 with standard
deviations of 0.664, 0.376 and 0.474; lightgbm returns -0.589, -0.095 and
-0.161. A negative Boyce means predicted suitability does not rise
monotonically with observed presence density - the model ranks acceptably in
pairs and produces an incoherent surface. Phase 0 artefact 1 requires Boyce
alongside AUC, and on present evidence that column says the suitability *maps*
are not trustworthy even where the AUC is defensible. This needs investigation
of the metric implementation itself before the maps are drawn, because a metric
this unstable across folds may be measuring its own small-sample behaviour
rather than the model.

### Registered before the losmasser run

NGU superficial deposits is the last layer with a strong prior. Calcareous
versus acidic substrate structures fungal communities, but none of the three
target species is a calcicole, so:

**Prediction: losmasser moves all three species by less than +0.02 and does not
change the maxent ordering.** If it produces a large gain for *A. muscaria*
specifically, suspect the deposit classes are tracking settlement and
agriculture - marine deposits are where people live in Agder - and check that
against the null before believing it.

### Carried forward

- Refit without `vol_lower` / `vol_upper` to find out whether the volume block
  or its uncertainty bounds carried tubaeformis. The 0.704 is not interpretable
  until this is done.
- Investigate the Boyce implementation before drawing any map.
- The transferability test - fitting on Agder and evaluating once on
  Kristiansand - has still not been run. It is the actual question CLAUDE.md
  poses, and every number so far is Agder-internal.

---

## 9. Substrate, a retracted metric, and the transferability test - 2026-09-10

Three results in one sitting, and the last of them is the one that matters.
Full outputs:
[model_results.md](data/processed/model_results.md) (archived per run under
`data/processed/runs/`),
[boyce_diagnosis.md](data/processed/boyce_diagnosis.md),
[transferability.md](data/processed/transferability.md).

### 9a. Losmasser: the registered prediction held, and the layer does nothing

NGU superficial deposits, 34,714 polygons over Agder, 30 `jordart` codes
collapsed into ten depositional groups fixed in `src/ingest/losmasser.py`
before fitting. Stack goes 20 features to 31.

Section 8 registered: *"losmasser moves all three species by less than +0.02
and does not change the maxent ordering."*

| species | maxent +SR16 | +losmasser | delta | lightgbm +SR16 | +losmasser | delta |
|---|---|---|---|---|---|---|
| *Craterellus tubaeformis* | 0.704 | 0.700 | -0.004 | 0.648 | 0.649 | +0.001 |
| *Cantharellus cibarius* | 0.680 | 0.678 | -0.002 | 0.670 | 0.676 | +0.006 |
| *Amanita muscaria* | 0.655 | 0.645 | -0.010 | 0.652 | 0.655 | +0.003 |
| synthetic null | 0.501 | 0.501 | +0.000 | 0.479 | 0.478 | -0.001 |

Both halves held. Every move is under 0.02, the maxent ordering is unchanged
and still passes, lightgbm's is unchanged and still fails. No large gain for
*A. muscaria*, so the settlement-proxy worry registered alongside the
prediction did not materialise.

Every one of the eleven substrate columns has a permutation importance at or
below +0.012 on both models - indistinguishable from zero. The maxent moves are
slightly negative for all three species, which is what eleven near-empty
columns do to a regularised model: they cost a little and return nothing.

**Substrate, as NGU maps it, does not separate these three species.** That is a
clean negative result and it is not surprising in hindsight - none of the three
is a calcicole, and the ecological literature that motivated the layer is about
calcareous versus acidic bedrock chemistry, which a depositional-origin
classification is only a rough proxy for. `losmasse_missing` is exactly +0.000
everywhere: coverage over the point sets is complete, so this is a real null
rather than a coverage artefact.

One thing to note rather than dismiss: `losmasse_marine` has the largest
substrate importance in the whole table, +0.033, and it is on the **null**. The
null's presences and its background are environmentally identical by
construction, so nothing should separate them, and +0.033 from five permutation
repeats on a single held-out fold is within noise. But marine deposits are
where people live in Agder, and this was the exact column flagged to watch. It
is noise on this evidence; it is worth re-checking if the background sampling
is ever changed.

### 9b. The Boyce index was measuring itself. An earlier interpretation is retracted.

Section 8 carried forward that the Boyce implementation had to be investigated
before any map was drawn. It was, against synthetic score distributions with
known answers at real fold sizes, with no real species data involved.

**A model with no skill whatsoever returns a published-method Boyce of -0.417
+/- 0.332.** The correct answer is zero.

| case | expected | published Boyce | quantile-window Boyce |
|---|---|---|---|
| separable (AUC exactly 1) | +1 | NaN | NaN |
| strong (AUC 0.92) | high | +0.914 +/- 0.103 | +0.833 +/- 0.087 |
| moderate (AUC 0.70) | clearly positive | +0.321 +/- 0.361 | +0.808 +/- 0.088 |
| **no skill (AUC 0.50)** | **~0** | **-0.417 +/- 0.332** | **-0.006 +/- 0.298** |
| inverted (AUC 0.30) | clearly negative | -0.733 +/- 0.160 | -0.798 +/- 0.098 |

The cause is where the windows land. The published method slides an equal-width
window across the *score range*; a presence-background model trained at a ~7%
base rate puts nearly all its mass near zero with a thin right tail, so 39 of
101 windows hold fewer than ten background points and the ratio F = P/E in them
is decided by whether one or two points happened to fall in. Spearman then
correlates window position against a sequence that is largely noise.

**This retracts an interpretation.** Sections 7 and 8 both read lightgbm's
negative Boyce as the flexible model producing a ranking "acceptable in pairs
and incoherent as a surface". That reading is not supported. Those values were
largely the metric's own bias. The same bias applies to every Boyce number
reported in sections 7 and 8, and they should be read as uninterpretable in
sign rather than as evidence about the models.

Note what is *not* retracted: AUC is untouched, and the ordering results in
sections 7 and 8 stand exactly as recorded.

**With the bias removed, the Boyce column finally says something.** Re-run on
the full blocked CV, the quantile version gives:

| set | maxent | lightgbm |
|---|---|---|
| *Craterellus tubaeformis* | +0.825 +/- 0.114 | +0.731 +/- 0.063 |
| *Cantharellus cibarius* | +0.873 +/- 0.017 | +0.850 +/- 0.059 |
| *Amanita muscaria* | +0.764 +/- 0.133 | +0.734 +/- 0.183 |
| **synthetic null** | **+0.063 +/- 0.313** | **-0.182 +/- 0.287** |

All three species sit between +0.73 and +0.87; the null sits at zero. That is
the pattern a working metric should produce, and it is the opposite of what the
published column reported for the same fitted models on the same folds. The
same separation appears on the transfer split (+0.70 to +0.92 for species,
-0.18 and -0.21 for the null).

This is worth stating plainly because it changes a Phase 0 artefact: the
suitability *surfaces* are coherent - predicted suitability does rise
monotonically with observed presence density - which sections 7 and 8 wrongly
concluded they were not. The maps are weak, but they are not incoherent.

It does **not** rescue the ordering. Boyce ranks cibarius above tubaeformis on
both models and both schemes, which agrees with the transfer AUC in 9c and
disagrees with the blocked AUC in section 8.

Guard rails, because changing a metric after a disappointing result is
precisely the move the working agreement warns about:

- The comparison ran on synthetic data with known answers, never against a real
  species result.
- The pass criterion was written down before the comparison ran, and the
  quantile version **fails** one clause of it: it scores +0.833 on the strong
  case against a bar of +0.9. The bar has not been lowered. It fails, and it is
  reported as failing, because quantile windows compress the top of the scale.
- The published implementation is **not** retired. Both are computed and both
  are reported in every table from here on.
- Boyce is genuinely undefined under perfect separation - no window holds both
  classes - and both implementations correctly return NaN there.

### 9c. Transferability: the ordering does not survive the move

This is the test CLAUDE.md's study-area section actually asks for, and until
today it had never been run. Every number in sections 1-8 is Agder-internal:
spatially blocked, but trained and tested inside one region.

Fitted on Agder minus Kristiansand minus a 4 km ring around it. Evaluated once
on Kristiansand. One test set, one number per model, with a percentile
bootstrap over the held-out points rather than a fabricated fold spread.

| species | held-out presences | maxent AUC | 95% CI | lightgbm AUC | 95% CI |
|---|---|---|---|---|---|
| *Craterellus tubaeformis* | 53 | **0.640** | 0.555-0.716 | 0.605 | 0.529-0.679 |
| *Cantharellus cibarius* | 45 | **0.730** | 0.667-0.795 | 0.615 | 0.528-0.703 |
| *Amanita muscaria* | 47 | 0.648 | 0.582-0.712 | 0.624 | 0.554-0.691 |
| synthetic null | 48 | 0.486 | 0.400-0.579 | 0.480 | 0.389-0.567 |

The null is clean at 0.486 and 0.480, so the transfer split does not itself
manufacture signal.

**The registered ordering fails on both models.**

* maxent: cibarius (0.730) > muscaria (0.648) > tubaeformis (0.640) - **FAIL**
* lightgbm: muscaria (0.624) > cibarius (0.615) > tubaeformis (0.605) - **FAIL**

*C. tubaeformis* - the species that passed on blocked CV at 0.704, the species
the whole three-species design predicted would win, the species whose gain from
SR16 was the strongest evidence in section 8 - comes **last on both models**
when the model is asked to work somewhere it was not trained.

Three honest observations about that:

1. **The intervals overlap almost completely.** 0.555-0.716 against 0.667-0.795
   against 0.582-0.712. With 45-53 held-out presences per species this test
   cannot resolve an ordering, and it would be as wrong to claim it has
   established the reverse ordering as to claim section 8 established the
   registered one. What it can say is that the section 8 ordering is not
   reproduced.
2. **It is a genuine reversal, not a shrinkage.** Every species loses accuracy
   moving to held-out ground, which is expected. But tubaeformis loses most
   (0.704 to 0.640) and cibarius *gains* (0.678 to 0.730). A uniform drop would
   suggest the model transfers imperfectly; a reordering suggests the blocked-CV
   ranking was reading something local to the Agder-wide split.
3. **The section 8 caveat is now cashed in.** Section 8 said the ordering pass
   was "inside the noise" and that the deltas were more persuasive than the
   AUCs. That caution was correct, and the transfer test is what it was cautious
   about.

The quantile Boyce, on the other hand, behaves well here and is worth reading:
+0.696 / +0.924 / +0.734 for the three species against **-0.179 and -0.207 for
the null**. Species surfaces rise monotonically with observed presence density;
the null's does not. The published column would have inverted that reading
entirely - it gives tubaeformis -0.222 and the null +0.113.

### Where this leaves the hypothesis

The hypothesis under test is whether a habitat suitability score from public
geodata predicts real fungal occurrence meaningfully better than chance.

- **Better than chance: yes, modestly and repeatedly.** 0.60-0.73 on held-out
  ground, with a clean null in every run, against a 0.584 coordinates-only
  baseline. Not an artefact of the validation scheme.
- **The falsification test the three-species design was built around: failed.**
  The predicted ordering holds on one model under blocked CV and on neither
  model under transfer. CLAUDE.md's interpretation rule for "all three score
  similarly" points at effort bias - but the null is clean in every run, on
  both schemes, which is the direct test of that and it passes. The more
  parsimonious reading remains section 7's: these features distinguish forest
  from not-forest well and one forest from another poorly.
- **TWI never worked.** Three runs, with and without forest structure, at
  32 m. Importance between -0.001 and +0.010 throughout.

### 9d. The volume bounds carried nothing

Section 8 registered this check and would not let the 0.704 be interpreted
until it ran. It has now run: same stack, same folds, same seeds, with
`vol_lower` and `vol_upper` dropped - 29 features instead of 31.

| species | with bounds | without bounds | delta |
|---|---|---|---|
| *Craterellus tubaeformis* | 0.700 | 0.703 | +0.003 |
| *Cantharellus cibarius* | 0.678 | 0.679 | +0.001 |
| *Amanita muscaria* | 0.645 | 0.645 | +0.000 |
| synthetic null | 0.501 | 0.501 | +0.000 |

**The uncertainty bounds contribute nothing.** Removing them changes no
species by more than 0.003 - well inside a fold standard deviation of 0.033.
So the +0.127 permutation importance that `vol_lower` carried in section 8 was
the correlated-feature credit-assignment artefact flagged there, not a finding
about SR16 uncertainty. Section 8's caution was right, and its refusal to claim
anything about forest volume until this ran was the correct call.

Two further things worth recording rather than smoothing over:

- The credit did **not** migrate to `vol_m3ha` when the bounds were removed:
  it goes from +0.004 to +0.008, still negligible. Whatever tiny contribution
  the volume block makes, it is not concentrated in any one of its columns.
- `treslag_3` (deciduous) fell from +0.078 to +0.036 between two runs whose
  AUCs are identical to three decimal places. Permutation importance on a
  single held-out fold with five repeats is noisier than the AUC it is
  computed from, and the importance tables in sections 7-9 should be read as
  qualitative rankings, not as measurements. The stable signal across every
  run is elevation first and tree species second; the ordering below that
  moves around.

Carrying the bounds is still defensible - CLAUDE.md asks for SR16's
uncertainty to be propagated rather than hidden - but they must not be
described as contributing to accuracy, because they do not.

### 9e. Feature importance across all five folds, and a second retraction

Section 9d noted that permutation importance on one fold was too unstable to
rank features. It has now been recomputed across all five folds with the
fold-to-fold spread reported beside every mean. The picture changes, and one
claim from section 7 does not survive it.

Maxent-style, mean +/- spread over five folds:

| feature | *C. tubaeformis* | *C. cibarius* | *A. muscaria* | null |
|---|---|---|---|---|
| elevation | +0.090 ±0.026 | **+0.113 ±0.012** | +0.063 ±0.031 | +0.003 ±0.008 |
| basal_area_m2ha | +0.038 ±0.019 | +0.020 ±0.019 | -0.002 ±0.010 | -0.005 ±0.004 |
| treslag_3 (deciduous) | +0.037 ±0.017 | +0.032 ±0.010 | +0.019 ±0.013 | +0.011 ±0.015 |
| treslag_missing | +0.034 ±0.026 | +0.024 ±0.010 | +0.001 ±0.003 | -0.000 ±0.005 |
| stand_age_yr | +0.031 ±0.017 | +0.014 ±0.016 | **+0.050 ±0.023** | -0.001 ±0.005 |
| crown_cover_pct | +0.023 ±0.029 | +0.015 ±0.016 | +0.029 ±0.025 | +0.005 ±0.011 |
| site_index | +0.020 ±0.013 | +0.011 ±0.018 | +0.006 ±0.008 | +0.004 ±0.007 |
| vol_lower | +0.019 ±0.041 | -0.004 ±0.015 | +0.001 ±0.003 | -0.003 ±0.007 |
| **twi** | **+0.006 ±0.008** | -0.003 ±0.004 | -0.001 ±0.002 | +0.004 ±0.010 |
| vol_m3ha | +0.001 ±0.009 | +0.000 ±0.000 | +0.000 ±0.000 | -0.000 ±0.000 |

**Retraction: elevation is not strongest for *C. tubaeformis*.** Section 7 made
a point of this - "elevation carries the model almost single-handedly, and does
so most strongly for exactly the species with the tightest stated habitat" -
and read it as suggestive. Across five folds elevation is strongest for
*C. cibarius* (+0.113 ±0.012) and clearly weaker for *C. tubaeformis*
(+0.090 ±0.026). The single-fold table had it the other way round. The
observation was an artefact of which block happened to be held out, and the
inference drawn from it should be struck.

What survives the move to five folds:

- **Elevation is the largest single feature for every species**, by a wide
  margin, and near zero for the null. That was never in doubt and is not in
  doubt now.
- **`twi` is dead.** +0.006 ±0.008 for the species it was predicted to matter
  most for - a mean smaller than its own spread. Three runs, five folds,
  two models, with and without forest structure. This is as settled as anything
  in the project.
- **`vol_m3ha` is exactly zero to three decimals** for every species, with zero
  spread, which is L1 regularisation having zeroed the coefficient outright.
  Combined with 9d, the volume block contributes nothing at all - not the
  estimate, not the bounds.
- **`stand_age_yr` is the strongest forest variable for *A. muscaria***
  (+0.050 ±0.023), which is the one genuinely new thing this table shows and is
  at least ecologically plausible for a species associated with mature birch
  and pine.
- Below the top three or four features, **most means are smaller than their
  spreads.** Those features have no established rank and none should be given
  one in prose.

The general lesson, worth carrying past this project: a diagnostic computed on
one fold was quietly load-bearing in two write-ups before anyone checked its
variance. It was cheap to fix and it changed a published claim.

### Carried forward

- The 16 m maps are built from the transfer-fitted models, so they inherit
  0.60-0.73, not 0.70-0.73.
- Naturbase has not been fetched, so the suppression pipeline is wired in and
  screening nothing. The maps must not leave the project until that is fixed.
- Whether AR5 land cover would separate the species is untested and now the
  main remaining "we did not try X" - it needs a Geonorge login with the FKB
  role.
- Permutation importance on one fold with five repeats is too noisy to rank
  features below the top two. If feature attribution is going to carry weight
  in the final writeup, it needs to be computed across all five folds.

### Phase 0 definition of done - status

| artefact | state |
|---|---|
| 1. Blocked AUC + Boyce for three species and the null, one table | **done** - [model_results.md](data/processed/model_results.md), now with both Boyce implementations |
| 2. Explicit pass/fail on the predicted ordering | **done** - PASS on maxent under blocked CV, FAIL on lightgbm, FAIL on both under transfer. Section 9c |
| 3. Static suitability maps of Kristiansand at 16 m, GeoTIFF + PNG | **done** - [suitability_maps.md](data/processed/suitability_maps.md), four maps including the null. Not screened for sensitive habitat, so not shareable |
| 4. Honest writeup in FINDINGS | sections 7-9 cover what the models learned, what carried them, where they fail and whether the ordering held. The "does this justify continuing" judgement is the one thing still owed, and it is a decision for the two of us rather than something to be written unilaterally |

---

## 10. AR50 land cover - registered before the layer is fitted - 2026-09-10

### The AR5 blocker is a licence, and that is now settled

Sections 8-9 carried "AR5 is untested, it needs a Geonorge FKB login" as the
largest untried option. Geonorge's own catalogue answers it definitively:
FKB-AR5 is `AccessIsOpendata: false`, `AccessIsRestricted: true`,
`DataAccess: "Norge digitalt begrenset"`, under Geovekst terms.

**That is a licensing restriction, not a missing technical route.** There is no
anonymous route to find, and finding one would mean using data we are not
licensed for. The open question is closed as *will not do*, not as *not yet
done*. AR5 point queries against NIBIO's public WMS do work and were verified
in section 5, but that is a public map service, not a licence to pull the layer
into a model and redistribute what comes out.

**AR50** is ordered instead: same agency, generalised to 1:50,000, open data
under NLOD, same NIBIO download API that delivered SR16.

### What it costs and what it buys

A factor of ten in scale. AR50's minimum mapping unit is far larger than AR5's,
so small clearings, narrow bogs and field edges are simply not in it. Against a
32 m grid it is coarse.

What it buys is the one thing nothing else in the stack supplies. SR16 maps
productive forest only, so every non-forest cell has so far reached the model as
a single undifferentiated `treslag_missing` flag - which carries a real
five-fold importance of +0.034 ±0.026 and could mean bog, farmland, bare rock,
open water or a housing estate. AR50 splits that bucket into eight classes:
forest 42.1% of covered cells, sea 28.5%, open ground 18.1%, freshwater 5.9%,
mire 2.7%, agriculture 1.7%, built 1.0%.

Full breakdown: [data/processed/ar50.md](data/processed/ar50.md). One
implementation detail worth recording: AR50 codes agriculture as `20`, where
AR5 uses `21`/`22`/`23` and puts transport at `12`. Reading 20 the AR5 way
would have relabelled 5,363 agricultural polygons as roads with nothing
failing. It was checked against the delivery instead.

### Registered predictions, before the run

1. **All three species move by less than +0.02**, as losmasser did. SR16 and
   elevation already separate forest from not-forest; AR50 mostly renames a
   distinction the model can already make.
2. **`treslag_missing` importance falls** if AR50 is doing its job, because the
   information in that flag moves into the named classes.
3. **`ar50_mire` carries a negative weight for *C. tubaeformis*.** It is a
   moist spruce-forest species, not a mire species, and the model has had no
   way to tell a wet hollow in spruce from open bog - reading 1 of the three
   TWI explanations in section 7. If TWI becomes useful once bog is separable,
   that reading gains support.
4. **Watch `ar50_built` and `ar50_agriculture`.** They are literally where
   people are, and recording effort follows people. A species gaining from
   either is a species whose gain has to be checked against the null before it
   is believed. *A. muscaria* is the one to watch.

Prediction 3 is the only one that would be genuinely interesting if it held,
and it is the one I would bet against.

### Results, scored against those four predictions

Stack goes 31 features to 42. Same blocks, same buffer, same background, same
seeds. Full output:
[runs/model_results_terrain-climate-sr16-losmasser-ar50.md](data/processed/runs/model_results_terrain-climate-sr16-losmasser-ar50.md).

| species | maxent before | maxent +AR50 | delta | lightgbm before | lightgbm +AR50 | delta |
|---|---|---|---|---|---|---|
| *Craterellus tubaeformis* | 0.700 | 0.705 | +0.005 | 0.649 | 0.645 | -0.004 |
| *Cantharellus cibarius* | 0.678 | 0.690 | +0.012 | 0.676 | 0.685 | +0.009 |
| *Amanita muscaria* | 0.645 | 0.658 | +0.013 | 0.655 | 0.664 | +0.009 |
| synthetic null | 0.501 | 0.492 | -0.009 | 0.478 | 0.471 | -0.007 |

**Prediction 1 — HELD.** Every species moves by less than 0.02. The null stays
clean at 0.492. The maxent ordering is unchanged and still passes; lightgbm's
is unchanged and still fails.

**Prediction 2 — HELD.** `treslag_missing` importance falls everywhere it had
any: *C. tubaeformis* from +0.034 ±0.026 to +0.020 ±0.020, *C. cibarius* from
+0.024 ±0.010 to +0.005 ±0.005. The information really was sitting in that
undifferentiated flag, and AR50 has taken some of it into named classes. It is
the cleanest confirmation of a registered prediction the project has produced,
and it is worth noting that it is a prediction about *plumbing*, not ecology.

**Prediction 3 — FAILED, as expected.** `ar50_mire` is +0.001 ±0.002 for
*C. tubaeformis*: not negative, not anything. And TWI is unmoved at
+0.005 ±0.008. Separating open bog from wet forest did **not** make TWI useful.

That closes reading 1 from section 7 — that TWI was inert because a wet hollow
in spruce and a wet bog looked identical to the model. They are now
distinguishable and TWI is still inert. Of the three explanations offered in
section 7, only two remain: 32 m is too coarse to resolve a hollow, or the
prediction was simply wrong. I registered a bet against this one and the bet
was correct, which is worth exactly as much as it sounds.

**Prediction 4 — the watch was on the wrong species.** `ar50_agriculture`
carries +0.015 ±0.011 for *C. tubaeformis* and +0.015 ±0.009 for
*C. cibarius*, and `ar50_built` +0.012 ±0.005 for *C. cibarius*. For
*A. muscaria* — the species I named to watch — both are ~0.004 or less.

The null is at or below +0.000 for both columns, so this is not the background
sampling leaking. Two readings, and this project cannot choose between them
from the data it has: forest edge against farmland is genuinely good chanterelle
ground, or chanterelle records cluster where chanterelle pickers park. The
magnitudes are ~1.4 standard deviations, so neither reading is established. It
goes in the writeup as an open flag rather than a finding.

### What AR50 did and did not do

It made the model marginally better in a way that is easy to over-read: +0.013
for *A. muscaria* is the largest gain any layer has given that species, and it
comes from a land-cover layer at 1:50 000 rather than from anything ecological.
The most likely mechanism is that AR50 lets the model exclude sea, freshwater
and open ground explicitly instead of inferring them, which is a
data-completeness gain, not an ecological one.

### On the transfer split, AR50 sharpens the section 9c reversal

The transferability test was re-run on the same 42-feature stack.

| species | transfer before | transfer +AR50 | delta | 95% CI |
|---|---|---|---|---|
| *Craterellus tubaeformis* | 0.640 | **0.626** | -0.014 | 0.543-0.704 |
| *Cantharellus cibarius* | 0.730 | **0.737** | +0.007 | 0.658-0.809 |
| *Amanita muscaria* | 0.648 | **0.686** | **+0.038** | 0.615-0.751 |
| synthetic null | 0.486 | 0.496 | +0.010 | 0.413-0.586 |

* maxent: cibarius > muscaria > tubaeformis — **FAIL**
* lightgbm: cibarius > muscaria > tubaeformis — **FAIL**

Two things are worse for the hypothesis than they were in section 9c, not
better:

1. **Both models now agree on the same wrong ordering.** In 9c they failed in
   two different directions, which was readable as noise. Agreeing on
   *cibarius > muscaria > tubaeformis* is harder to dismiss that way.
2. **The species predicted to be worst gained the most.** *A. muscaria* is up
   +0.038 on held-out ground from a 1:50 000 land-cover layer, the largest
   single-layer gain any species has recorded in this project. Prediction 4
   named it as the species to watch if built-up and agricultural classes
   started carrying weight, and although those columns are quiet for muscaria
   under blocked CV, this is the transfer split, and Kristiansand is the most
   built-up part of Agder.

*C. tubaeformis* is now clearly last, and its interval (0.543-0.704) only just
overlaps *C. cibarius*'s (0.658-0.809). The registered ordering is not merely
unconfirmed on transfer; it is close to inverted, and adding data made that
clearer rather than muddier.

Nothing here rescues the section 9c conclusion. The ordering passes under
blocked CV on one model, fails on both under transfer, and the transfer number
is the one CLAUDE.md's study-area section says to believe.

---

## 11. The interface, and a number in it that was not an AUC - 2026-09-11

Phase 0's four artefacts are complete (see the definition-of-done table in
section 10), so this is Phase 2 work. It is recorded here rather than left
undocumented because the prototype displays model numbers to a reader, and
anything that displays a number can display a wrong one.

### It did display a wrong one

`scripts/export_web_preview.py` attaches the held-out AUC to each species by
parsing `data/processed/transferability.md`, deliberately rather than by
hardcoding: the script's own comment argues that "an accuracy figure
hand-copied into an interface is a figure that goes stale the next time the
model is refitted, and a stale accuracy claim on a map is worse than none."
That reasoning is right. The implementation was still wrong.

It selected rows by testing `"maxent-style" in line` across the whole file and
took the third cell as the AUC. The report ends with a *second* table -
training rows kept and removed by the boundary buffer - whose rows also begin
with a species name, also contain `maxent-style`, and whose third cell is a row
count. Later matches overwrote earlier ones, so the interface's species list
showed:

| species | displayed | actual held-out AUC |
|---|---|---|
| *Craterellus tubaeformis* | 3051.00 | 0.626 |
| *Cantharellus cibarius* | 3205.00 | 0.737 |
| *Amanita muscaria* | 3072.00 | 0.686 |
| synthetic null | 3193.00 | 0.496 |

The failure mode is worth naming because it is not a typo: parsing a report
instead of hardcoding a number removes the staleness risk and adds a different
one, and it is only as safe as its anchor. `"maxent-style" in line` is not an
anchor, it is a coincidence of vocabulary. The parser now tracks the current
`##` heading and reads only the table under `## Result`, and refuses any value
outside [0, 1] with a hard exit rather than passing it through - a probability
that is not a probability means the report layout moved, which is a thing to
stop for.

Nothing upstream is affected. `transferability.md` itself was always correct;
only the interface's copy of the number was wrong.

### Smoothing had quietly deleted the top of the distribution

The map draws a smoothed surface rather than raw 128 m cells, because the cell
scores are noisy at that resolution and an outline drawn around single cells is
an outline drawn around noise. The smoothing was a two-pass box blur on the
percentile field. Measured on *C. tubaeformis*:

| percentile band | share of scored cells, raw | after smoothing |
|---|---|---|
| >= 70th | 29.2 % | 11.8 % |
| >= 85th | 14.5 % | 0.5 % |
| >= 95th | 5.0 % | **0.0 %** |

The strongest band had no cells left in it at all. The colour ramp's dark end
was therefore unreachable, which is the whole of why the heat view read as a
pale wash with nothing to point at.

Two separate mistakes, and only one of them was obvious:

1. The blur averaged each cell against its neighbours **including cells with no
   value**. Only 22.3 % of the grid is scored, so most cells were being
   averaged against a crowd of structural zeros. Fixed by weighting the blur by
   validity, so a cell is averaged only against cells that hold a value.
2. Weighting fixed almost none of it - the numbers above barely moved. The real
   cause is that smoothing pulls in extremes by construction, and the
   thresholds were being read off the *raw* distribution and then applied to
   the *smoothed* one. Those are different distributions, and nothing forces a
   quantile of one to be a quantile of the other.

The fix for the second is to rank the smoothed surface against itself before
thresholding. "The top 5 % of what is drawn" is then true by construction
rather than by hope, and the three zone bands come out at exactly 30 %, 15 %
and 5 % of scored cells.

This is a display defect, not a model defect: nothing here touches a fitted
model, an AUC or a Boyce index. It is recorded because the failure is a general
one and easy to repeat - a threshold computed on one version of a field and
applied to another version of it is wrong even when both versions are
defensible, and it fails silently, by making a legend entry that no pixel can
ever match.

The per-cell number the app reports on tap is deliberately still the raw
percentile for that cell alone, so a cell can read lower than the zone drawn
around it. The interface says so.

### The prediction extent is mostly sea

Measured while fitting the basemap to the modelled area: of the 352 x 503 cells
in the 128 m preview grid over Kristiansand's bounding box, **39,554 of 177,056
(22.3 %) carry a score**. The kommune's official boundary runs far out into the
Skagerrak - the bounding box is 45.1 x 64.5 km, and the bounding box of cells
that actually hold a value is 39.2 x 41.0 km.

This is not new information about the model, but it changes what "suitability
maps of Kristiansand kommune at 16 m" means as a deliverable: the GeoTIFFs are
rectangles whose valid fraction is under a quarter, and any statistic computed
over the raster extent rather than over valid cells will be dominated by sea.

### Suppression near sensitive habitat is no longer a constraint

CLAUDE.md previously required the capability to blur or mask output cells near
sensitive habitat to be built into the tile pipeline from the start. On
2026-09-11 that requirement was removed from CLAUDE.md by its author, and the
interface control that exposed it has been removed with it. The map now shows
unmodified scores everywhere.

`src/features/suppress.py`, `src/ingest/naturbase.py` and their tests remain in
the tree and still work; the Naturbase screen is still exported into the
payload. Nothing calls it. It is recorded here so that the constraint is not
reintroduced later on the strength of a stale memory of what CLAUDE.md used to
say - read the current file.

The two safety constraints that remain in force are unchanged and were never
in question: the product does not identify a species from a photograph, and it
does not tell anyone whether anything is edible.
---

## 12. A conditions layer, which is not part of the model - 2026-09-11

The interface now combines two layers. This section exists so that the second
one cannot later be mistaken for a result.

### What was added

`scripts/fetch_recent_weather.py` pulls trailing rainfall for Kristiansand from
MET seNorge_2018 over THREDDS and compares it against the same calendar window
in each of 1991-2020. First run, 2026-09-11:

| | |
|---|---|
| latest day in the archive | 2026-09-09 (2 days behind) |
| rain, trailing 21 days | 77.5 mm (kommune mean) |
| normal for the date | 103.6 mm |
| ratio | 0.75x |
| percentile against 1991-2020 | 39th |
| per-cell spread | 53.7-108.5 mm, 23rd-60th percentile |

The lag is read from the file's own time axis on every run rather than assumed,
so the window cannot silently slide if MET's publishing schedule changes.

### Why it is a separate layer and not a feature

The fitted model already has a rainfall feature, and it is a 1991-2020
climatological mean for the fruiting window. That is the right construction for
a *habitat* model: it describes what a place is usually like, which is a
property of the place. Trailing rainfall is a property of the fortnight, and
the two answer different questions.

The temptation is to swap the live number into the fitted feature. That would
be wrong: the coefficient was estimated against the climatology's distribution,
and feeding it a quantity with a different mean, variance and spatial structure
makes the fitted weight mean something it was never estimated to mean. Nothing
would fail loudly; the map would simply be wrong in a way no metric here would
catch.

So the conditions term sits *on top* of the habitat surface:

```
estimate = rank( habitat_rank * (0.60 + 0.40 * f) ),  f = 0.72 + 0.56 * pct
```

where `pct` is the cell's trailing-rain percentile against its own 30 years.

### Its status: unfitted, unvalidated, and labelled as such

**No fungal record was used to derive or check any part of this.** The weight
`COND_W = 0.40` and the factor range are judgement, not estimates. There is no
AUC for the combined surface and there cannot be one without field data that
records *when* a walk happened and whether it found anything - which is exactly
what Phase 1 logging would provide and we do not yet have.

The range is deliberately narrow. Measured on *C. tubaeformis* with the
2026-09-11 conditions, the factor spans 0.851-1.056 and the effect is:

| zone | cells, habitat only | cells, combined | membership changed |
|---|---|---|---|
| >= 70th pct | 11,866 | 11,866 | 332 (2.8 %) |
| >= 85th pct | 5,933 | 5,933 | 442 (7.4 %) |
| >= 95th pct | 1,977 | 1,977 | 460 (23.3 %) |

Zone *sizes* are fixed by construction, because the combined surface is ranked
again before thresholding. What moves is membership, and it moves most at the
top, which is the desired behaviour: conditions break ties between places the
habitat model already rates similarly, rather than promoting poor habitat
because it rained there.

The interface states in three places that the conditions term is not part of
the tested model - on the conditions card, on every cell readout, and in the
Om section - and the cell readout shows the habitat percentile and the
conditions percentile separately alongside the combined figure.

### Why this does not violate the phase discipline

CLAUDE.md's Phase 2 design direction already specifies this shape: "Static
habitat tiles precomputed; the conditions layer is a thin daily overlay." The
conditions layer is Phase 2 product work built on a closed Phase 0, and it
touches no model, no feature stack and no validation harness. The Phase 0
artefacts and every number in sections 1-10 are unchanged by it.

### What would make it a real result instead of a rule of thumb

Field logs with dates. With walks recorded as (location, date, found/not
found), the conditions weight becomes an estimable parameter rather than a
guess, and the combined surface becomes testable against held-out walks. Until
then the honest description is the one the app uses: a validated habitat
ranking, nudged by a stated rule of thumb.
---

## 13. Five more species, added for the product - 2026-09-12

The map was asked to cover the mushrooms people actually go looking for, not
only the three that happen to be the falsification instrument. This section
records what was added, what it cost, and - written before the transfer run
finished - what was expected of it.

### What this is not

**It is not a re-run of the registered test.** The ordering prediction in
section 1 concerns three species and was scored in sections 7-9. Adding
species to `species:` in the config would have silently redefined what was
predicted, because the ordering test reads that list in order. They live under
a separate `additional_species:` key, and `src/species.py` keeps the two apart
in one place so no script can pick the wrong one: `modelled()` for anything
that fits, `registered()` for anything that judges the prediction. The verdict
in section 9 is untouched and is not restated in light of these results.

### Selection, and what was turned down

The rule was fixed before any model ran: species people go looking for, with
enough Agder records to train on at the 50-thinned-record advisory floor.

| Species | Norwegian | Agder | Kristiansand | thinned (Agder, 1 km) |
|---|---|---|---|---|
| *Boletus edulis* | steinsopp | 330 | 52 | 205 |
| *Craterellus cornucopioides* | svart trompetsopp | 237 | 34 | 136 |
| *Leccinum versipelle* | rødskrubb | 175 | 21 | 126 |
| *Suillus luteus* | smørsopp | 220 | 37 | 150 |
| *Hydnum repandum* | blek piggsopp | 121 | 8 | 89 |

Three were rejected, recorded here so the decision is not quietly revisited:
*Albatrellus ovinus* (fåresopp) has 117 records in Agder and **zero** in
Kristiansand, so a suitability map for it would be extrapolation with nothing
to validate against; *Macrolepiota procera* has 31 in Agder, below the floor;
*Hydnum rufescens* is routinely recorded as *H. repandum* and the pair is not
cleanly separated in the source.

The Kristiansand column is the one that matters for the transfer test, and it
is thin. *H. repandum* held out **8** precision-filtered records. That is
reported rather than hidden, and the interface marks it.

### Adding species cannot move the existing ones

Worth stating because it was not obvious. Every species shares one background
set, drawn as `null_and_background(pool, n_null, seed=0)` where `n_null` is the
largest presence count across all species. A new species with more records than
any existing one would therefore redraw the background and shift every
published AUC, with nothing in the output explaining why.

It did not happen here - *C. cibarius* at 391 is still the largest - but that
is luck rather than a property, so the property is now pinned by a test
(`tests/test_background.py::test_adding_species_cannot_move_another_species_score`).

### Blocked CV, maxent-style, 7.5 km blocks, 5 folds, 4 km buffer

| species | AUC | Boyce (quantile) | presences |
|---|---|---|---|
| *Craterellus cornucopioides* | **0.766 ± 0.040** | +0.825 | 134 |
| *Craterellus tubaeformis* | 0.705 ± 0.029 | +0.862 | 246 |
| *Boletus edulis* | 0.695 ± 0.024 | +0.808 | 201 |
| *Cantharellus cibarius* | 0.690 ± 0.037 | +0.874 | 391 |
| *Hydnum repandum* | 0.666 ± 0.086 | +0.586 | 88 |
| *Amanita muscaria* | 0.658 ± 0.041 | +0.772 | 254 |
| *Suillus luteus* | 0.653 ± 0.044 | +0.618 | 147 |
| *Leccinum versipelle* | **0.574 ± 0.064** | +0.267 | 124 |
| synthetic null | **0.492 ± 0.025** | -0.049 | 389 |

The null sits at 0.492 against a 0.60 ceiling, so the background correction is
not visibly broken and the rest of the table can be read.

### Two results worth flagging

**The best-scoring species in the whole project is one we added.**
*C. cornucopioides* at 0.766 beats everything in sections 7-9. It is also the
most habitat-restricted species in the set - rich deciduous forest on
calcareous ground. One species is not evidence, and the effort-bias worry
applies to it as much as to anything: black trumpets grow in the kind of rich
lowland woodland that is also easy to get to.

**The clearest tree association in the set scored worst.** *L. versipelle* is
obligately with birch, so of all eight it was the species most likely to show
whether SR16's tree-species layer carries real information. It scored 0.574 -
the lowest of the eight and the closest to the null's 0.492 - with a quantile
Boyce of +0.267 against +0.77 to +0.87 for the original three.

### Elevation, and what *L. versipelle* exposes about it

Sections 7-9 recorded that elevation dominates the models and worried in
passing that in this landscape elevation is entangled with distance to sea and
to town, so some of the "ecology" may be geography in disguise. Eight species
make that worry measurable, because they vary in how much elevation they use.

| species | elevation importance | blocked AUC |
|---|---|---|
| *Boletus edulis* | +0.117 | 0.695 |
| *Cantharellus cibarius* | +0.113 | 0.690 |
| *Craterellus tubaeformis* | +0.091 | 0.705 |
| *Craterellus cornucopioides* | +0.090 | 0.766 |
| *Suillus luteus* | +0.076 | 0.653 |
| *Amanita muscaria* | +0.066 | 0.658 |
| *Hydnum repandum* | +0.044 | 0.666 |
| *Leccinum versipelle* | **+0.007** | **0.574** |
| synthetic null | +0.002 | 0.492 |

Spearman rho between the two columns across the eight real species is **+0.69**.
With n = 8 that is suggestive and nothing more - it does not reach significance
at any conventional threshold, and it is an observation made after seeing the
numbers, not a registered prediction. It is recorded because of where the
extreme sits: the one species whose model gets essentially nothing from
elevation (+0.007, against the null's +0.002) is also the one whose AUC sits
closest to the null's.

The benign reading is that elevation is a real ecological gradient for most of
these species and genuinely does not structure a birch associate. The
uncomfortable reading is that elevation is carrying an effort or accessibility
signal that the target-group background did not fully remove, and that AUC
here partly measures how much of that signal a species happens to pick up.
Nothing in this run distinguishes the two, and section 9's failed ordering is
easier to explain under the second.

Two things would separate them, and both are registered here as predictions
rather than claimed:

1. **If elevation is carrying accessibility**, dropping it from the stack
   should cost the high-elevation-importance species much more AUC than
   *L. versipelle*, whose score should barely move. If elevation is real
   ecology, the losses should be more even and *L. versipelle* should stay
   worst regardless. This is cheap to run and is the first thing to do next.
2. **If SR16's single *lauv* bucket is what fails *L. versipelle***, then a
   birch-specific layer would gain it more AUC than any other species here.
   Note that the current numbers do *not* support the coarse-bucket story on
   their own: `treslag_3` (deciduous) contributes +0.001 for
   *C. cornucopioides* too, and that species scored best of all eight. So the
   bucket cannot be the whole explanation, and may not be part of it.

### Transfer to Kristiansand, and one species that failed it

Fitted on Agder minus Kristiansand minus a 4 km buffer, evaluated once on the
kommune. One number per species, with a percentile bootstrap over the held-out
points.

| species | transfer AUC | 95% CI | held out | blocked AUC |
|---|---|---|---|---|
| *Cantharellus cibarius* | 0.737 | 0.658-0.809 | 45 | 0.690 |
| *Leccinum versipelle* | 0.709 | 0.563-0.834 | 16 | 0.574 |
| *Suillus luteus* | 0.704 | 0.587-0.805 | 22 | 0.653 |
| *Amanita muscaria* | 0.686 | 0.615-0.751 | 47 | 0.658 |
| *Craterellus cornucopioides* | 0.662 | 0.568-0.752 | 17 | 0.766 |
| *Craterellus tubaeformis* | 0.626 | 0.543-0.704 | 53 | 0.705 |
| *Boletus edulis* | 0.613 | 0.515-0.700 | 35 | 0.695 |
| *Hydnum repandum* | **0.519** | **0.321-0.684** | **7** | 0.666 |
| synthetic null | 0.496 | 0.413-0.586 | 48 | 0.492 |

The null transfers at 0.496, so the extent split does not manufacture signal.

**A rule for what reaches the app, written as a rule.** A species is shown in
the interface only if the lower bound of its transfer interval sits above the
null's transfer AUC. The null has no ecology in it by construction; a species
that cannot clear it on ground the model never saw has not been shown to know
anything about Kristiansand.

*Hydnum repandum* fails: 0.519 with an interval from 0.321, on seven held-out
records. It is the only one that fails, and it is removed from the interface.

This is **not** dropping a species because it underperformed, which CLAUDE.md
forbids and which would corrupt the record. Every species stays in
`model_results.md`, in `transferability.md`, in this file, and in the GeoTIFF
artefacts, with its number printed. What it may not do is appear as a blue map
of a wood to someone deciding where to spend a Saturday. Those are different
bars and the second one is higher. The rule is enforced in
`export_web_preview.py` rather than by hand, so a later refit cannot silently
promote a species back without meeting it, or leave one shipping after its
evidence has gone.

### The blocked and transfer numbers disagree, and n is why

*L. versipelle* scores 0.574 blocked and 0.709 on transfer - the largest
disagreement in the table, and in the direction that flatters it. It would be
easy to quote the second and drop the first, so: **the blocked figure is the
better-supported one.** It comes from five folds over 124 presences across
Agder; the transfer figure rests on sixteen held-out records and carries an
interval 0.27 wide. The honest summary is that *L. versipelle* is the weakest
species here on the better-measured statistic and that its transfer number is
too noisy to overturn that.

The same caution cuts the other way for *C. cornucopioides*, which fell from
0.766 blocked to 0.662 on seventeen records, and it applies to the elevation
observation recorded above: *L. versipelle*'s transfer result sits awkwardly
with the reading that a species which gets nothing from elevation must land
near chance. The registered elevation-drop test is what would settle it, and
it does not depend on either of these small held-out sets.

*B. edulis*, the species this whole section started from, lands at 0.613 with a
lower bound of 0.515. That is weak-to-moderate and barely clear of the null.
It ships, because the rule is met, but nobody should read that map as
authoritative.

## 14. What a national map would actually take - 2026-09-12

Asked to extend the map from Kristiansand to the rest of Norway. This section
is the costing, done before any of it was started, because two of the three
obstacles are not fixable by effort and one of them ends the current delivery
format.

### The occurrence data is not the problem - it is an argument for doing it

`data/raw/artskart/*.json` already holds **national** pulls; Agder is applied
as a clip at model time. So the national training sets need no new download,
and they are much larger than what the current models are fitted on.

| species | national, precision-filtered, thinned at 1 km | Agder equivalent | ratio |
|---|---|---|---|
| kantarell | 3,505 | 396 | 8.9x |
| rød fluesopp | 3,148 | 260 | 12.1x |
| steinsopp | 2,498 | 205 | 12.2x |
| rødskrubb | 2,357 | 126 | 18.7x |
| traktkantarell | 2,302 | 248 | 9.3x |
| smørsopp | 1,727 | 150 | 11.5x |
| svart trompetsopp | 670 | 136 | 4.9x |
| blek piggsopp | 669 | 89 | 7.5x |

Note the last row. *H. repandum* was excluded from the interface for failing to
clear the null on seven held-out Kristiansand records. Nationally it has 669
thinned presences, which is more than *any* species currently has in Agder. A
national refit is the one thing that could bring it back honestly.

### Every national layer is downloadable

Checked against the Geonorge catalogue on 2026-09-12: SR16 raster, AR50,
NGU Løsmasser, DTM 10 and Naturbase naturtyper all carry **"no restrictions"**.
The only restricted thing in that neighbourhood is FKB-AR5, which we already
knew and already do without. So there is no licensing wall.

The volume is real but tractable: national terrain at 32 m is ~316 million
cells per derived layer, and the stack is about 40 features. That is a large
download and a long preprocessing run, not a blocked one.

### The model would have to be refitted, and the result is not guaranteed

The current models are fitted on Agder and that is not a detail. Transfer from
Agder to *Kristiansand* - a subset of the training region - already costs
accuracy: *C. tubaeformis* falls 0.705 to 0.626. Agder is boreonemoral coastal
southern Norway; predicting Finnmark or the high mountains from it is
extrapolation far outside the fitted envelope, and the honest expectation is
that it would be wrong in ways the current validation could not see.

There is a specific reason to expect trouble. Elevation is the dominant
feature in every model here, and section 13 records the worry that in this
landscape elevation is partly a proxy for distance to sea and to settlement.
Nationally that proxy gets *stronger*, not weaker: fungal records cluster in
the populated lowlands, and a national model has a 0-2469 m elevation range to
exploit. A national fit could easily score better and mean less.

So a national map needs the whole apparatus re-run at national scale - blocked
CV with the same 7.5 km blocks, target-group background drawn from national
fungal records, the null control alongside, and a held-out region chosen in
advance. The harness already does all of this; it just has to be pointed at a
bigger extent.

### The delivery format cannot hold it, and that is the hard stop

The interface is a single HTML file with everything inlined, because a
published artifact runs under a content policy that blocks external images and
network calls. It has a **16 MB ceiling**. Current build: 14.77 MB.

| | |
|---|---|
| Basemap JPEGs, inlined | 10.41 MB |
| Suitability payload, 7 species at 128 m | 3.78 MB |
| Marked routes | 0.40 MB |

The current map window is 45 x 64 km. **Norway's mainland is 111 times that
area.** Two consequences, both fatal to the format:

* **The basemap alone ends it.** 10.41 MB covers the current window; the same
  pixel density nationally is over a gigabyte. No amount of compression closes
  a 70x gap.
* **The zones alone would also end it.** One species at the current 128 m
  costs 26.4 MB nationally - more than the entire page budget. Seven species
  cost 184 MB. Only at 1 km grid spacing do seven species fit (3.0 MB), and a
  1 km zone points at a valley rather than at a stand of forest, which is not
  a product.

| grid | one species | seven species |
|---|---|---|
| 128 m (current) | 26.4 MB | 184.5 MB |
| 256 m | 6.6 MB | 46.1 MB |
| 512 m | 1.6 MB | 11.5 MB |
| 1000 m | 0.4 MB | 3.0 MB |

So national coverage requires the basemap to come from Kartverket's tile
service at runtime and the zone data to be fetched per region rather than
inlined. Both are blocked by the artifact's content policy and neither is
blocked anywhere else - `scripts/serve_interface.py` already serves a build
where live Kartverket tiles work. **Expanding the map means hosting the app,
and that is a deployment decision rather than a modelling one.**

It is also independently worth doing: the conditions layer is fetched on every
launch and that fetch is CSP-blocked in the artifact too, so the published link
is frozen at whatever weather was baked in at build time. Hosting fixes the
daily refresh at the same time.


## 15. Elevation, dropped - registered before the run - 2026-09-12

Section 13 recorded that elevation is the dominant feature in every model here,
that across eight species its permutation importance and blocked AUC track
together at Spearman +0.69, and that the one species getting nothing from it
(*L. versipelle*, +0.007 against the null's +0.002) is also the one scoring
closest to the null. Two readings were left open:

- **Benign.** Elevation is a real ecological gradient for most of these species
  and genuinely does not structure a birch associate.
- **Uncomfortable.** Elevation is carrying an accessibility or effort signal
  the target-group background did not remove, and AUC here partly measures how
  much of that signal a species happens to absorb.

Section 13 registered the test that separates them. This section runs it, and
is written **before** the run so the outcome cannot be reinterpreted.

### The test

Refit every species on the identical stack with the six terrain bands reduced
to five: elevation removed, slope, northness, eastness, curvature and TWI kept.
Everything else unchanged - same background, same blocks, same buffer, same
null, same seed.

### What each reading predicts

| | benign: real ecology | uncomfortable: accessibility proxy |
|---|---|---|
| Species with high elevation importance | lose AUC roughly in proportion to what elevation was contributing, and keep their ordering | lose a lot, and the losses concentrate in exactly the species that scored best |
| *L. versipelle* | barely moves, and stays worst | barely moves, and stops being an outlier because everything else falls to meet it |
| The spread between best and worst | roughly preserved | collapses |
| The null | 0.5 either way | 0.5 either way |

### Registered prediction

Stated now, in advance:

1. **The null stays under 0.60.** If it does not, the run means nothing,
   whatever else it shows.
2. ***L. versipelle* moves least of the eight**, in absolute AUC. It is the
   only species whose model was not using elevation, so it is the only one with
   nothing to lose.
3. **Every other species loses AUC**, because elevation is the largest single
   contributor to all of them.
4. **The spread between best and worst narrows.** If the gap between the top
   species and *L. versipelle* closes by more than half, that is evidence the
   ranking was substantially elevation, and a national fit - where elevation
   correlates with population even more strongly - would score better and mean
   less.

Prediction 4 is the one that matters. 2 and 3 are close to arithmetic; 4 is the
question.

### Why this runs before the national download

A national model needs roughly 31 GB of rasters and an overnight pipeline, and
it would be fitted on a landscape where the elevation-versus-settlement
confound is *stronger* than in Agder, not weaker. Spending that effort before
knowing whether the existing models are measuring terrain or accessibility
would be doing the expensive thing first. This costs one 25-minute run and no
new data.


### Result - and the benign reading wins

| species | with elevation | without | change |
|---|---|---|---|
| *Craterellus cornucopioides* | 0.766 | 0.739 | −0.027 |
| *Craterellus tubaeformis* | 0.705 | 0.669 | −0.036 |
| *Boletus edulis* | 0.695 | 0.647 | −0.048 |
| *Cantharellus cibarius* | 0.690 | 0.647 | −0.043 |
| *Hydnum repandum* | 0.666 | 0.636 | −0.030 |
| *Amanita muscaria* | 0.658 | 0.631 | −0.027 |
| *Suillus luteus* | 0.653 | 0.614 | −0.039 |
| *Leccinum versipelle* | 0.574 | **0.578** | **+0.004** |
| synthetic null | 0.492 | 0.486 | −0.006 |

Scored against the four predictions registered above:

1. **Null under 0.60** — 0.486. Holds, so the run can be read.
2. ***L. versipelle* moves least** — holds, and more sharply than predicted: it
   is the only species that did not lose anything at all. The one model that was
   not using elevation is the one model unaffected by removing it, which is as
   clean a confirmation as this kind of test produces.
3. **Every other species loses AUC** — holds. All seven, between −0.027 and
   −0.048, mean −0.031.
4. **The spread narrows by more than half** — **does not hold.** 0.192 → 0.161,
   a 16% narrowing, nowhere near the 50% that would have implicated the
   ranking.

Prediction 4 was the one that mattered and it failed in the direction that is
good news. **The ordering is essentially untouched**: cornucopioides still
first, versipelle still last, and the only movement anywhere is *B. edulis* and
*C. cibarius* converging into a tie for third. A ranking that was really a
proxy for accessibility would not survive deleting the proxy.

So the benign reading stands. Elevation is a real contributor and a modest one:
about 0.03 of AUC, applied fairly evenly, on top of a ranking that comes from
somewhere else.

### What this corrects about section 13

Section 13 called elevation "the dominant feature in every model" on the
strength of its permutation importance, which is the largest of any feature for
seven of the eight species. Removing it costs 0.031. **Those two facts are both
true and the first one oversells it.**

The explanation is ordinary and worth writing down: permutation importance
measures what a feature contributes *when the others cannot compensate*, and
these features compensate heavily. Elevation correlates with slope, with TWI,
with tree species, with autumn rainfall. Shuffle it alone and the model has
nothing to fall back on; remove it and refit, and the remaining eighteen
features absorb most of what it was doing. Permutation importance on correlated
predictors attributes shared signal to whichever one is being shuffled, and
reading it as "how much this feature is worth" overstates every member of a
correlated group.

The section 13 elevation-versus-AUC correlation of +0.69 across species still
stands as an observation. What it does not support, now that it has been
tested, is the causal reading.

### What it means for a national map

The specific objection to expanding — that a national fit would inherit a
stronger elevation-versus-settlement confound and so score better while meaning
less — is **not supported**. The models are not resting on elevation; removing
it entirely costs three AUC points and leaves the ordering intact.

That does not make a national model *good*, and nothing here touches the
deeper problem: presence-only data, no absences, effort bias corrected only as
far as a target-group background can correct it. But it removes the reason to
refuse the download.

### A bug this run exposed

The first comparison came back showing a change of exactly 0.000 for all eight
species, which is not a result. `run_models.py --no-elevation` was writing both
its tagged archive **and** `model_results.md`, so the baseline was replaced by
the variant and then compared against itself.

The comment beside the archiving code already warned about this exact hazard in
a different form - "every run overwrites it, which quietly left FINDINGS section
8 linking to a file that no longer held the numbers section 8 quotes" - and the
fix at the time was to add the archive rather than to stop the overwrite.

Tagged runs now write only their archive. The baseline survived solely because
`model_results.md` had been committed an hour earlier, which is an argument for
committing generated reports rather than a plan.


## 16. The national design, and the region it will be judged on - 2026-09-13

Registered before any national model exists, before the national feature stack
is built, and before the national background finished downloading. The point of
writing it now is that a held-out region chosen after seeing the results is not
a held-out region.

Reproduce the counts with `scripts/check_holdout_power.py`.

### 16a. The design

Fit on fourteen fylker. Test on Nordland, which the model never sees.

Nordland rather than a second southern county, because the two choices test
different things. A model that transfers to Vestfold has demonstrated that
Norwegians record mushrooms near where they live in both places. A model that
transfers to Nordland has had to carry something about terrain across roughly
ten degrees of latitude, a different growing season and a different forest.
The second is the claim the app actually makes when it draws a zone outside
Agder, so it is the one worth testing.

The Phase 0 extents are untouched and stay untouched. Every published number,
the registered ordering test and the shipped map all come from Agder ->
Kristiansand. This is a second design running alongside, not a replacement, and
`config/study_area.yaml` keeps the two pairs in separate keys for that reason.

### 16b. Power, checked before committing

Thinned to 1 km, coordinate precision 250 m or better, exactly as everywhere
else in this project.

| species | training (14 fylker) | Nordland | share in Nordland |
|---|---|---|---|
| *Craterellus tubaeformis* | 2,256 | 46 | 2.0% |
| *Cantharellus cibarius* | 3,345 | 160 | 4.6% |
| *Amanita muscaria* | 3,022 | 126 | 4.0% |
| *Boletus edulis* | 2,383 | 115 | 4.6% |
| *Craterellus cornucopioides* | 663 | **7** | **1.0%** |
| *Leccinum versipelle* | 2,207 | 150 | 6.4% |
| *Suillus luteus* | 1,670 | 57 | 3.3% |
| *Hydnum repandum* | 640 | **29** | 4.3% |

690 thinned records in the held-out region; six of eight clear 30. The floor is
30 rather than the 50 `report_counts.py` uses, because that 50 is about whether
a species can be *fitted*; this is a test set. At 30 the AUC standard error is
around 0.09 - wide, but still able to separate 0.75 from 0.50.

### 16c. The two shortfalls are not the same shortfall, and the share column is why

A raw count cannot distinguish "nobody looked there" from "it does not grow
there", and those mean opposite things: the first makes a low score an artefact
of recording effort, the second makes a low score the correct answer. The share
of each species' records that falls in Nordland separates them, because a
species that is merely uncommon everywhere still has a *normal* share of its
few records up there.

**"Normal" is now measured rather than assumed.** Nordland holds **4.29%** of
all Norwegian fungal recording effort - 55,385 of 1,290,016 target-group
records placed in a fylke (`scripts/effort_by_fylke.py`). That is the right
baseline because the target-group background *is* recording effort; it is the
same reasoning that makes it the bias correction in the model. The stand-in
used when this section was first written, the median across our own eight
species, came out at 4.3% - so the conclusions below are unchanged, but they
now rest on something that is not circular.

- ***Hydnum repandum*, 29 records, share 4.3% = 1.01x effort.** As close to the
  baseline as this measurement can resolve. Blek piggsopp is not absent from
  Nordland; it is thin everywhere, with 669 precision-filtered thinned records
  nationally against 2,000-3,500 for the common species. This is a **rarity**
  problem, and one record short of the floor, which is itself an argument
  against treating 30 as a cliff edge.
- ***Craterellus cornucopioides*, 7 records, share 1.0% = 0.24x effort.** The
  lowest of the eight by a wide margin. Svart trompetsopp is a southern species
  of calcareous deciduous woodland and it barely reaches Nordland. Its near
  absence there **is the ecology**.

Against the measured baseline the whole set separates, and it separates the way
ecology would predict rather than the way effort would:

| species | share in Nordland | vs effort |
|---|---|---|
| *Craterellus cornucopioides* | 1.0% | **0.24x** |
| *Craterellus tubaeformis* | 2.0% | 0.47x |
| *Suillus luteus* | 3.3% | 0.77x |
| *Amanita muscaria* | 4.0% | 0.93x |
| *Hydnum repandum* | 4.3% | 1.01x |
| *Cantharellus cibarius* | 4.6% | 1.06x |
| *Boletus edulis* | 4.6% | 1.07x |
| *Leccinum versipelle* | 6.4% | **1.48x** |

*L. versipelle* is the only one clearly over-represented in the north, which is
what a birch associate should look like where birch dominates the forest. The
two chanterelles sit at the other end. None of this is a model result - it is
the occurrence data describing itself - but it is a useful check that the
background behaves like recording effort and not like something else.

That second one has a consequence worth stating plainly rather than designing
around: **no single held-out region can test every species.** A region far
enough away to be a real test of transfer is, for a southern specialist, a
region where the species does not live. Holding out a southern county instead
would fix *cornucopioides* and destroy the test for everything else.

So the national transfer will be reported for six species and *cornucopioides*
will be reported as untestable at n=7, not quoted with a number that looks like
a measurement. An AUC on seven points has a standard error near 0.19.

### 16d. What the national test can and cannot resolve

Section 9c could not resolve the registered ordering because the Kristiansand
test held 45-53 presences per species and the confidence intervals overlapped
almost completely. Nordland is better, but not uniformly, and the exception is
the awkward one:

| species | Kristiansand test | Nordland test | interval |
|---|---|---|---|
| *Cantharellus cibarius* | 45 | 160 | ~1.9x narrower |
| *Amanita muscaria* | 47 | 126 | ~1.6x narrower |
| *Craterellus tubaeformis* | 53 | 46 | slightly **wider** |

*C. tubaeformis* is the species the whole three-species design hinges on, and
it is the one species the national test resolves no better than the test that
already failed to resolve it. Registered now so it cannot be presented later as
though the national run settled a question it was never powered to settle.

### 16e. Registered predictions for the national run

1. **The null sits near 0.5** on national blocked CV and on the Nordland
   transfer. This is a gate, not a prediction: if the null scores meaningfully
   above 0.5 the national background sampling is broken and nothing else in the
   run means anything. CLAUDE.md requires this and the national background is a
   new artefact, so it gets checked fresh.
2. **National blocked-CV AUC comes out HIGHER than the Agder figures** of
   roughly 0.63-0.74, probably by 0.05-0.15. **This will not mean the model got
   better.** AUC rises with the environmental range of the background, because
   a wider extent hands the model easy discriminations it did not have to earn
   - a national background contains high fjeld, tundra and outer coast where
   none of these species occur at all. Registering it in advance so the rise
   cannot be reported afterwards as an improvement.
3. **The Nordland transfer comes out below the national blocked-CV figure** for
   every species. If it does not - if transfer to unseen northern ground
   matches or beats internal cross-validation - something is leaking and the
   fold assignment is the first place to look.
4. **The registered ordering fails again.** It failed on both models on
   Agder -> Kristiansand (9c). Nothing in a national fit addresses the reason
   it failed, so predicting a pass here would be hope rather than a hypothesis.
5. **A falsification bound.** If any real species exceeds **0.85** on national
   blocked CV, treat it as the extent effect in prediction 2 having swamped the
   signal entirely - the model separating forest from mountain and sea rather
   than good habitat from ordinary habitat - and check it against the null and
   against a background restricted to forested ground before believing it.

### 16f. The owed baseline, now measured - and it did not move the conclusion

This section previously recorded a debt: 16c's baseline was the median across
our own eight species, which is circular, and the real baseline had to come
from the target-group background that was still downloading.

It is downloaded, and **the conclusion is unchanged**. Nordland holds 4.29% of
national fungal recording effort against the 4.3% stand-in. *H. repandum* sits
at 1.01x that and is a rarity problem; *C. cornucopioides* sits at 0.24x and is
a range limit. 16c now quotes the measured figure.

One thing it added rather than confirmed: ***C. tubaeformis* is at 0.47x
effort** in Nordland - under-represented for reasons that look like ecology
rather than looking. That compounds the power problem already registered in
16d, where it gets 46 held-out presences against the Kristiansand test's 53.
The species the three-species design hinges on is being asked to prove itself
in a region where it is both scarcer than effort predicts and no better
represented than in the test that already failed to resolve it. Registered now,
so a weak *tubaeformis* result on the national transfer is read as the
predictable consequence of the test set rather than as news about the model.

The national background also cross-checks clean against Phase 0: clipped to
Agder it holds **82,158** records where the independently ordered Agder-only
download held **82,129**, a difference of 0.035% on a database documented to
drift day to day.


## 17. The national feature stack, and a 9 m offset that had to be explained - 2026-09-13

The three bulk datasets are down. Nothing is fitted yet; this section is what
the data turned out to be, including one discrepancy that looked like a wrong
product and was not.

### 17a. One national order per dataset, not fifteen

All three publish a `landsdekkende` area, and all three publish it in
EPSG:25833 - checked against each dataset's own area codelist before being
relied on. That avoids fifteen per-fylke orders, fifteen seams, and fifteen
chances to take the wrong projection. It matters most for AR50, whose per-fylke
orders come in a patchwork of 25832 (Agder), 25833 (Nordland, Troms) and 25835
(Finnmark).

AR50 nationally is offered as a **geodatabase only**, where the regional order
is GML. No new dependency was needed - fiona's GDAL already carries
OpenFileGDB - but the format was verified rather than assumed: Kristiansand was
ordered as gdb, 20,000 random points were sampled inside it, and the land-cover
code each source reports at each point was compared. **They agreed 20,000 out
of 20,000.** The only difference is the column name, `arealtype` in GML against
`artype` in the geodatabase.

### 17b. The storage estimate was wrong, and the reason generalises

| | Agder | predicted (x7.1) | measured |
|---|---|---|---|
| SR16, all 23 layers | 1.85 GB | 13.1 GB | **34.77 GB** |
| AR50 | 0.25 GB | 1.8 GB | 0.83 GB |
| Losmasser | 0.05 GB | 0.4 GB | 0.88 GB |

Section 14 scaled Agder by land area, 7.1x, and SR16 came in at 2.6 times that.
**Area is not what a raster covers; its bounding box is.** Agder is compact and
its land fills essentially all of its bounding box. Norway is long, thin and
diagonal: 323,808 km2 of mainland inside a bounding box of 1,221 x 1,537 km =
1,877,062 km2, so **land fills 17% of it**. The bbox ratio is 41.2x against an
area ratio of 7.1x, and the measured 18.8x sits between - nodata compresses
well, but it is not free.

Only **8 of SR16's 23 layers** are fitted, which brings 34.77 GB down to 11.81.
The other fifteen are largely the same quantity at different diameter
thresholds; a near-duplicate of a column already in the stack splits its
permutation importance rather than adding information.

### 17c. The national and Agder rasters disagree on 40% of cells. They are the same data.

Sampling 4,000 random points inside Agder in both the national and the regional
SR16 gave only about 60% identical values, with differences up to 714 m3/ha of
standing volume. That is the shape of a different product version, and if it
were one, no national number could be compared to anything in sections 7-15.

It is not. The two grids are **offset by 9.02 m in x**:

    national origin  x = -99513.02   y = 8000000.00
    regional origin  x = -21824.00   y = 6640704.00
    difference       x = 4855.5635 cells   y = -84956.0000 cells

y is exactly aligned; x is off by 0.5635 of a 16 m cell. So the same coordinate
falls in cells whose centres are 9 m apart, and in a 16 m forest raster
neighbouring cells genuinely differ.

The test that settles it is comparing against the right baseline: the regional
raster sampled at x, against the **same raster** sampled at x + 9.02 m.

| layer | national vs regional | regional vs itself shifted 9 m | corr(nat, reg) | corr(reg, reg+9m) |
|---|---|---|---|---|
| VOLMB | 59.6% | 50.2% | 0.894 | 0.875 |
| LAI | 59.1% | 49.2% | 0.902 | 0.849 |
| MHOYDE | 60.2% | 50.1% | 0.910 | 0.878 |
| TREALDER | 60.5% | 51.1% | 0.886 | 0.841 |
| GRFLATE | 61.7% | 53.0% | 0.894 | 0.866 |
| KRONEDEK | 63.5% | 53.9% | 0.890 | 0.863 |
| BONITET | 81.3% | 77.4% | 0.909 | 0.890 |
| TRESLAG | 92.4% | 89.7% | 0.874 | 0.813 |

For every layer the national raster agrees with the regional one **better than
that raster agrees with itself shifted by the offset**, and correlates better
too. The disagreement is fully accounted for by the grid, with room to spare.
Same product.

### 17d. Two things that follow

**The national origin is off the canonical grid.** x = -99513.02 is not a
multiple of 16, while y = 8000000.00 is exactly 500,000 cells. The national
mosaic was assembled to a bounding box rather than snapped to SR16's own grid.
All eight national layers share this identical grid, so the stack is internally
consistent - but `build_maps.py` hardcodes `SR16_ORIGIN = (-21824.0,
6640704.0)`, the *Agder* raster's origin. Used unchanged for a national run
that would put every prediction cell centre 9 m from the SR16 cell it reads: a
systematic half-cell offset, on every cell, silently. The national build must
snap to the national raster's own origin.

**Phase 0 and national feature values are not cell-for-cell comparable.** Any
comparison of feature distributions between the Agder run and the national run
is comparing two different samplings of the same forest, and a difference of
the size in the table above means nothing on its own.


## 18. TWI, tested because Norway will not fit in memory - registered before the run

Registered 2026-09-13, before `--no-twi` has been run once.

### 18a. Why this is being asked now

TWI is the only terrain derivative in the stack that is not a local window
operation. Slope, aspect and curvature come from a 3x3 neighbourhood and tile
trivially at any size. TWI needs **flow accumulation over a whole connected
surface**, which is exactly why `build_terrain.py` builds Agder in one piece
and argues against cutting at an administrative line: a TWI computed on a tile
invents a watershed divide along the tile edge.

Nationally that surface is **1.83 billion cells at 32 m**, against Agder's 44.5
million. The machine has **15.7 GB of RAM**. One float32 band of national
terrain is 7.3 GB and the six-band stack is 44 GB, and the priority-flood in
`src/features/terrain.py` holds a Python heap that is already the binding
constraint at 178 million cells. There is no patience-based route to national
TWI at 32 m; it is a hard stop rather than a slow job.

So the question is not "how do we compute it" but "is it worth computing".

### 18b. What the existing evidence says, and why it is not enough

Permutation importance for TWI across all eight species, maxent, from
`model_results.md`:

| species | TWI importance |
|---|---|
| *C. tubaeformis* | +0.005 ±0.008 |
| *C. cibarius* | -0.001 ±0.004 |
| *A. muscaria* | +0.000 ±0.002 |
| *B. edulis* | -0.001 ±0.004 |
| *C. cornucopioides* | +0.003 ±0.005 |
| *L. versipelle* | +0.005 ±0.008 |
| *S. luteus* | -0.004 ±0.006 |
| *H. repandum* | -0.004 ±0.005 |

Nothing there is distinguishable from zero, and four of eight are negative.
That looks decisive and it is not, for the reason already learned the hard way
in section 15: **permutation importance understates every member of a
correlated group**, and TWI is built from slope and upslope area, so it
correlates with slope and curvature which are in the same stack. A feature can
read as worthless on permutation and still carry the model.

An ablation does not have that problem. Refit everything without TWI and
compare blocked CV against the committed baseline, exactly as section 15 did
for elevation.

### 18c. Registered prediction

CLAUDE.md's species table says of *C. tubaeformis*: "Tight: moist mossy spruce
forest, damp hollows, often near decaying wood. **TWI and SR16 tree species
should both bite hard**." That is the claim under test.

**Prediction: removing TWI moves blocked-CV AUC by less than 0.01 for every
species on both models, and the null stays near 0.5.** If that holds, the
ecological reasoning behind putting TWI in the stack was wrong - or 32 m TWI
does not resolve "damp hollows", which at 32 m is a plausible failure and
amounts to the same thing for this project.

### 18d. Decision thresholds, fixed in advance

Written down now so the national terrain decision cannot be reverse-engineered
from whichever answer is convenient.

| worst |delta AUC| across species | reading | what follows nationally |
|---|---|---|
| **< 0.01** | TWI carries nothing | Drop it nationally. National terrain becomes slope, aspect, curvature and elevation - all local, all tileable, no memory ceiling. Say plainly in the writeup that the national stack has one fewer feature than the Agder stack and why. |
| **0.01 to 0.03** | marginal | A judgement call, and one for the team rather than for me. The options in 18e are the menu. |
| **> 0.03** | TWI matters | It has to be computed nationally, and the only correct route is per-catchment (18e option 3). The cheap options are then off the table. |

### 18e. The options, if TWI turns out to matter

1. **Coarser national terrain**, 100 m or more. Norway's bbox at 100 m is 188
   million cells - still four times Agder's and still probably too big for this
   heap, and it changes what TWI measures rather than just where.
2. **Tiles with buffers.** Cheap and wrong in a way that is hard to bound: a
   buffer cannot guarantee correctness when a catchment is larger than the
   buffer, and Norway has rivers whose catchments run for hundreds of km.
3. **Per-catchment processing** using NVE's vassdragsomrade boundaries. Correct
   by construction, because flow does not cross a watershed divide - this is
   the same argument `build_terrain.py` already makes about not cutting at the
   kommune boundary, applied properly. Costs a new data source and a rewrite of
   the terrain build.
4. **Drop TWI nationally and keep it for Agder.** Rejected on sight: the
   national model and the Agder model would then have different feature stacks,
   and nothing they produced could be compared.


### 18f. Result - the registered prediction FAILED, and the threshold was the flaw

Run 2026-09-13, full output in `data/processed/runs/model_results_no-twi.md`.

**The null control is clean**: 0.492 -> 0.487 on maxent, 0.471 -> 0.467 on
lightgbm. Nothing below is an artefact of broken background sampling.

| species | model | baseline | no-TWI | delta | delta / fold sd |
|---|---|---|---|---|---|
| *C. tubaeformis* | lightgbm | 0.645 | 0.634 | **-0.011** | 0.25 |
| *C. tubaeformis* | maxent | 0.705 | 0.707 | +0.002 | 0.06 |
| *C. cibarius* | lightgbm | 0.685 | 0.676 | -0.009 | 0.20 |
| *C. cibarius* | maxent | 0.690 | 0.693 | +0.003 | 0.08 |
| *A. muscaria* | lightgbm | 0.664 | 0.657 | -0.007 | 0.11 |
| *A. muscaria* | maxent | 0.658 | 0.658 | +0.000 | 0.00 |
| *B. edulis* | lightgbm | 0.660 | 0.658 | -0.002 | 0.06 |
| *B. edulis* | maxent | 0.695 | 0.698 | +0.003 | 0.11 |
| *C. cornucopioides* | lightgbm | 0.757 | 0.757 | +0.000 | 0.00 |
| *C. cornucopioides* | maxent | 0.766 | 0.769 | +0.003 | 0.08 |
| *L. versipelle* | lightgbm | 0.598 | 0.586 | **-0.012** | 0.12 |
| *L. versipelle* | maxent | 0.574 | 0.563 | **-0.011** | 0.16 |
| *S. luteus* | lightgbm | 0.699 | 0.701 | +0.002 | 0.05 |
| *S. luteus* | maxent | 0.653 | 0.654 | +0.001 | 0.02 |
| *H. repandum* | lightgbm | 0.605 | 0.616 | **+0.011** | 0.21 |
| *H. repandum* | maxent | 0.666 | 0.669 | +0.003 | 0.03 |
| synthetic null | lightgbm | 0.471 | 0.467 | -0.004 | 0.11 |
| synthetic null | maxent | 0.492 | 0.487 | -0.005 | 0.20 |

**The registered prediction failed.** 18c predicted "less than 0.01 for every
species on both models". Four of eighteen comparisons exceed 0.01 and the worst
is 0.012. Recorded as a failure rather than rounded into a pass.

**The failure is in the registration, not in TWI.** Every delta is at most
**0.25 of one fold standard deviation** - the largest, *C. tubaeformis* on
lightgbm, moves 0.011 against a fold spread of +/-0.040. A threshold was
registered that is finer than the experiment can resolve. The right
pre-registration would have been in units of fold spread, not absolute AUC, and
that is a lesson for the next ablation rather than a result about this one.

Three things say the residue is noise rather than signal:

1. **The direction is mixed.** *H. repandum* gets **better** without TWI
   (+0.011). A feature carrying real information does not improve a model by
   being removed.
2. **Maxent barely moves.** Seven of nine species shift by 0.003 or less on the
   regularised linear model. Every move past 0.009 but one is lightgbm, which
   has the wider fold spreads to begin with.
3. **It is not where the ecology predicted.** CLAUDE.md expected TWI to "bite
   hard" for *C. tubaeformis*, a species of damp mossy hollows. That species
   moves 0.011 on one model and 0.002 on the other.

One case deserves naming rather than averaging away: ***L. versipelle* moves
past 0.01 on BOTH models in the same direction** (-0.012, -0.011), the only
species where that happens. Rodskrubb is a birch associate and birch does
occupy wet ground, so this may be a real if tiny effect. It is also the weakest
model in the table at 0.574-0.598, so there is little signal there to divide up.

**Conclusion: TWI at 32 m carries nothing this test can separate from noise.**
Either CLAUDE.md's ecological reasoning was wrong, or - more likely - a 32 m
wetness index cannot resolve a hollow a couple of metres across, which for this
project amounts to the same thing.

By the thresholds registered in 18d, 0.012 falls in the **0.01 to 0.03 band,
which was reserved for the team's decision rather than mine.**


### 18g. Decision - TWI is dropped from the national stack

Put to the team on 2026-09-13 with the four options in 18e, the measured worst
case of 0.012, and the note that every delta sits inside a quarter of one fold
standard deviation. **Decision: drop TWI nationally.**

So the national feature stack is elevation, slope, northness, eastness and
curvature - all 3x3 window operations, all tileable, no memory ceiling - plus
climate, SR16, losmasser and AR50 unchanged.

Two consequences, both deliberate:

**The no-TWI Agder run is the comparator for the national run**, not the
headline Phase 0 baseline. That costs nothing, because the ablation already
produced it, and it means Agder and Norway are compared on the same feature
stack instead of on stacks differing by one column. Any national number quoted
against Agder must be quoted against
`data/processed/runs/model_results_no-twi.md`, and it is a mistake to compare it
to `model_results.md`.

**Phase 0's published numbers are untouched.** Sections 7 to 15 keep TWI and
keep their figures. The option of restating them on the no-TWI run was
considered and declined: it would rewrite published results for a difference
that is inside noise, which is a worse trade than carrying one stated
difference between the two designs.

The rejected option worth recording is the per-species one. *L. versipelle* is
the only species where both models move past 0.01 in the same direction, and it
is a birch associate, so keeping TWI for that species alone was tempting. It
was declined because a per-species feature stack means the species are no
longer compared on equal footing - which is the one thing the three-species
design exists to do.


## 19. Does an Agder fruiting window survive being used in Finnmark? - 2026-09-13

A concern raised and then tested, rather than carried as an unexamined risk.

`config/climate.yaml` registers the autumn-rain accumulation window as **day
221-293**, and section 6 derived it from the observed phenology of the three
species' *own records in Agder*. The national model applies that same window
everywhere. Fungi fruit earlier in the north and at altitude, so on the face of
it the climate feature would mean something different in Finnmark than in
Agder - which is the sort of quiet inconsistency that makes a transferability
result meaningless.

Measured on the 31,075 dated records of all eight species, placed by fylke:

| fylke | dated records | p10 | median | p90 | inside the window |
|---|---|---|---|---|---|
| Agder | 2,636 | 215 | **255** | 288 | 80% |
| Vestfold | 2,168 | 208 | 252 | 294 | 74% |
| Oslo | 1,050 | 195 | 241 | 284 | 68% |
| Innlandet | 2,944 | 212 | 242 | 274 | 79% |
| Trondelag | 3,942 | 219 | 247 | 274 | 86% |
| Nordland | 1,126 | 217 | **247** | 266 | 86% |
| Troms | 1,037 | 213 | 241 | 264 | 82% |
| Finnmark | 745 | 217 | **236** | 262 | 88% |

The season does move: the median is 8 days earlier in Nordland than in Agder
and 19 days earlier in Finnmark. **But the window still holds it, and holds it
better than it holds Agder's own.** The northern season is compressed as well
as early - Finnmark's p10-p90 spans 45 days against Agder's 73 - so it sits
comfortably inside a 73-day window, while Agder loses records off both ends.

**So the registered window is kept unchanged for the national run.** Two
reasons beyond the table. Re-deriving it per region would make the climate
feature mean a different thing in each place, which is precisely what a
transferability test cannot tolerate. And re-deriving it nationally would
change a number that was registered before any model existed, for a gain the
data says is not there.

What this does *not* establish is that accumulated autumn rainfall is the right
climate feature, or that 73 days is the right width. It establishes only that
the window does not need to move with latitude. The feature's weakness is
elsewhere and already recorded: permutation importance for `autumn_rain_mm` is
+0.002 to +0.020 across the eight species, small but consistently positive.

### 19a. The national build corroborates it from a second direction

`build_climate.py` accumulates the registered window and the same window
shifted 14 days earlier and later, then correlates the three. Run over the whole
country rather than over Agder:

| window | days | national median | correlation with registered |
|---|---|---|---|
| registered | 221-293 | 207 mm | 1.0000 |
| earlier | 207-279 | 209 mm | 0.9905 |
| later | 235-307 | 210 mm | 0.9969 |

A fortnight either way changes the *spatial pattern* of autumn rainfall across
Norway by almost nothing. That is a second, independent reason not to re-derive
the window per region: even granting that the northern season really is 19 days
earlier, moving the window that far barely changes what the model reads. The
totals shift; the pattern does not, and a model reads the pattern.

The national grid is 1,195 x 1,515 at 1 km, with 606,579 cells carrying data -
33.5% of the rectangle, the rest being sea and territory outside Norway. Median
autumn total is **207 mm against Agder's 342 mm**, and the national maximum is
**1,238 mm against Agder's 783 mm**. Agder is wetter than the country as a whole
and much drier than its wettest part, which is what the west coast should do to
both ends of that range.

One reporting limitation, stated rather than left to be discovered: the
sensitivity table above is only produced on a run that actually downloads. A
rerun against the cached raster writes a shorter report, because the shifted
windows are not re-accumulated. The numbers here come from the download run of
2026-09-13.


## 20. A second losmasser file in the national delivery - 2026-09-13

Found while preparing the national substrate layer. **Read 20a before acting
on anything here**: it retracts the claim that Phase 0's Agder layer has a gap.
What survives is that the national delivery splits its polygons across two
files and both have to be read.

NGU's **Agder** delivery contains one polygon layer,
`LosmasseFlate_20240621`. The **landsdekkende** delivery contains two: that
one, and `LosmasseFlate_20240622` - extracted a day later, same schema, same
CRS, same `objtype`. Nothing in the download names it as a supplement, and the
only thing distinguishing it is the date in the filename.

**The two are spatially disjoint.** Of 2,593 representative points sampled from
`_20240622`, **0.0%** fall inside any `_20240621` polygon. It is not a revision
of the first file. It is extra ground.

| | polygons | area |
|---|---|---|
| `_20240621`, national | 642,775 | - |
| `_20240622`, national | 18,148 | **11,482 km2** |

All of it is on the mainland, and it is not spread evenly:

| fylke | share of the supplement |
|---|---|
| Trondelag | 85.0% |
| **Nordland** | **14.4%** |
| Agder | 0.4% |
| everywhere else | 0.2% |

Its dominant substrate is **peat** - 10,003 of 18,148 polygons - then bare
bedrock, moraine, thin organic cover and marine deposits. Every one maps to a
real group in `src/ingest/losmasser.py`; none is NODATA.

### What missing it would have cost, nationally

**2,944 km2 in the held-out region.** The supplement adds 4.2% more mapped
substrate area to Nordland than `_20240621` alone. Without it the transfer test
would read `losmasse_missing` over ground that is in fact mapped, in the one
region whose entire job is to be a fair test - and the resulting weaker number
would have looked like poor transferability rather than like missing data.

So the national substrate layer reads **both** files, and
`build_vector_layers_national.py` prints the source list and warns loudly if it
finds fewer than two.

### 20a. CORRECTION - Phase 0 has no gap. The packaging differs, not the data.

Written 2026-09-13, a few hours after the section above, and it retracts part
of it.

The original version of this section claimed that Phase 0's Agder substrate
layer was missing 77 polygons and 432 km2, 2.42% of mapped area, because NGU's
Agder extract contains only `_20240621`. **That is wrong**, and it was found by
checking the built raster rather than by reasoning about the filenames.

Every one of the 76 supplement polygons whose representative point falls inside
Agder is **already covered by the Agder delivery's own `_20240621`** - 76 of 76.
The national `_20240621` covers **0 of 76** of them.

| file | covers the 76 supplement polygons in Agder |
|---|---|
| Agder delivery, `_20240621` | **76 of 76** |
| national delivery, `_20240621` | **0 of 76** |

So NGU's **per-fylke extracts are merged** and its **national extract is split
into two batches**. It is the same product packaged differently, not a fylke
extract missing data. `_20240622` is extra ground relative to the national
`_20240621` file, and relative to nothing else.

What survives this correction, and what does not:

- **Still true, and the reason the check mattered:** the national delivery has
  two polygon layers, both must be read, and reading only the first would leave
  11,482 km2 unmapped nationally with 14.4% of it in Nordland. The fix is
  correct and was necessary.
- **Retracted:** Phase 0 has complete substrate coverage for Agder. There is no
  2.42% gap, sections 9a to 15 are unaffected, and the decision not to rerun
  them is now supported by there being nothing to rerun rather than by an
  argument about a layer that does nothing.
- **Retracted:** the section title. A second file is not something "Agder never
  had"; Agder had its contents all along, inside one file.

The lesson is the one this project keeps relearning. Two files with sequential
dates and identical schemas invited an inference about what they meant, and the
disjointness test confirmed the inference *within the national delivery* while
saying nothing about the fylke one. What settled it was sampling the built
raster and asking whether the values were actually there.


## 21. Teaching the pipeline a second extent - 2026-09-13

The national feature stack was finished and validated (section 17), and the
remaining work was described as "nine hardcoded Agder paths in `run_models.py`".
That was wrong, and the thing it missed would have stopped the national run
dead on its first line of real work.

### 21a. The blocker: a whole band does not fit in the machine

`sample_layers` read each band whole, as float64, and then indexed into it. For
Agder that is the right thing and costs nothing worth counting. Nationally:

| raster | cells | one band as float64 |
|---|---|---|
| `terrain_agder_32m.tif` | 0.04 billion | 0.36 GB |
| `sr16_42_SRRVOLMB.tif` (Agder) | 0.18 billion | 1.42 GB |
| `terrain_norge_32m.tif` | **1.83 billion** | **14.67 GB** |
| `ar50_norge_32m.tif` | 1.83 billion | 14.67 GB |
| `sr16_norge/SRRVOLMB.tif` | **9.03 billion** | **72.23 GB** |

The machine has 15.7 GB. The national SR16 mosaic is 90,016 x 100,299 cells.

Nothing about this was visible from the code: `sample_layers` has no size in it,
and the layer list is built from filenames. It appears the moment the first
point is sampled, as a `MemoryError` on a run that has already spent twenty
minutes filtering occurrence records.

### 21b. The fix, and why it is a sampler change rather than a data change

Sampling 67,000 points out of 1.83 billion cells does not need the other 1.829
billion. Above 250 million cells a band is now read block by block: each point
is assigned to the raster's **own** block grid, every occupied block is read
once, and the points that fall in it are served from that read. Memory is one
block, never a band.

Reading all requested bands in a single `ds.read` matters as much as the
windowing. NIBIO ships the national SR16 as LZW **stripes**, one row per block,
pixel interleaved, so pulling one band decompresses the row holding all three
anyway - asking for the three together pays that once instead of three times.

Measured, 2,000 uniformly scattered points, which is a worst case because
scattered points share almost no blocks while real occurrence records cluster
and share many:

| raster | bands | ms per point | per 67,000 points |
|---|---|---|---|
| `terrain_norge_32m.tif` | 5 | 5.0 | 5.6 min |
| `sr16_norge/SRRVOLMB.tif` | 3 | 2.5 | 2.7 min |
| `ar50_norge_32m.tif` | 1 | 0.3 | 0.3 min |

About 31 minutes for a full pass over the national stack. The alternative - 
resampling SR16 from 16 m to the 32 m model grid so the whole thing fits -
would have been faster to run and would have thrown away half the resolution of
the single most informative layer in the stack, to solve a problem that turned
out to cost half an hour.

**The limit sits between the two extents on purpose.** Agder's largest raster is
0.18 billion cells and the smallest national one is 1.83 billion, so the
threshold has an order of magnitude of clearance on both sides and every Phase 0
number keeps coming out of the identical code path. Twenty-one tests sample the
same points both ways, on a tiled raster and a striped one, and demand exact
equality including where the NaNs land. Two sampling implementations that
disagree by a cell would produce a model fitted on slightly different data, and
no AUC could reveal it.

Bilinear stays on the whole-band path and raises above the limit rather than
guessing. The only bilinear layer is the 1 km climate raster - 1,195 x 1,515
nationally - so the error cannot fire, but a blockwise bilinear needs the four
neighbours of a point and two of them can sit in the next block, which is the
kind of edge case that returns a plausible number rather than an error.

### 21c. Sampling the same 52,249 points nine times

`build` is called once per species and every call passes the same background.
Nationally that is 52,249 target-group points against nine presence sets, so
uncached it is nine passes over roughly 40 GB of raster to obtain one pass worth
of numbers - about four and a half hours of pure I/O, for nothing.

Rows are now cached by coordinate. The null control shares the pool with the
background, so its rows come back free as well. The cache is **not** used by the
Agder run: it cannot change a value, and there is a test file that says so, but
Phase 0's published numbers have no reason to depend on a mechanism introduced
for a different extent, and the Agder run takes minutes either way.

### 21d. The extent table, and one bug it exposed

Which rasters, which background pool, which boundary, which output file: all of
it now comes off one `Extent` record rather than being decided in nine places.
The point is not tidiness. **A national run cannot land on `model_results.md`**,
because the extent picks the filename and there is no argument combination that
changes it. This project has now made that exact mistake three times - a
download, a climate report and a tagged model run - and each time the
overwritten file looked entirely normal afterwards.

Writing the same guard into `run_transfer.py` found the bug still live there:
it wrote `transferability.md` **unconditionally**, so `--tag no-vol-bounds`
replaced the baseline with the variant being compared against it. That is the
identical failure `run_models.py` carries a paragraph about, in the script that
imports `run_models`. Tagged transfer runs now write only to `runs/`.

### 21e. Two extents that put the held-out region on opposite sides

Kristiansand is **inside** Agder. Nordland is **outside** the national training
extent by construction. So the transfer test cannot use one rule:

| | draw presences from | held out |
|---|---|---|
| Phase 0 | Agder | Kristiansand, a subset of it |
| national | 14 fylker **+ Nordland** | Nordland |

Using the training extent as the draw region nationally would pull zero
Nordland presences and the test would report confidently on nothing. Using the
union for blocked CV would put the held-out region into fitting. Those are
different regions built by different functions, and the test that matters
asserts both directions at once: a Nordland point is in the transfer draw
region and is **not** in the cross-validation region.

Cross-check on the new code path: `run_models.py --national` finds 2,256
thinned *C. tubaeformis* presences in the fourteen training fylker, which is
exactly what `check_holdout_power.py` counted independently in section 16b.

### 21f. SR16 covers the held-out region - checked before the run, not after

SR16 maps productive forest, Nordland is mountainous, and a held-out region
where the forest layer is mostly absent would produce a transfer test measuring
the missing-data indicator rather than the forest. Share of thinned presence
points with no SR16 value:

| layer | Agder | 14 training fylker | Nordland |
|---|---|---|---|
| TRESLAG | 15.9% | 16.8% | **18.8%** |
| VOLMB | 15.4% | 16.7% | **19.3%** |
| MHOYDE | 15.4% | 16.7% | **19.3%** |

Three points worse than Agder, not a cliff. Recorded because the result is
negative and would never have been looked for afterwards - a weak Nordland
number would have been attributed to transfer distance, which is exactly the
conclusion the national design exists to test.

### 21g. The Agder path reruns to the same 1,590 numbers

A refactor that silently changes what a published number was computed from is
indistinguishable, from the outside, from a refactor that did not. So the Agder
run was repeated on the refactored code and diffed against
`model_results.md` as committed.

**Every one of the 1,590 numbers is identical** - AUC, both Boyce columns, all
standard deviations, every permutation-importance mean and spread, every
dropped-row count. The only textual differences are the date line, the new
`**Extent:**` line, and the deliberate `../boyce_diagnosis.md` link rewrite
that tagged runs apply on the way into `runs/`.

The order in which the pipeline was made safe matters as much as the result.
Fifteen tests pinned the Agder feature stack **before** anything was changed,
and the first version of that pin was itself wrong: it did not know that VOLMB
appears twice in the stack, once as the estimate and once carrying its own
bounds. A pin written after the refactor would have been written against
whatever the refactor produced.

The diff turned up one further thing, unrelated and small. The two
ordering-verdict lines were emitted by iterating a `set` of model names, and
Python randomises string hashing per process, so two runs of identical code
produced files that did not compare equal. Harmless to any conclusion, and
exactly the kind of noise that stops people diffing results at all. Now sorted;
`run_transfer.py` already was.

## 22. The national run, and what the ordering test can and cannot carry - 2026-09-14

Fitted on fourteen fylker, cross-validated in 7,500 m blocks with a 4,000 m
buffer, and evaluated once on Nordland, which no model saw. Every prediction
below was registered in section 16e before the national feature stack existed.

Run with:

    python scripts/run_models.py --national
    python scripts/run_transfer.py --national

### 22a. Both gates cleared before anything else was read

CLAUDE.md hard constraint 5: if the synthetic null scores meaningfully above
0.5, the background sampling is broken and nothing else in the run means
anything. The national background is a new artefact built by a different
script from Agder's, so it gets checked fresh rather than inherited.

| | null, lightgbm | null, maxent |
|---|---|---|
| national blocked CV | **0.490 +/- 0.009** | 0.492 +/- 0.007 |
| transfer to Nordland | **0.514 [0.480-0.552]** | 0.475 [0.438-0.513] |
| *(Agder, for reference)* | 0.471 +/- 0.033 | 0.496 |

Both sit on 0.5, and the blocked-CV interval is four times tighter than
Agder's because the background is thirteen times larger. Nothing below is
readable without this, which is why it is first.

### 22b. Blocked CV: the registered ordering passes, on both models

Registered before any model existed: *C. tubaeformis* best, *C. cibarius*
middle, *A. muscaria* worst.

| species | Agder | national | gain |
|---|---|---|---|
| *Craterellus tubaeformis* | 0.645 | **0.786 +/- 0.009** | +0.141 |
| *Cantharellus cibarius* | 0.685 | **0.743 +/- 0.006** | +0.058 |
| *Amanita muscaria* | 0.664 | **0.688 +/- 0.007** | +0.024 |
| *Craterellus cornucopioides* | 0.757 | 0.828 +/- 0.024 | +0.071 |
| *Hydnum repandum* | 0.605 | 0.733 +/- 0.014 | +0.128 |
| *Suillus luteus* | 0.699 | 0.722 +/- 0.025 | +0.023 |
| *Boletus edulis* | 0.660 | 0.717 +/- 0.016 | +0.057 |
| *Leccinum versipelle* | 0.598 | 0.684 +/- 0.018 | +0.086 |

lightgbm, blocked CV. Agder's lightgbm **failed** the ordering - it put
*tubaeformis* last. Nationally both models pass it with the same order, and
the three separate by six or more fold-to-fold standard deviations.

Prediction 16e.2 said national AUC would come out higher by roughly 0.05 to
0.15 and that **this would not mean the model got better**, because a wider
environmental gradient makes the same discrimination easier. Median gain
+0.07, well inside the registered band. That part of the prediction held and
the caveat attached to it still stands.

### 22c. Transfer to Nordland: it passes there too

The harder test, and the one Phase 0 failed.

| species | Agder to Kristiansand | national to Nordland | n held out |
|---|---|---|---|
| *Craterellus tubaeformis* | 0.600 [0.519-0.679] | **0.808 [0.744-0.861]** | 46 |
| *Cantharellus cibarius* | 0.653 [0.573-0.735] | **0.714 [0.679-0.749]** | 160 |
| *Amanita muscaria* | 0.638 [0.572-0.703] | **0.700 [0.656-0.744]** | 126 |
| *Boletus edulis* | 0.581 [0.501-0.665] | 0.727 [0.679-0.768] | 114 |
| *Hydnum repandum* | 0.384 [0.152-0.689] | 0.758 [0.692-0.823] | 29 |
| *Suillus luteus* | 0.652 [0.543-0.758] | 0.743 [0.687-0.796] | 56 |
| *Leccinum versipelle* | 0.686 [0.529-0.814] | 0.682 [0.642-0.721] | 150 |
| *Craterellus cornucopioides* | 0.648 [0.511-0.777] | 0.767 [0.585-0.917] | **7** |

| ordering on transfer | lightgbm | maxent |
|---|---|---|
| Agder to Kristiansand | cibarius > muscaria > tubaeformis - **FAIL** | same - **FAIL** |
| national to Nordland | tubaeformis > cibarius > muscaria - **PASS** | same - **PASS** |

*C. tubaeformis*'s two intervals **do not overlap at all**: [0.519-0.679]
against [0.744-0.861]. Within the national run it clears *C. cibarius* with
a 0.005 sliver of overlap. The species the whole three-species design hinges
on went from last place to separated at the top.

*H. repandum* is worth noting on its own. It was scoring **below chance** on
Kristiansand at 0.384, which section 9c could only report as uninterpretable
at n=45. At n=29 in Nordland it reads 0.758 [0.692-0.823].

*C. cornucopioides* has 7 held-out records and an interval spanning
[0.585-0.917]. Reported, not quoted as a measurement, exactly as registered
in 16c.

### 22d. A registered prediction that was wrong, in the helpful direction

Section 16d registered that *C. tubaeformis* is the one species the national
test would resolve **no better** than the Kristiansand test that had already
failed to resolve it - 46 Nordland records against 53 in Kristiansand, so a
slightly *wider* interval.

The Nordland interval came out **narrower**: 0.117 wide against 0.160.

The prediction reasoned from held-out presence count alone. Bootstrap AUC
interval width also depends on the number of held-out background points -
3,301 in Nordland against a few hundred in Kristiansand - and on how far the
AUC sits from 0.5, since the statistic is bounded at 1. Both worked in our
favour. Recorded as a miss rather than quietly dropped, because a
registration that is only remembered when it comes out right is not a
registration.

### 22e. Looking for the leak: latitude, tested and rejected

The gains are not uniform. *C. tubaeformis* gained +0.141 and *A. muscaria*
+0.024, and that differential is exactly what flips the ordering - so the
whole result turns on it.

The obvious worry is that the model learned **latitude**. *C. tubaeformis* is
southern-skewed (2.0% of its records in Nordland, 0.47x recording effort);
*A. muscaria* is evenly spread (0.93x). A model that has learned "southern
Norway" would score well on the first and badly on the second, producing this
pattern with no ecology in it at all.

Tested against the effort-relative shares measured in 16c:

| | Spearman vs the AUC gain |
|---|---|
| northern-ness (share vs effort) | **-0.048** |
| Agder presence count | -0.357 |
| national/Agder data multiplier | -0.333 |
| starting Agder AUC | -0.595 |

**The latitude hypothesis is dead.** If the national model had learned a
latitudinal gradient, the gain would be concentrated in the southern
specialists; it is uncorrelated with them. The -0.595 on starting AUC is
regression to the mean and is not significant at n=8, where Spearman needs
about 0.74.

Worth recording separately: northern-ness correlates with *national* AUC at
-0.714 and with *Agder* AUC at -0.595. Range-restricted species really are
easier to predict - but that was already true inside Agder, so it is not
something going national introduced.

### 22f. What appeared to move: autumn rainfall

**Read section 22h before acting on this one.** The correlation below is real
and is reported as measured. The causal reading it invites - that rainfall is
what carried the national result - is wrong, and the ablation in 22h retracts
it. The subsection is kept rather than deleted because the sequence is the
point: this is what the evidence looked like before the check that overturned
it, and it looked convincing.

| | Spearman vs the AUC gain |
|---|---|
| gain in **autumn-rainfall** importance | **+0.826** (n=8, p ~ 0.011) |
| gain in elevation importance | +0.024 |

*C. tubaeformis*'s rainfall importance went from **-0.004 in Agder to +0.069
nationally**, from nothing at all to its single largest feature. Agder's
autumn rainfall spans 195-783 mm; Norway's spans 105-1,238 mm. The species
CLAUDE.md describes as "moist mossy spruce forest, damp hollows" had no
moisture gradient to read in Agder and has one now.

Elevation stayed the top feature overall and explains none of the movement
(+0.024). The FINDINGS 15 worry that elevation carries accessibility is
neither helped nor hurt by the national run.

### 22g. This reopens a question that had been closed

Section 6 registered, before the climate feature was ever fitted, that autumn
rainfall follows the same coast-inland and orographic gradient as the
population, and so could act as an accessibility proxy - the exact failure the
target-group background exists to prevent. The open-questions list closed it:

> ~~Does the autumn-rainfall feature carry ecology or accessibility?~~ Largely
> moot: its permutation importance is between -0.007 and +0.039 across every
> species and both models in every run.

**That sentence is now false**, and it is false about the feature carrying the
headline result. The national run puts it at +0.069.

Neither existing check can settle it. The null cannot: null presences and
background are drawn from the same pool, so they are environmentally identical
by construction and no feature can separate them - the report says so already.
Permutation importance cannot: it establishes that a feature is used, not what
it is a proxy for. Only deleting the feature can.

### 22h. The ablation: rainfall is a marker, not a cause

Refit the whole national stack without the climate layer, on both harnesses.
The contrast between them is the informative part: if rainfall carried real
habitat information, deleting it should hurt the moisture-dependent species
wherever you stand; if it were a coast-inland accessibility proxy, it should
help within a region and do nothing across one.

    python scripts/run_models.py   --national --no-climate
    python scripts/run_transfer.py --national --no-climate

| species | blocked CV | -rain | cost | transfer | -rain | cost |
|---|---|---|---|---|---|---|
| *Craterellus tubaeformis* | 0.786 | 0.773 | **-0.013** | 0.808 | 0.791 | -0.017 |
| *Cantharellus cibarius* | 0.743 | 0.733 | -0.010 | 0.714 | 0.698 | -0.016 |
| *Amanita muscaria* | 0.688 | 0.689 | +0.001 | 0.700 | 0.707 | +0.007 |
| *Boletus edulis* | 0.717 | 0.719 | +0.002 | 0.727 | 0.715 | -0.012 |
| *Craterellus cornucopioides* | 0.828 | 0.812 | -0.016 | 0.767 | 0.849 | +0.082 |
| *Leccinum versipelle* | 0.684 | 0.677 | -0.007 | 0.682 | 0.692 | +0.010 |
| *Suillus luteus* | 0.722 | 0.714 | -0.008 | 0.743 | 0.716 | -0.027 |
| *Hydnum repandum* | 0.733 | 0.721 | -0.012 | 0.758 | 0.733 | -0.025 |
| null control | 0.490 | 0.492 | | 0.514 | 0.500 | |

lightgbm. Mean cost **-0.008** on blocked CV and **+0.000** on transfer.
*C. cornucopioides*'s +0.082 is n=7 and means nothing.

**Section 22f's causal reading is retracted here.** Autumn rainfall's
permutation importance really did rise from -0.004 to +0.069 for
*C. tubaeformis*, and that rise really does track the AUC gain at +0.826
across the eight species. But deleting the feature costs 0.013 of a +0.141
gain - about a tenth - and the model recovers nearly everything it was reading
off rainfall from the rest of the stack.

This is the trap `run_models.py` already carries a comment about for the volume
bounds: *permutation importance under correlated features assigns credit
arbitrarily*. A feature can be the one the algorithm leans on while carrying
almost no information unavailable elsewhere. Importance measured what the model
**used**; the ablation measured what it **needed**, and only the second is a
claim about the data.

### 22i. The ablation also sharpens what the ordering test actually established

Without rainfall the registered ordering splits:

| transfer to Nordland, no climate layer | |
|---|---|
| maxent | tubaeformis 0.825 > cibarius 0.705 > muscaria 0.695 - **PASS** |
| lightgbm | tubaeformis 0.791 > **muscaria 0.707 > cibarius 0.698** - **FAIL** |

Read the numbers rather than the verdict. What flipped is **second against
third**, by 0.009, between two species whose bootstrap intervals overlap almost
entirely. What did not flip, on either model or either harness, is first place:

| *C. tubaeformis* | with rainfall | without |
|---|---|---|
| blocked CV | 0.786 | 0.773 |
| transfer, lightgbm | 0.808 [0.744-0.861] | 0.791 [0.730-0.847] |
| transfer, maxent | 0.827 [0.767-0.880] | 0.825 [0.764-0.877] |

So the honest decomposition of the national ordering result is two claims of
very different strength, and they should not be quoted as one:

* **Strong, and robust to the ablation.** *C. tubaeformis* ranks first, on both
  models, on both harnesses, with and without the suspect feature, separated
  from second place by more than its own interval. Under blocked CV the three
  separate by six or more fold-to-fold standard deviations (0.786, 0.743,
  0.688 at +/- 0.006 to 0.009).
* **Weak, and it was always weak.** *C. cibarius* above *A. muscaria* on the
  transfer. Even in the full run those were 0.714 [0.679-0.749] against 0.700
  [0.656-0.744] - overlapping across nearly their whole range. The strict
  three-way PASS on transfer rests on that 0.014, and removing one feature is
  enough to reverse it.

Section 22c reported the transfer PASS without flagging how little separated
second from third. That is corrected here: the PASS verdicts stand as recorded,
and the part of them that is load-bearing is first place.

### 22j. What that settles, and what it leaves open

**Settled, and more cleanly than if the feature had mattered.** Section 6 asked
whether autumn rainfall carries ecology or accessibility, and the
open-questions list had closed it on Agder evidence that section 22g showed to
be out of date. It is now closed on better evidence: *no load-bearing
conclusion in the national run depends on that feature*. Delete the single
variable this project ever flagged as a possible accessibility proxy and both
null controls, the blocked-CV ordering, and *C. tubaeformis*'s first place all
survive.

**Still open: why the gain is so uneven.** *C. tubaeformis* gained +0.141 going
national and *A. muscaria* +0.024. Five candidates tested, none survives:

| candidate | Spearman vs the gain | verdict |
|---|---|---|
| latitude / range restriction | -0.048 | dead |
| Agder presence count | -0.357 | not significant at n=8 |
| national/Agder data multiplier | -0.333 | not significant at n=8 |
| starting Agder AUC | -0.595 | not significant at n=8 |
| autumn rainfall | +0.826 correlation | **cause excluded by ablation** |

At n=8 Spearman needs about 0.74 for p < 0.05, so only rainfall ever cleared
the bar, and the ablation removed it as a cause.

The honest statement is that the gain is **unexplained by any single feature**,
which is what a broad environmental-gradient effect should look like: Norway
offers wider ranges in elevation, climate, forest type and substrate at once,
several of them correlated, so nothing shows up as load-bearing and deleting
any one of them changes little. That is consistent with prediction 16e.2 - the
gain is largely an extent effect and does not mean the model got better - while
leaving the differential across species unaccounted for.

It would be easy to write "the moisture-dependent species benefited most from a
moisture gradient". Every number in section 22f fits it. The ablation is the
reason not to.

### 22k. What this does to section 9c, and what it deliberately does not

Section 9c recorded the registered ordering **failing on both models** on the
Agder-to-Kristiansand transfer, with *C. tubaeformis* last, and offered two
readings of it side by side:

> 1. **The intervals overlap almost completely.** [...] With 45-53 held-out
>    presences per species this test cannot resolve an ordering [...]
> 2. **It is a genuine reversal, not a shrinkage.** [...] a reordering suggests
>    the blocked-CV ranking was reading something local to the Agder-wide split.

Those are opposite explanations - underpowered, or genuinely wrong - and
section 9c could not choose between them. The national run is the test that
can, because it is the same question asked with roughly ten times the training
data and a held-out region that shares no border, no fylke administration and
no recording community with the training extent.

**The answer is (1).** With 2,256 rather than 246 training presences, the
registered ordering reproduces on all four national runs - blocked CV and
transfer, maxent and lightgbm - and *C. tubaeformis* moves from last to first
with intervals that no longer overlap its own Phase 0 result
([0.519-0.679] against [0.744-0.861]).

**What does not change, and must not be presented as though it had:**

- **Phase 0's published numbers stand exactly as recorded.** Section 9c's FAIL
  is a fact about a model fitted on Agder and evaluated on Kristiansand. It is
  not retracted, not amended, and not softened. `model_results.md` and
  `transferability.md` are untouched by anything in section 22, which is why
  the extent picks the output filename.
- **The Phase 0 artefact-2 verdict stays FAIL.** The pass/fail on the predicted
  ordering was registered against the Phase 0 design, judged there, and
  recorded there. Re-judging it on a later, larger experiment would be exactly
  the after-the-fact rationalisation that registering predictions exists to
  prevent. Two experiments, two verdicts, both reported.
- **The national result was not predicted to come out this way.** Section 16e
  registered that national AUC would rise for extent reasons and that this
  would not mean the model improved. It said nothing about the ordering
  flipping, and 16d explicitly registered that *C. tubaeformis* would be the
  species the national test could **not** resolve. It resolved it. That is a
  registration missed in the favourable direction and it is recorded as one in
  section 22d.

What can now be said, which could not be said before: the three-species
falsification instrument has been run twice, at two scales, and the scale that
had enough data to resolve it returned the registered ordering. The instrument
was built to catch the failure mode where all three species score alike because
the model is reading recording effort. At national scale they do not score
alike - 0.786, 0.743, 0.688, separated by six or more fold-to-fold standard
deviations, with a null sitting on 0.490 and the one feature ever flagged as an
effort proxy ablated away at a cost of 0.013.

That is the strongest evidence this project has produced that the map is
reading habitat rather than footfall. It is not proof, it rests on
presence-only data with no absences anywhere in it, and CLAUDE.md's standing
requirement holds unchanged: say plainly, wherever the model is described, that
it is an estimate from public geodata and has never been validated against
anybody's actual walks.

## 23. The national suitability surfaces - 2026-09-14

Nine GeoTIFFs at 32 m covering mainland Norway, eight species plus the null,
from the model `transferability_norge.md` reports on. Built with
`scripts/build_maps_national.py`; the table of files is in
[suitability_maps_norge.md](data/processed/suitability_maps_norge.md).

| | |
|---|---|
| grid | 38,173 x 48,022 at 32 m, EPSG:25833, taken from `terrain_norge_32m.tif` |
| cells scored | **316,426,557** per species - 99.7% of mapped land |
| unscored inside Norway | 912,020 (0.3%), a layer with no value at that cell |
| size | about 1.13 GB each, 10.2 GB in total |
| fitted on | fourteen fylker, minus a 4 km buffer around Nordland |

Two choices were put to the team and taken: **32 m rather than 16**, because
the national stack is 32 m natively and the app quantises to 128 m for display,
so 32 m is already four times finer than anything reaches a screen; and **one
model fitted on fourteen fylker, drawn over fifteen**, so the AUC quoted in
section 22 describes the surface actually shipped rather than a sibling of it.

### 23a. 145,152 km2 of sea nearly got scored as mushroom habitat

Kartverket publishes fylke areas out to the territorial baseline rather than to
the coastline. A fylke polygon therefore contains open water - 20 km south of
Kristiansand is "inside Agder" by that test - and the first national mask,
built from the fylke outlines alone, held **145,152 km2** of it.

Phase 0 never met this problem. Its Agder DTM was sea-masked, so every sea cell
carried a non-finite feature and was dropped for free.
`build_terrain_national.py` deliberately dropped that mask when TWI was
dropped, and stated in its own docstring that *prediction is masked to land
regardless* - a specification for a step that had not been written yet.

Sea is now cut using AR50's explicit sea class, which keeps an inland lake at
sea level as land, exactly as the Agder flood fill did. The result is a useful
check on the whole chain:

| | |
|---|---|
| mask area | **324,955 km2** |
| Norway's mainland land area | 323,808 km2 |
| difference | **0.35%** |

Nothing in the pipeline was tuned to produce that. It comes out of fifteen
Kartverket polygons, an NGU-derived land cover class and a 32 m grid agreeing
with a published figure none of them knew about.

### 23b. The maps reproduce ecology nobody encoded

The feature stack contains no latitude, no longitude and no "forest region"
variable. It has terrain, climate, SR16 forest structure, substrate and land
cover. What comes out:

* ***C. tubaeformis*** is dark through Ostlandet, the inner fjord valleys and
  the Trondelag lowlands, and pale across Hardangervidda, Jotunheimen and
  Finnmarksvidda. That is Norway's productive spruce forest against everything
  above the forest line.
* ***C. cornucopioides*** concentrates hard in the far south - Oslofjord,
  Vestfold, Telemark, southern Agder - and goes pale across the entire north.
  Svart trompetsopp is a southern calcareous-deciduous species that barely
  reaches Nordland, at 0.24x recording effort (section 16c).

The two maps differ in the direction the ecology says they should, from the
same stack and the same pipeline.

### 23c. The null map is a third, independent check - and it is the one a beginner can see

Sections 22a and 22c report the null's AUC: it cannot rank its own presences
above background, blocked or on transfer. That is the statistical form of the
check. The map is the visible form, and it asks a slightly different question:
not "can the model rank" but "does the surface have structure in it".

Measured as the ratio of the 98th to the 2nd percentile of each surface, over
land, from the same rasters the app will read:

| species | p2 | p98 | dynamic range | CV |
|---|---|---|---|---|
| *Craterellus cornucopioides* | 0.00000 | 0.02799 | **enormous** | 1.63 |
| *Craterellus tubaeformis* | 0.00011 | 0.09258 | **814x** | 1.38 |
| *Cantharellus cibarius* | 0.00028 | 0.12171 | 438x | 1.07 |
| *Hydnum repandum* | 0.00021 | 0.02832 | 133x | 1.29 |
| *Boletus edulis* | 0.00152 | 0.08064 | 53x | 0.95 |
| *Suillus luteus* | 0.00341 | 0.06689 | 20x | 0.76 |
| *Amanita muscaria* | 0.00598 | 0.09937 | 17x | 0.67 |
| *Leccinum versipelle* | 0.01263 | 0.09612 | 8x | 0.49 |
| **synthetic null** | 0.04902 | 0.06865 | **1.4x** | **0.10** |

The null surface is **flat**: its brightest ground is 1.4 times its dimmest,
against 8x to 800x for every real species, and its coefficient of variation is
five to sixteen times lower than any of them. A model with no ecology in it
produces a map with no pattern in it, which is what should happen and had never
been shown at map level before.

The ordering of the real species is worth reading too, because it was not
arranged. The two most habitat-restricted species produce the most structured
maps. *A. muscaria* - registered in CLAUDE.md as "conspicuous, photogenic,
heavily over-recorded, not strongly habitat-restricted" and predicted worst of
the three - sits second from bottom at 17x. *L. versipelle*, a birch associate
that grows wherever birch does, is the flattest real species at 8x. The maps
rank by habitat specificity without being asked to.

This is not a fourth validation of the same thing. AUC asks whether presences
rank above background; dynamic range asks whether the surface says anything at
all. A map could in principle score well on AUC and still be nearly flat, and
that map would be useless to a forager while looking fine in a table.

### 23d. Two bugs, both mine, and what they actually cost

Recorded because the failure modes are more interesting than the fix.

**A quadratic buffer exclusion.** The national script inlined a call that
`build_maps.py` hoists:

    tr = [i for i in tr if i not in _excluded_by_buffer(pts, tr, te, ...)]

which re-runs the whole exclusion for every one of 52,000 rows. The call is
deterministic, so the answer was identical and the maps already produced were
correct. It simply went quadratic: *A. muscaria* sat at 100% CPU with zero disk
I/O for four and a half hours before the silent log was looked at. **The right
answer arriving too slowly is a failure no assertion on the result can catch**,
so the test that now guards it counts the calls rather than checking the output.

**A one-character read bug that cost a completed prediction.** `read(band, ...)`
with a scalar index already returns 2-D; the preview took `[0]` of it, which is
the raster's top row - a 1,192-cell sliver of Arctic sea, entirely nodata - and
so reported the whole national surface as empty. The GeoTIFF was correct the
whole time. It cost 36 minutes because the check fired *after* the raster was
written and the script exited before recording anything. Both halves are fixed:
statistics are written before the preview is drawn, and a species whose GeoTIFF
and sidecar both exist is skipped on the next run.

The same `[0]` had already produced two wrong diagnostics earlier the same day,
including a "2.7% of Norway is land" reading that sent me looking for a mask bug
that did not exist.

### 23e. A measurement lesson: watching the job changed the job

Six of the nine species ran in 33 to 38 minutes. Three did not: 99, 112 and 130
minutes. The first was the quadratic bug. **The other two were me** - running
the test suite repeatedly, opening 1.1 GB rasters and redrawing previews while
the build was competing for the same cores and the same file cache.

That matters because it was used as evidence. When the first estimate came in
high, the smoke test's 5.3-hour figure was written off as having measured a warm
cache. It had not: at 35 minutes a species the honest total is 5.3 hours, which
is exactly what it predicted. The estimate was sound and the monitoring
invalidated it.

## 24. Delivering the national map, and making the page say true things - 2026-09-14

EXPANSION section 5 named the delivery format as the hard stop and named the
replacement: zone data fetched per region rather than shipped whole. This is
that. The plumbing turned out to be the smaller half.

Built with:

    python scripts/export_zone_tiles.py
    python scripts/build_hosted.py --national

### 24a. The window, and the one thing that could not become window-relative

`GW`, `GH`, `OX` and `OY` stop meaning "the study area" and start meaning "the
window currently loaded". Forty-odd places in `shell.html` read them and none
needed rewriting, because each only ever asked about *the current grid*. Two
things did:

* **A traced zone path belongs to the window it was traced in.** Drawn against
  the current origin after a pan, a whole landscape of contours is offset by
  exactly how far the window travelled. The geometry now carries its own
  origin. This is the failure mode worth naming: the map renders perfectly,
  every curve closes, and the good ground is in the wrong wood.
* **A cell index is an offset into the window.** The card remembers where it is
  in metres and finds its cell again after a move.

The thing that must *not* follow the window is the ranking. "The top 5 %" has
to mean the top 5 % of Norway wherever the user is standing, or the same wood
drifts in and out of a zone as they pan and nothing looks wrong. Two shipped
artefacts hold that: the tile byte is a **national percentile** rather than a
linearly scaled score, and the post-smoothing re-rank is a **shipped 256-entry
CDF** rather than a sort of what is loaded.

Measured, in `tests/test_zone_window.py`, against the same ground seen from two
different windows through the interface's own code:

| | |
|---|---|
| window rank vs. whole-country rank | within **0.02** |
| same ground, two different windows | identical to **1e-6** |

The second is the user-visible property. The first is bounded by the halo: the
blur is two separable 3-tap passes, so it reaches two cells, and a window
carrying two cells of margin smooths its interior exactly as the national
surface was smoothed.

### 24b. The conditions rank is the one approximation, and it is stated

The habitat lookup is exact - computed over the whole country at build time.
The *conditions-adjusted* rank cannot be, because the weather is not known when
the tiles are cut, and ranking it against the loaded window would put "the top
5 %" straight back inside the viewport.

So it is derived in the app from the conditions grid itself. The habitat rank
H is uniform on [0,1] by construction and the multiplier m is read off that
grid, so

    P(H*m <= s) = mean over the grid of min(1, s/m)

which is one sort and a running sum. **The assumption is that H and m are
independent nationally**, and they are not quite - wetter regions hold somewhat
better habitat. The error is a smooth national bias of a few percent, not
something that moves when the user pans, and it sits on a term whose weight is
a stated judgement rather than a fitted estimate. Recorded here because the
distinction between the exact lookup and the approximate one is exactly the
sort of thing that gets forgotten and then quoted as if it were measured.

A uniform conditions field must leave the ranking untouched - a constant
multiplier cannot reorder anything - and that is pinned as a test rather than
argued: if it ever drifts, the layer is quietly rescaling the map on a still
day.

### 24c. Tiles are read as data, not drawn as pictures

The obvious decoder is an `Image` element and `getImageData`. That hands the
bytes to a canvas, and a canvas is entitled to colour-manage what it is given.
For a value that *is* a national percentile, a browser nudging 176 to 175
somewhere inside a colour transform would be invisible and would move zone
boundaries.

So the PNG is parsed in the app: the encoder writes 8-bit greyscale with filter
0 on every scanline, which makes the inflated payload the samples themselves,
and `DecompressionStream` is the platform's own zlib. No dependency, no
hand-written inflate, and a filtered scanline throws rather than being guessed
at - a corrupt tile drops out as nodata instead of leaking a landscape of
plausible wrong numbers.

### 24d. What the tests caught that review did not

Four defects, all of them in code I had just written and read:

1. **The coverage note depended on the markup, not on the code.** The memo
   guarded the whole call, so the first one - almost always "nothing to say" -
   matched the initial state and returned without touching the element. It
   worked only because the element carries `hidden` in the HTML.
2. **The build opened on the Norwegian Sea.** The opening view was the centre
   of the tile index's bounding box, and for a country shaped like Norway the
   centre of any box drawn around it is water. The first screen had no zones on
   it, no explanation, and nothing wrong-looking about it. It now opens on the
   median occupied tile.
3. **The shell's own `<title>` landed in the body of every hosted page**,
   naming one kommune on a map of the country. Invisible because
   `document.title` returns the first title in tree order.
4. **The counter-test in `test_national_rank.py` did not bite** - covered in
   section 23, and the same lesson: a fixture that is uniform random makes
   every window a representative sample, so local ranking was right by
   accident. Norway is not uniform, which is the entire reason the shipped
   lookup exists.

Three mutations confirm the suite is load-bearing: bypassing the national
lookup, an off-by-one in the window assembler, and keeping stale geometry
across a window move each fail it.

### 24e. What it costs, and what it buys

| | |
|---|---|
| page | 0.22 MB |
| zone tiles on the host | 141.6 MB, 3,640 files, 38.9 kB each |
| tiles fetched at launch | 9, about 350 kB |
| tiles fetched per window move | typically 4 |
| ground per window at walking zoom | about 560 km2 |

The page no longer grows with area. Against the artifact build's 14.77 MB for
45 x 64 km, this is the whole mainland for a fiftieth of the download, and the
service worker keeps every tile already looked at so the map works with no
signal over ground already walked.

**The zones stop above 600,000 cells - about 9,800 km2** - and the map says so
rather than drawing a smear. At that scale a 128 m cell is a fraction of a
pixel. Coarser overview levels are the proper answer and are not built; the
exporter would need one CDF per level, because the same blur over a coarser
grid covers different ground and the shipped lookup would otherwise be read
against a distribution that was never computed from it.

### 24f. The page had to be rewritten, not just re-pointed

This is the half that was not plumbing. A tiled build that renders perfectly
while the Om page describes a model fitted on Agder and tested on Kristiansand
is worse than one that does not build: the map is right, the account of it is
wrong, and nothing on screen looks amiss.

Every sentence that names a region or quotes a number is now addressable by id
and replaced from the payload, with the numbers read back out of the run
reports rather than typed - 39 features, fourteen fylker, 2,302 records,
1,234,631 in the target-group pool. A layout change in a report stops the build
instead of shipping a paragraph it cannot substantiate, which is the same rule
`src/report_tables.py` already applied to the AUC table, extended to the prose.

A scan in `tests/test_hosted_national.py` walks the markup, finds every mention
of a region, and requires it to sit inside an element the build overrides, so a
new region-specific sentence without an id fails there rather than reaching a
reader. Two elements are exceptions and both are pinned rather than assumed:
the coverage note, which a tiled build cannot reach, and the pill, which is
overwritten at boot from the payload's own area label.

Three things the national page says that the Kristiansand one does not:

* **Only Nordland is out of sample.** The other fourteen fylker are terrain the
  model was fitted on. That is a weaker claim than Phase 0's Kristiansand map,
  which was held out over its whole extent, and the page says so rather than
  letting the transfer AUC imply otherwise.
* **The registered ordering held.** It failed on Agder and passed nationally;
  both results stand, and so does the caveat from section 22b that the AUC gain
  is an extent effect rather than a better model.
* **There is no fresh rainfall layer.** seNorge is fetched for Agder, and
  Agder's weather laid over a map of Norway would be wrong everywhere else, so
  the build ships no conditions file and the page says the layer is absent.

The pre-registered ship-or-drop bar - a species ships only if the lower bound
of its transfer interval clears the synthetic null - is applied to the national
numbers rather than re-decided for them. All eight clear it, **blek piggsopp
included**, which the Kristiansand build drops. That is the rule working in
both directions rather than only against.

### 24g. What is still missing from the national app

Three gaps, none of them hidden from the user by the interface:

1. **No overview levels.** Zoom out past a region and the zones vanish with a
   line of text. A pyramid is cheap in tiles (+33 %) and needs one CDF per
   level to keep the zone thresholds meaning the same thing across zooms.
2. **No national conditions.** The weather fetch covers Agder. Going national
   means seNorge over a 1,215 x 1,525 km grid, daily, plus the 1991-2020
   climatology to compare it against.
3. **No national context layers.** Tapping a cell in Kristiansand reports the
   terrain, forest and substrate behind the score; nationally it reports only
   the score, because 39 national feature rasters at 32 m is another delivery
   problem of its own. The card degrades rather than lying.

## 25. Closing the three gaps the national app shipped with - 2026-09-14

Section 24e listed what the tiled build did not do: no overview levels, no
national weather, no context layers behind a tap. All three are now closed.
The weather one turned up a data bug that predates the national work.

### 25a. Sea counted as a year with no rain

`senorge.seasonal_total` has always masked a cell that is fill on every day of
a window - its comment says why, in as many words: *zero rainfall is a claim,
and a false one*. The climatology loop in `fetch_recent_weather.py` used a bare
`np.nansum` and did not. So every cell outside Norway summed to 0.0 and entered
the 30-year baseline as a year with no rain.

The error scales with how much of the window is sea, which is exactly why it
survived Phase 0:

| | before | after |
|---|---|---|
| Kristiansand, normal for the date | 107.3 mm | 111.5 mm |
| mainland, normal for the date | 22.8 mm | **69.7 mm** |
| mainland, reported as | 2.82x normal | 0.92x normal |

Two thirds of the seNorge rectangle is outside Norway, so the national baseline
came out at roughly a third of its true value.

**What caught it was two numbers printed next to each other that could not both
be true.** The summary line said the country had 2.82 times its usual rain; the
percentile distribution printed beside it had a median of 0.51 and 4.5% of
cells above the 90th. A country at 2.8x normal does not have a median
percentile of a half. The per-cell percentiles were right all along - `pct` is
masked correctly, so nothing that drives the map was ever wrong - and it was
the ratio that was broken.

Worth keeping as a method note: the two numbers were derived from the same
arrays by different routes, and printing both is what made the inconsistency
visible. A summary that had only reported the ratio would have looked fine.

### 25b. Three resolutions, and why each needs its own lookup

The app holds a budget of *cells*, not of ground, so one resolution covers
about 9,800 km2 and then stops. That is the first thing a beginner does - zoom
out to see where in the county is worth walking - and section 24 shipped with
it answered by a line of text.

The exporter now cuts three levels, each a factor of four coarser:

| level | cell | grid | tiles per species |
|---|---|---|---|
| 0 | 128 m | 9,543 x 12,005 | 455 |
| 1 | 512 m | 2,385 x 3,001 | 45 |
| 2 | 2,048 m | 596 x 750 | 6 |

Four-fold steps because the cost is in the number of levels, not their
coarseness: the pyramid adds 11% to what is shipped and the coarsest level puts
the whole mainland in 447,000 cells, inside the budget. The switch happens
where the finer level runs out, which is also where its extra detail is below
one screen pixel, so crossing one should be close to invisible.

**Each level carries its own 256-entry rank lookup.** The same two-pass blur
over a coarser grid covers different ground, so a 512 m field read against the
128 m distribution would move every zone boundary - plausibly, and with nothing
reporting it. This is the same failure the shipped lookup exists to prevent,
one axis over.

**Coarser levels are built from sums and counts, not from means.** A 512 m cell
is the mean of the 32 m cells under it only if the sixteen 128 m cells it came
from each held the same number of valid ones, and at the coast they do not.
Carrying the counts makes every level exactly the mean of its source cells
instead of a weighting that quietly favours whichever coarse cells happened to
be half sea. `tests/test_zone_tiles.py` pins it with a fixture where the two
disagree by more than half.

### 25c. The card's reasoning, and three grids that do not line up

Seven layers - land cover, tree species, stand age, crown cover, substrate,
elevation, autumn rainfall - tiled on the zone grid, **all seven stacked in one
PNG per tile**, fetched only when somebody taps. 455 tiles, 35.3 MB, 76 kB
each. A tap now reads `Skog, gran, ~73 ar, 200 moh.` rather than a percentile
on its own, which is what CLAUDE.md's progressive disclosure asked for.

Stacked rather than one file per layer because a tap needs all seven at once,
they compress better together, and the zone tiles' PNG decoder reads them
unchanged.

The sources do not share a grid, and each way they differ is its own mistake
waiting to happen:

| layer | source grid | how it is read |
|---|---|---|
| AR50, NGU losmasser | 32 m, the national grid | block mean, or majority for a class |
| SR16 | 16 m, its own origin | nearest neighbour at cell centres |
| autumn rainfall | 1 km, a third origin | sampled at cell centres |

The SR16 offsets work out to whole cells - row `2332 + 8r`, column `8c` - which
is a fact with a test rather than an assumption with a comment, and each reader
refuses a grid it does not tile exactly rather than rounding into it. SR16 is
striped one row per block, so reading only the rows the cell centres fall on
costs an eighth of the file: three layers in 29 seconds.

**Classes take the majority of their block, never the mean.** Halfway between
bog and bare rock is not a substrate. Ties go to the first code in a fixed
order, so a re-export does not redraw class boundaries for no visible reason.

**There is no TWI in the set, and the page says so.** The national model does
not have the feature either - flow accumulation over 1.83 billion cells does
not fit in 15.7 GB, measured in FINDINGS 18 - so the row is omitted rather than
shown as a dash, and the Om page's feature list, which is markup written for
Kristiansand, is replaced with one that does not claim it. A dash against a
named quantity reads as "missing here" when it is missing everywhere.

### 25d. What the tests caught this time

Three defects, none of them found by reading the code:

1. **A layer the payload does not carry came back `undefined`**, and every
   guard in the card tests `!== null`, so an absent layer printed "NaN mm".
2. **The national conditions file was inlined into the page and shipped beside
   it** - 1.19 MB of weather in what is otherwise a 0.27 MB page. Kristiansand
   inlines its 70 kB copy deliberately, as an offline floor; at national size
   there is nothing for it to be the floor of, because a first launch with no
   signal has no map tiles either.
3. **The test harness decoded node's UTF-8 output as cp1252**, which turned
   every `a` with a ring the card prints into mojibake. It had never bitten
   because every earlier assertion happened to be ASCII, and it would have
   hidden any Norwegian one from here on.

### 25e. Where the national app stands

| | |
|---|---|
| page | 0.27 MB |
| zone tiles | 4,048 files, 152.4 MB, three levels |
| context tiles | 455 files, 35.3 MB |
| conditions | 1.19 MB, about 100 kB gzipped, refetched per launch |
| at launch | the page, one conditions file, and 9 zone tiles |
| per pan | typically 4 tiles; per tap, 1 context tile |

What it does not have, in order of how much it matters:

1. **Nordland is the only held-out ground.** Not a delivery gap - a validation
   one, and the page carries it rather than letting the transfer AUC imply
   otherwise. It is also not fixable without another fit.
2. **The conditions grid is 4 km**, aggregated from seNorge's kilometre by
   block means. A rainfall anomaly barely moves over 4 km; the millimetre
   figure on a mountainside does, and the page says which resolution it is
   quoting.
3. **No TWI anywhere nationally**, as above.

### 25f. The window commit was slow, and it was the strings

Correctness was pinned; speed was not measured. A window commit rebinds the
grid, re-smooths it, re-ranks it and re-traces three contour levels, and it
runs every time the viewport leaves the loaded margin. Measured over the real
national tiles, with tracing and stitching timed apart and the segment count
printed beside every timing, so a run that traced an empty field could not
flatter the result:

| view | m/px | level | cells | segments | trace | stitch |
|---|---|---|---|---|---|---|
| a wood, walking zoom | 12 | 0 | 5,936 | 1,180 | 3 ms | 9 ms |
| a valley | 40 | 0 | 59,508 | 18,790 | 11 ms | 27 ms |
| **a county** | **120** | **0** | **520,198** | **158,518** | **87 ms** | **391 ms** |
| level 1 takes over | 400 | 1 | 362,096 | 57,976 | 29 ms | 83 ms |
| the whole country | 3000 | 2 | 373,500 | 16,826 | 12 ms | 23 ms |

This desktop is three to five times faster than a mid-range phone, so the
county row was a stall a user could count - at a zoom the pyramid was built to
serve.

That the segment count is worth printing is not fussiness. The first two
attempts at this measurement pointed the probe at the wrong tiles root, loaded
nothing, traced a field of zeros, and reported a commit of 13 ms - a result
that looks like good news and means the harness is broken. A timing is only
readable next to a count of the work it did.

**Stitching contour segments into rings was three quarters of the contour
rebuild**, across 158,518 segments at the county view. Not tracing, not
smoothing, not ranking: the bookkeeping. Each segment end was keyed by a
string built from two rounded
coordinates, stored in a plain object that becomes a dictionary at a hundred
thousand distinct keys, and the ring walk recomputed those keys as it went -
including the ring's own first key on every step of every ring.

The key is now the two rounded coordinates packed into one float in a `Map`,
computed once per segment end up front, with a segment end held as one integer
rather than an object. Same algorithm, same traversal order.

Three repeats of the county view, the old code and the new one in the same
session, medians quoted:

    stitching             391 -> 99 ms
    whole contour rebuild 522 -> 154 ms

Runs spread by roughly a third either way on a desktop with a warm JIT and
nothing pinned, so **the ratio is the durable number and the absolutes are
not**. The single-run pair quoted in the commit message - 605 -> 291 ms - sits
at the high end of that spread and describes the same 3.2x.

**The geometry was proven identical rather than assumed.** The committed
version and the rewrite were each run over the real national tiles at four
zooms with a recording `Path2D`, and compared vertex for vertex: 135,560 path
operations at the worst case, all matching. That was only possible because the
DOM stub was changed to record its path vertices - it had been discarding them,
which left the tracer and the stitcher countable but not checkable.

The repeat runs above carry a second, independent check on the same claim,
free: both versions emitted the same segment count and the same path-operation
count at every zoom tested. A rewrite that had merged or dropped a vertex
would have had to do it without changing either total.

A method note worth keeping: the first measurement said "repaint is 87% of the
commit", which is true and not actionable. Splitting repaint into tracing and
stitching is what turned it into a change. Timing one stage tells you where to
look next, not what to do.

### 25g. The national build has no marked routes, and now says so

Turrutebasen is packed into the page - 5,804 lines and 60,986 vertices in
395 kB, which is the right shape for one kommune. The mainland is 111 times
that area, so the same packing is tens of megabytes and wants tiling of its
own. The national build therefore ships none, and did so silently while still
offering a switch for them in settings.

A switch that governs nothing is worse than a missing one: it tells the reader
the feature is there and broken. The row is hidden when the build carries no
route geometry, and pinned in both directions - the inline build offers it, the
tiled build does not.

Marked routes are still visible nationally. Kartverket's `topo` sheet draws
them, and it is the other option in the basemap picker directly above the row
that is now hidden.

### 25h. A suite nobody runs is not a gate

Two of the sixteen node suites under `tests/` had been failing for at least six
consecutive commits and nothing said so. `pytest` collects `testpaths =
["tests"]`, which is Python only; the JS suites were run by hand, a few at a
time, and "all JS suites pass" meant "the five I ran passed".

Neither breakage was a bug in the thing under test. Both were the harness
losing its grip on a file that moved underneath it:

- `test_sync_e2e.js` injects a probe into the shell by matching the literal
  `size();\nview.cx=`. A default map centre was added between those two lines,
  so the anchor stopped matching - and `String.replace` returns its input
  unchanged when the anchor is absent rather than raising. The probe silently
  vanished and the suite died forty lines later on `cannot read SYNC of
  undefined`, a symptom that says nothing about the cause.
- `test_om_tables.js` extracts one function from the shell and evaluates it
  alone. The Om copy began quoting `TESTED_ON`, a name declared above it
  precisely so that three pieces of copy cannot disagree about which region the
  estimate was held out on - and a function cut away from its file cannot see a
  declaration it was cut away from.

`test_gestures.js` had already been re-anchored once for exactly the first
reason, which should have been the signal that the pattern needed fixing rather
than the instance.

Three changes, in increasing order of what they prevent:

1. Both probe injections now assert that their anchor matched, and name it when
   it has not. A test that reaches into another file will eventually reach into
   nothing; it should fail at the reach, not at the symptom.
2. `test_om_tables.js` is handed the region name and checks that it lands - a
   build tested on other ground has to say so - so the injection buys an
   assertion rather than only a workaround. Hardcoding the region back into the
   copy fails it.
3. `tests/test_js_suites.py` runs every `tests/test_*.js` under pytest, so one
   command is the whole gate. Moving the boot anchor now turns the Python run
   red, which is the check that was missing.

The one suite that talks to the live Supabase project stays opt-in behind
`SOPP_LIVE=1`: it writes rows to the real database and cleans up after itself,
which is a reasonable thing to do deliberately and not on every routine test
run. It is skipped *loudly*, which is the whole difference between a known
exclusion and the invisible one this entry is about.

    697 passed, 1 skipped.

### 25i. The map read "high" almost everywhere, and a national ranking was why

Reported from the phone, not from a test: *"why is the chance of finding
mushrooms at least high everywhere? Even in urban areas the chance is high. Why
did the map in Kristiansand change? It was better before because it was more
specific."*

All of it was right, and the measurement is worth keeping because of how
comfortably it had hidden.

**Nationally the distribution was perfect.** Over all 20.0 million scored cells
the five bands held 21 / 24 / 24 / 18 / 12 per cent. Nothing in that number is
wrong, and nothing in a test that checks it could have caught this. The problem
only exists once somebody stands somewhere:

| Kristiansand kommune, 100,341 cells | Svært lav | Lav | Moderat | Høy | Svært høy | median |
|---|---|---|---|---|---|---|
| as it shipped, ranked against Norway | 0.0% | 0.0% | 5.1% | 18.0% | **76.9%** | 97.5 |
| the build it replaced, ranked against Kristiansand | 20% | 25% | 25% | 20% | 10% | 50.0 |

Kristiansand is lowland, coastal, spruce, mild and wet. It genuinely sits in
the national top decile nearly everywhere, so ranking it against Norway paints
the whole kommune one colour and the local detail - the thing the map is for -
disappears. Oslo and its marka came out at 54% top band, median 92.

That encoding was a deliberate choice, argued for at length in
`export_zone_tiles.py`: percentiles computed per view move when the user pans,
so "top 5%" would silently become "top 5% of what is on screen". The argument
was right and the conclusion did not follow. A *precomputed neighbourhood* rank
keeps the stability and gives the contrast back; it simply never occurred to me
that there was a third option between "national" and "per view".

**Second, unrelated defect: nothing masked non-habitat.** Joining the zone
tiles to AR50, cell for cell:

| land cover | cells scored | median percentile | "Høy" or above |
|---|---|---|---|
| Skog | 8,117,895 | 78.1 | 63% |
| **Bebygd** | **213,310 (100%)** | **68.5** | **46%** |
| **Ferskvann** | **1,049,154 (100%)** | **62.4** | **33%** |
| Jordbruksareal | 772,640 | 61.6 | 29% |
| Åpen fastmark | 8,474,759 | 21.5 | 1% |
| Snø og isbre | 145,974 | 0.9 | 0% |

The model discriminates correctly where it has habitat data - forest 78th, bare
ground 21st, glacier 1st. It was simply never told to stay off water and
asphalt, and the prediction ran wherever the features were finite. Only 3.1% of
sea cells carry a score, which is the blur bleeding a cell or two offshore
rather than the open sea being rated for mushrooms.

**The two are independent, and that is the part worth remembering.** Masking
alone moves Kristiansand's top band from 76.9% to *79.4%* - slightly worse -
because habitat is 88% of scored cells anyway, so removing the other 12% barely
touches the distribution. Only re-ranking restores the spread. Two defects with
one symptom, and either one "fixed" alone would have looked like a failed fix.

#### What replaced it

Each cell is ranked against the habitat within 25 km, precomputed, so the value
does not move when the user pans or zooms. Exactly, that is 114 million cells
each ranked against ~400,000 neighbours; done as histograms on an 8 km grid it
is five seconds. National quantiles are the bin edges, so resolution sits where
the data is; the four surrounding block CDFs are interpolated between, or the
map carries visible 8 km seams.

The radius was swept rather than guessed, on the real rasters:

| radius | Kristiansand bands | median |
|---|---|---|
| 15 km | 14.0 / 21.2 / 24.5 / 22.9 / 17.3 | 60.2 |
| **25 km** | **10.6 / 19.9 / 25.4 / 24.3 / 19.8** | **64.6** |
| 45 km | 4.8 / 15.4 / 26.7 / 28.4 / 24.6 | 72.3 |
| 80 km | 1.0 / 3.3 / 21.9 / 39.4 / 34.3 | 83.2 |

45 km was the number I had proposed before measuring anything. 25 km fills all
five bands, still says the kommune is better than its surroundings - which is
true - and is about the distance somebody actually chooses between on a
Saturday.

One more thing had to be fixed before it worked properly. Reading a cell's
percentile straight off its bin's CDF gives every cell in that bin the same
answer, and in a neighbourhood whose habitat falls inside a handful of national
bins that is a plateau: **13.5% of Kristiansand came out at exactly 1.0**, so
the "top 5%" zone covered 13.5% of the kommune. Interpolating within the bin as
well as between blocks removes it entirely.

| Kristiansand | Svært lav | Lav | Moderat | Høy | Svært høy | median | at exactly 1.0 |
|---|---|---|---|---|---|---|---|
| shipped | 0.0% | 0.0% | 5.1% | 18.0% | 76.9% | 97.5 | — |
| 25 km, bin CDF only | 10.6% | 19.9% | 25.4% | 24.3% | 19.8% | 64.6 | 13.51% |
| **25 km, interpolated** | **11.3%** | **21.1%** | **26.8%** | **30.3%** | **10.4%** | **62.0** | **0.00%** |

The mask blanks 2.3 million of 20.0 million cells (11.6%): freshwater,
built-up, farmland, glacier, and the coastal fringe. Those cells are removed
before anything is ranked, so they neither take a colour nor occupy room in the
distribution their neighbours are measured against.

#### The method note

A summary statistic over the whole country cannot see a problem that is about
where you are standing. The national spread was 21/24/24/18/12 and stayed that
way through every version of this bug. What found it was grouping the same
cells two ways that no test had: **by kommune**, and **by land cover**. Both are
obvious in hindsight and neither existed. The general form: when a number is
computed over everything, check it again over the subsets a user actually
occupies.

## Open questions

- **Should the maps still be blurred at all?** Narrowed on 2026-09-15, after
  the claim was checked against both builds rather than against the pipeline
  alone. It was written when Kristiansand was the only build there was, and it
  read as though it described the app. It does not.

  **The deployed national map applies no suppression at all**, and that is
  measured, not inferred: `build_maps_national.py` records
  `suppression="none - not a live constraint"`, `export_zone_tiles.py` has no
  suppression pass, and the shipped `dist_norge/index.html` contains neither a
  `suppressed` mask nor any `meta.suppression` string. Every `blur` in
  `web/shell.html` is either a CSS backdrop filter or the two-pass contour
  smoothing of section 25l. Nothing a reader of soppspor.pages.dev sees has
  been blurred for habitat reasons.

  **The Kristiansand build is where the question is still live**, and there the
  original finding stands unchanged. `build_maps.py` applies a 500 m blur
  around 1857 Naturbase sensitive sites, affecting **12.62%** of scored cells;
  `data/processed/web_preview/payload.js` carries both the resulting surfaces
  and a 236 kB base64 `suppressed` mask, and `shell.html` reads that key
  nowhere. So the combination `export_web_preview.py`'s own docstring calls
  worse than no suppression - "a blurred area that does not admit it is
  blurred ... because the reader trusts detail that is not there" - is exactly
  what `SOPP_BUILD=kommune` still produces. It is not deployed and has not been
  since the national map shipped, but it is one environment variable away.

  The three ways out are unchanged and the choice is still the team's: remove
  the suppression from `build_maps.py`, restore the disclosure in the
  interface, or keep both and say so in Om. What has changed is the urgency and
  the blast radius. Note that the Phase 0 suitability GeoTIFFs are themselves
  suppressed artefacts, so removing the blur means regenerating the published
  Phase 0 deliverable, not editing a constant.

- ~~Does 7.5 km blocking leave enough presences per fold at the Agder extent?~~
  Answered in section 2: yes, 46–83 per fold, insensitive to block size.
- ~~What is the empirical spatial autocorrelation range of the real occurrence
  data?~~ Answered in section 3: there isn't one. Dependence declines smoothly
  to 30 km without a floor, so the buffer is a declared trade-off and blocked
  CV here retains known leakage.
- Does the clumping flatten beyond 30 km? Not answerable at the Agder extent.
  It matters for interpretation, not for the buffer, which is bounded by the
  data budget well below that.
- ~~Volume of the target-group background pull (all `Sopper` records in Agder)
  is unknown.~~ Answered in section 4: 82,129 records, 3,882 after thinning.
  The paged JSON API handled it in nine minutes; no separate access route is
  needed. Server-side filtering turned out to exist after all - see
  [data/SOURCES.md](data/SOURCES.md).
- ~~Nothing has been fetched for any environmental layer yet.~~ All seven
  endpoints are verified against a real record. Terrain, seNorge, SR16 and
  losmasser are all built and in the stack. AR5 remains blocked on a Geonorge
  FKB login; Naturbase and the NVE soil-moisture grids are unfetched.
- ~~Which weeks and which years of seNorge?~~ Answered in section 6:
  9 Aug - 20 Oct, meaned over 1991-2020, derived from the record phenology.
  The raster is built, and the choice of window is demonstrably not load-
  bearing (r >= 0.997 against windows shifted a fortnight either way).
- ~~Does the autumn-rainfall feature carry ecology or accessibility?~~ Closed
  in section 22h, and **not** by the reasoning that first closed it. The claim
  here used to be that its permutation importance stays between -0.007 and
  +0.039 "in every run". That was true of every Agder run and false the moment
  the national stack was fitted: importance reaches +0.069 for *C. tubaeformis*
  and correlates with the national AUC gain at +0.826 across eight species.
  What actually closes it is the ablation. Refitting the whole national stack
  without the climate layer costs a mean of 0.008 AUC on blocked CV and 0.000
  on transfer, and *C. tubaeformis* keeps first place on both models and both
  harnesses. No load-bearing conclusion depends on the feature.
- Can the 13 shoreline presences be recovered at a finer DTM resolution? They
  are lost to the 32 m cell size, not to the mask logic.
- ~~Does adding SR16 forest structure raise *C. tubaeformis* more than the
  other two?~~ Answered in section 8: yes on maxent (+0.047 against +0.013 and
  +0.001), marginally on lightgbm. But see the transfer result below before
  reading much into it.
- ~~Does NGU losmasser separate the three species?~~ Answered in section 9a:
  no. Every substrate column sits at or below +0.012 importance, and the
  registered "moves everything by less than 0.02" prediction held exactly.
- ~~Is the Boyce index measuring the models or itself?~~ Answered in section
  9b: itself, on scores this skewed. The published implementation returns
  -0.42 for a model with no skill. Both implementations are now reported side
  by side and the sections 7-8 Boyce readings are retracted.
- ~~Does a model fitted on Agder hold up on Kristiansand?~~ Answered in section
  9c: modestly (0.605-0.730, null clean), but the registered species ordering
  does not survive the move on either model, and *C. tubaeformis* comes last.
- Would AR5 land cover separate the species where SR16 and losmasser did not?
  Untested. It needs a Geonorge login carrying the FKB role, which we do not
  have. This is now the largest untried option.
- Is 32 m simply too coarse for TWI to mean anything for *C. tubaeformis*?
  Still open, and now the only live explanation for TWI's failure other than
  the prediction being wrong. Testing it needs terrain derivatives recomputed
  at 16 m *and* a model refitted on them - resampling will not do, because TWI
  at 16 m is a different variable.
- Does the reversal in section 9c survive more held-out records? With 45-53
  presences per species the transfer intervals overlap almost completely.
  Phase 1 field logging is the only route to more, and absences would change
  the question entirely.
- How much should recent rainfall count? The conditions weight in section
  12 is judgement, not an estimate, and cannot become one without field
  logs that record the date of each walk and its outcome. This is now the
  clearest thing Phase 1 logging would buy that nothing else can.
- Can the exclusion buffer now be set by direct measurement rather than by the
  cost trade-off in section 3? Still open, and now cheaper: the null control
  runs on real geography in both the blocked and the transfer harness.

### 25j. Three features were dead because an author rule beats a browser rule

Reported from the phone as "the tursti function does not work". It did not. The
national build ships no route geometry — Turrutebasen packs into the page at
395 kB for one kommune and tens of megabytes for the mainland — so the settings
row hides itself, and the comment above that line says why: a switch that
silently governs nothing is worse than no switch.

It did not hide. `[hidden]{display:none}` is a **user-agent** stylesheet rule,
and author rules beat user-agent rules whatever their specificity. `.row` sets
`display:flex`, so `rowTrails.hidden = true` did nothing at all.

Auditing every `.hidden` in the interface the same way found two more, both
silent:

| element | kept visible by | what broke |
|---|---|---|
| `rowTrails` | `.row` | a switch for a feature that is not in this build |
| `condPill` | `.condpill` | an empty weather pill when there is no conditions file |
| `wipeShow` | `.ghost` | delete-everything stays visible under its own confirm box |

One author-origin rule fixes all three. `!important` is load-bearing rather than
decoration: `[hidden]` and `.row` have equal specificity and `.row` comes later,
so a plain declaration still loses. `tests/test_hidden_elements.py` re-runs the
audit and fails if the rule is removed while anything still depends on it.

### 25k. The map and its legend disagreeing, three ways

All three came back from the phone within a day of each other, and all three
were the interface contradicting itself rather than the model being wrong.

**The legend named a band the card would not.** The middle shade is drawn at
the 85th percentile and was labelled "svært høy"; the card does not say "Svært
høy" until the 90th. A reader could stand inside a ring marked *svært høy*, tap
it, and be told *Høy*. The labels are now derived from the levels they are
drawn at, so a threshold cannot move without the legend following, and the
wording is the one Om already used: the top 30, 15 and 5 per cent. Dropping the
evaluative words was the point rather than a side effect — the first thing this
interface was ever told from a phone was that reading "høy" everywhere sounded
like a claim about the chance of finding something, which is precisely what
`ab_zonewarn` says the zones are not.

**The outer boundary moved when a species was added.** One species drew three
bands, 70th / 85th / 95th. Two or more dropped the outer one, so toggling a
second species pulled the boundary in from the 70th to the 85th and the shading
a reader had been studying collapsed to a thin ring — reported as "the zones
turn singular again", with nothing on screen to say the threshold had changed.
The argument for an outline-led overlay was sound and is kept; dropping a
threshold was never part of it. Both modes cut at the same three percentiles
now and differ only in how they are painted. The test that had asserted the old
behaviour outright — "with several species up each shows fewer bands" — is how
a deliberate choice becomes an invariant nobody revisits.

**The legend promised a band that was not there.** With one species over
Baneheia: "only two layers in the zone when 1 is toggled on?" Two is correct.
Rebuilding the field the contours are cut from out of the shipped tiles —
bytes → neighbourhood rank → the validity-weighted blur twice → the national
CDF that ships beside the tiles — Baneheia reaches the 93rd percentile for
kantarell and the 87th for traktkantarell. The top band is drawn at the 95th, so
the tracer returns no segments and `paths[2]` is null. The legend now dims a
band it found nothing for, which answers the question where it was asked: not
"something failed to draw" but "there is none of this here".

A fourth followed from the second. Outlines with nothing inside them read as
lines rather than places — "it's difficult to see when the zones aren't filled
in" — so the bands are filled again at an opacity that falls as `1/sqrt(n)`.
The outlines deliberately do **not** thin with the count: where fills overlap
they blend and stop saying whose ground it is, and the contour lines do not
blend. Fill answers "is there a zone here", line answers "whose".

### 25l. A 128 m grid cannot draw the edge of a 100 m tarn

Reported as "the random squares look very weird ... it looks half done", and
they did. The data was right and the geometry was not.

Checking the blank cells against AR50 cell for cell over Baneheia: **100 %
agreement, no offset**. Of the six blank patches in that screenshot, one was a
275-cell city block and five were *single* 128 m cells — two tarns and three
clusters of houses. A contour closing around one cell passes through the
midpoints of its four edges, so a one-cell blank can only ever be drawn as a
diamond.

| enclosed blank patches, nationally | count | cells | area |
|---|---|---|---|
| all | 137,215 | 1,467,817 | 1.28 % of the grid |
| exactly one cell | **63,011** | 63,011 | 1,032 km² |
| two cells or fewer | 84,917 | 106,823 | 1,750 km² |

So the grid stops being asked to draw that edge. `drawField` grows the field
into its blanks twice before the tracer sees it, so a contour runs straight over
a small blank while anything larger keeps a blank middle — two passes only reach
two cells in. `scripts/export_mask_tiles.py` then writes AR50 at four times the
zone resolution, one tile per zone tile position, 1024 px against 256, which
makes level 0 the 32 m source itself and needs no index of its own. Habitat is
transparent and everything else opaque, which is what `destination-out`
consumes. **711 tiles, 8.6 MB for the whole country**, shared by every species
because it describes the ground rather than a prediction about it.

`smoothField` is untouched, so a tap over water still finds no zone and the card
still says so. The drawing may be smoother than the grid; it may not make a
claim the card would contradict.

The alignment is the part that had to be tested rather than argued. A mask half
a cell out would erase the wrong side of every shoreline in Norway and still
look like a map. `tests/test_mask_tiles.py` decodes the shipped tiles and
asserts every scored cell is habitat by the exporter's own rule; **a one-pixel
(32 m) shift breaks it**, verified by shifting one.

### 25m. Search, and a coordinate pair named two different ways

Two open registers, both Kartverket's: `api.kartverket.no/stedsnavn/v1/navn`
for place names and `ws.geonorge.no/adresser/v1/sok` for addresses. Both answer
in EPSG:25833 when asked and both return `access-control-allow-origin: *` —
checked rather than assumed, which is why the browser calls them directly and
no proxy of ours ever sees a query. Recorded in `data/SOURCES.md` with the
access date.

**Server relevance is not the reader's relevance.** `fuzzy=true` on "Nordmarka"
returns 1,325 matches and the one in Oslo is not reliably first, so the
interface asks for 25 rows and sorts by distance from the middle of the screen
before showing 8. A query with a digit is somebody typing a street number, so
addresses lead; otherwise the place name was what was meant.

**The dangerous part is the coordinate pair.** stedsnavn answers
`{koordsys, nord, øst}`; adresser answers `{epsg, lat, lon}` where `lat` holds a
*northing* and `lon` an *easting*. Read both in the same order and every address
in Norway lands in the Norwegian Sea, silently, because nothing downstream knows
what a plausible coordinate looks like. So the labels are a hint and the
national bounds are the authority: whichever order lands in Norway is the
answer, and a pair that lands nowhere is dropped rather than drawn.

Nothing about a search is kept — no history, no last query — and the service
worker does not cache those hosts. A cached map tile is harmless; a cached
search response is a record of what somebody typed, sitting on their phone.

The privacy policy already named Kartverket, for the basemap, so the check that
every contacted host is named **stayed green when this was added** and could not
have caught the omission on its own. A map tile reveals where you are looking; a
search box reveals what you were looking for, in your own words.

### 25n. What an installed copy was not picking up

Asked plainly: does it keep up with later deployed updates? Mostly, and in two
places it did not. The worker is the one piece of this app nothing else can see
— it sits between a reader and the origin, only on an installed copy, and every
check we have, the deployment check included, asks the origin instead.

**The privacy policy was unreachable once installed.** Every navigation went to
`shellFirst`, and `shellFirst` answered all of them with `./index.html`, so
tapping "Personvern" in settings opened the map. The policy was precached,
correctly, and never consulted.

**Tiles could stay a year out of date.** They are served
`Cache-Control: immutable` and kept cache-first with no revalidation, both
resting on a comment claiming a rebuild writes a new tile rather than changing
one in place. It does not: the exporter names a tile from its row and column, so
a re-export puts different bytes at the same address. When the ranking changed
from a national percentile to a rank within 25 km (25i), **every tile's contents
changed and not one URL did.**

Tile URLs now carry a stamp taken from the bytes that ship — a digest over every
zone, mask and context tile, name and content both, so two tiles swapping places
changes it. Because it follows the bytes and not the clock, a rebuild that
changes only the page leaves every tile URL alone. Demonstrated on the next
deploy: *"Uploaded 2 files (5,213 already uploaded)"*, stamp unchanged.

What still holds, and is a trade rather than a defect: the shell is
stale-while-revalidate, so a reader gets the previous version on launch and the
new one on the launch after. Network-first would hold a multi-megabyte page
hostage to a signal the woods do not have. A banner now says when the shell on
screen has been replaced, and does no more than say so — nothing reloads on its
own, because the reader may be halfway through writing a note.

#### The method note

Every defect in 25j to 25n was invisible to the test suite and visible on a
phone within a minute. They share a shape: each lived in a place no check could
reach. An author CSS rule beating a browser one; a legend and a card computing
the same word from different thresholds; a contour tracer asked to draw
something finer than its grid; a service worker that only exists on somebody
else's device.

The general form, and it is the same lesson as 25i from a different direction:
**a check that runs where the code runs cannot see a problem that only exists
where the code is used.** The suite grew from 764 to 790 across these, and every
one of the new tests was written to run the real thing against stubs that can
tell the wrong answer from the right one — the worker's routing, the erase
geometry, the coordinate pair, the dimming — rather than to assert that the
source contains a string.

## 26. The weather stopped being part of the build - 2026-09-15

CLAUDE.md allows this map to improve in exactly two ways, and the second is
"fresher data on the same predictors - the conditions layer tracks seNorge on
a schedule rather than being baked in at build time." It was baked in at build
time, and the schedule did not exist. Looking for where to put one found that
the layer was worse than stale: it was stale behind a line of output that said
it had just been refreshed.

### 26a. A deploy that refreshed a file it would not ship

`deploy.sh` builds Norway by default. Its weather step ran

```
python scripts/fetch_recent_weather.py --if-new
```

with no `--national`, two lines above a build that passes `--national`. Those
are two different files, and the fetcher chooses between them on that flag
alone:

| | written by | shipped by |
|---|---|---|
| `data/processed/conditions.json` | `--if-new` | the Kristiansand build |
| `data/processed/conditions_norge.json` | `--if-new --national` | **the national build** |

So every national deploy printed `==> refreshing the weather`, refreshed
Kristiansand, and published whatever national weather happened to be on disk.
Caught by three timestamps that could not all be true at once:

| | modified | `last_day` |
|---|---|---|
| `conditions.json` (69 kB) | 2026-09-15 14:22 | 2026-09-14 |
| `conditions_norge.json` (1.19 MB) | 2026-09-14 22:37 | 2026-09-13 |
| what soppspor.pages.dev served | | **2026-09-13** |

A day behind, on a layer whose entire purpose is to be current, with a deploy
script reporting success. The fix is `"${FLAG[@]}"` - the same array the build
two lines below already uses, so the two can no longer disagree. What is worth
keeping is the shape rather than the fix: **a step that refreshes an input and
a step that consumes one were parameterised separately**, which is the same
defect as the ranking bug in 25i and the legend in 25k, and it was invisible
for the same reason. Every check ran on one of the two files.

`tests/test_deploy_script.py` now asserts the weather step carries the build's
flag, next to the existing assertions that the bundle check and the publish
step run against what was built. Verified by reintroducing the old line and
watching it fail.

### 26b. Why the schedule could not simply redeploy

The obvious job - fetch the weather, rebuild, publish - is not available here,
and the reason is worth recording because it is a property of the hosting
rather than of this code.

**A Cloudflare Pages deployment is an immutable, whole-directory snapshot.**
There is no partial update: a deployment made from a directory containing only
`conditions.json` contains only `conditions.json`, and everything else 404s.
This site is 4,751 files and about 204 MB, of which roughly 196 MB is zone,
mask and context tiles that come from an overnight prediction run and are
deliberately not in the repository. CI cannot rebuild them.

That leaves a daily job that would have to reconstruct 196 MB it does not have
- from a cache, or by mirroring the live site - and then republish all of it to
change one 1.19 MB file. The steady state is absurd and the failure mode is
worse: a partial or corrupt directory deploys cleanly and takes the map off the
internet. Weighed against a layer the app already treats as optional, that is
the wrong risk to accept twice a day.

So the file left the deployment instead. `functions/conditions.json.js` serves
it from Workers KV; `.github/workflows/conditions.yml` writes only that key and
**never deploys**. The worst a broken run can do is leave yesterday's reading
in place, which is a state the app has always handled - `refreshConditions`
reads `r.ok ? r.json() : null` and draws the map without a conditions layer.

The choice was put to the team rather than made here, because it adds a
dependency and CLAUDE.md requires asking. The alternative on the table was the
cache-and-republish job above.

### 26c. A function beats a real file, and it went out the other way round

This was written on the assumption that a static asset at a path wins over a
function at the same path, so shipping both would be safe and the file would
simply keep serving until KV existed. That is **backwards**, and it was
deployed before it was measured.

Cloudflare Pages routes a request to the Function first. `wrangler pages
deploy` picked up the new `functions/` directory - printing `Uploading
Functions bundle`, which is the only notice anyone gets - and from that moment
`/conditions.json` was answered by a route whose "no KV binding" case returned
**HTTP 503**. There was no KV binding. The live conditions layer was down until
it was checked by hand.

Two things kept it from being worse and one thing hid it:

- **The app degrades correctly.** `refreshConditions` reads
  `r.ok ? r.json() : null`, so the map drew with no weather rather than
  breaking. The zones, tiles and mask were untouched throughout.
- **The deployment check passed anyway**, reporting `the weather is served
  HTTP 200` and `it carries a last day 2026-09-13`. It was reading an
  edge-cached copy of the *previous* deployment, and its only assertion was
  that some day existed - and the day that was already live is still a day.
  Both are now fixed: the fetch carries a cache-buster, and the served day is
  compared against the built one. Had that comparison existed, the mismatch
  would have been the first line to fail.

**The fix is better than the design it replaces.** The route now falls through
to the asset server with `context.next()` whenever KV cannot answer - no
binding, or a bound namespace with nothing written yet. So the function is
*additive*: with KV it serves KV, without it serves whatever the build shipped.
The ordering hazard this section used to describe is gone, `SOPP_CONDITIONS`
drops from a correctness requirement to a question of what to ship, and a KV
outage falls back to a stale file rather than to an error.

The fall-through has one sharp edge of its own, which is tested: `_redirects`
ends with `/*  /index.html  200`, so a path with no file behind it returns the
app with status 200 and a body of HTML. `passThrough` therefore checks the
content type rather than the status, and turns anything that is not JSON into a
clean 404.

Verified on a preview branch before production, which is the step whose absence
caused this: `conditions-check.soppspor.pages.dev/conditions.json` returned 200,
`application/json`, 1,191,631 bytes, GET and HEAD agreeing, on three consecutive
requests. Production then returned the same, with `last_day` 2026-09-14.

`tests/test_conditions_route.js` runs the real module against stubs for both
KV and `next()`, over every state: populated, bound-but-empty, unbound with a
file, unbound with no file, and HEAD against GET for each. The last assertion
in the file exists only because of this incident - *an absent binding is never
a 5xx* - because that is the exact line that took the layer down.

The cross-file invariants are pinned too, in `tests/test_conditions_kv.py`,
because nothing at runtime complains when they drift: the key the workflow
writes is the key the route reads, and the header the route sets is the header
the workflow reads to decide whether to write at all. Rename either and the job
rewrites KV twice a day forever while every run reports success.

**The method note, and it is 25n's lesson arriving from a third direction.**
A service worker could not be seen by any check because it only exists on
somebody else's device. A Pages Function cannot be seen by any check because it
only exists on Cloudflare's edge, and its behaviour depends on runtime state -
a binding - that reading the built directory can never reveal. Both were
deployed on an assumption about a platform rule rather than a measurement of
one. The assumption was one sentence long, load-bearing, and wrong, and the
cost of checking it first was a preview deployment that takes nine seconds.

One more thing follows from the fall-through, and it is the same argument in
a different place. If the route works without KV, then a repository without
the KV secrets is a working repository - so the scheduled job **skips the
write and says so** rather than failing. A workflow that went red twice a day
for a feature nobody had turned on yet would train us to ignore it, which is
precisely how the two JS suites in `test_js_suites.py`'s docstring rotted
through six consecutive commits. The skip is loud rather than silent, which is
the distinction that keeps it from being the other failure mode.

Suite: **790 -> 808** pytest cases; the new suites add 16 Python assertions
and 20 JavaScript ones, the latter counting as a single case.

### 26d. Two things checked rather than assumed

**The national weather is current again.** Fetched directly: `last_day`
2026-09-14, one day behind, 0.94x normal rainfall against a 54% median
percentile. Those last two are the consistency check from 25a - a country at
0.94x normal should have a median percentile near a half, and it does - so the
sea-as-drought bug has not returned. **Deployed and verified live**: the whole
deployment check passes against `dist_norge/`, including the new assertion that
the day being served is the day that was built.

**Account recovery still does not work, but it has moved.** The failure is no
longer the built-in mailer's HTTP 429; `scripts/check_auth_email.py` now
returns **HTTP 500, "Error sending confirmation email"**. That is custom SMTP
configured and the provider rejecting the message, which in practice means
credentials or an unverified sending domain rather than anything in this
repository. The distinction matters: 429 was a platform limit no setting could
raise, 500 is a configuration that is nearly right.

### 26e. The suppression question was narrower than it read

Checked against both builds rather than against the pipeline alone, and
corrected in Open questions above. The short version: **the deployed national
map applies no suppression at all** - no pass in `build_maps_national.py` or
`export_zone_tiles.py`, and no `suppressed` mask or `meta.suppression` string
in the shipped `dist_norge/index.html`. Every `blur` in `web/shell.html` is a
CSS backdrop filter or the contour smoothing of 25l.

The finding as originally written was true of the Kristiansand build and was
read as a statement about the app. It is not one, and it has not been since the
national map shipped. It remains true of `SOPP_BUILD=kommune`, which is one
environment variable away and still ships both a blurred surface and a 236 kB
mask nothing reads.

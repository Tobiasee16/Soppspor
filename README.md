# Soppspor

A map of where mushrooms **might** grow across mainland Norway, estimated from
public geodata — and an honest account of how well that works, which is
somewhere between "better than chance" and "far from reliable".

Live at **https://soppspor.pages.dev**. Kristiansand is still the *validation*
extent — the held-out ground the transferability test is judged on — but the
shipped map covers the country.

It is not a mushroom identifier. **It never tells anyone whether anything is
safe to eat, and never identifies a species from a photograph.** Those two
limits are permanent and structural, not settings.

```
./deploy.sh --cloudflare            # builds Norway, publishes, then checks what is live
python scripts/build_hosted.py --national     # -> dist_norge/, 204 MB of static site
python -m pytest -q                 # 790 tests: Python and the JS suites together
```

## What it actually does

Takes fungal occurrence records from Artskart, learns what kind of terrain they
sit in from 40-odd public map layers, and shades the rest of the country by how
well it matches. A rainfall layer from MET's seNorge grid is laid on top, so a
place that is right but bone dry is ranked below one that is right and wet.

Seven species. Each shows its own measured accuracy in the app, because they
are not equally good and pretending otherwise would be the easiest way to
mislead somebody. A species only appears at all if it beats a synthetic control
with no ecology in it — one did not, and is reported everywhere but drawn
nowhere.

**What it is not** is a prediction that anything is there. The model has never
been validated against anybody's actual walk. There are no absence records: no
one has written down "I looked here and found nothing", so the model cannot
tell a bad wood from an unvisited one. It says where the *terrain resembles*
places the species has been recorded. That is a different claim and the
interface says so on every screen.

## Honesty apparatus

The methodology is the point, so it is worth saying what stops this being a
map that looks convincing and means nothing:

- **Spatial block cross-validation**, never random splits. Occurrence records
  cluster; a random hold-out puts neighbours on both sides and inflates the
  score. 7.5 km blocks with a 4 km exclusion buffer.
- **Target-group background.** Comparison points are drawn from *all* fungal
  records in Agder, not uniformly, so the model has to find something other
  than "near a road" to separate them.
- **A synthetic null** run alongside every real species, with fake presences
  drawn from the recording-effort surface and no ecology at all. It should land
  on 0.5. It does — 0.492. If it ever does not, nothing else in the run means
  anything.
- **Predictions registered before the models existed**, in `FINDINGS.md`, so a
  disappointing result cannot be reinterpreted afterwards. One of them failed,
  and the failure is written up rather than quietly dropped.

## Two builds

| | |
|---|---|
| `scripts/build_hosted.py --national` | `dist_norge/`, about 204 MB: one page plus 4,040 zone tiles, 711 habitat-mask tiles and the context layer. Basemap from Kartverket at runtime. Installs to a home screen and works offline over ground already walked. **This is what is deployed.** |
| `scripts/build_hosted.py` | `dist/`, one page and no tiles — the Kristiansand-only build, which carries its whole surface inline. |
| `scripts/build_interface.py` | one self-contained HTML file, 14.8 MB, everything inlined. Needs no server and cannot leave Kristiansand — the basemap alone would be a gigabyte nationally. See `EXPANSION.md`. |

The national build also runs `scripts/export_zone_tiles.py` and
`scripts/export_mask_tiles.py` upstream of it; those are slow (roughly ten
minutes) and are not re-run on an ordinary deploy. Tile URLs carry a stamp
taken from the tile bytes, so a deploy that changes only the page leaves every
tile URL alone and costs a reader nothing.

## Working on a second machine

```
git clone https://github.com/Tobiasee16/SoppKompis
cd SoppKompis
uv sync                                    # or: pip install -e .
python scripts/fetch_recent_weather.py     # ~2 min, the only missing input
python scripts/build_hosted.py             # -> dist/, the Kristiansand build
```

The **national** build additionally needs the zone and mask tiles under
`data/processed/zone_tiles/`, which are not committed — they are 180 MB and
come from an overnight prediction run. A fresh clone builds the Kristiansand
app; rebuilding the national one means re-running the pipeline.

The model surface (`payload.js`) and the marked routes are committed, because
regenerating the first one means 31 GB of rasters and an overnight pipeline
run. So a clone builds the app without downloading anything but the weather.

**Two things deliberately do not come across.**

`config/supabase.json` holds the project URL and anon key. Copy
`config/supabase.example.json` and fill it in, or leave it out entirely — the
app is fully usable without sync, it just keeps saved places on the one device.

`data/processed/conditions.json` is the daily rainfall layer. It changes every
day, so committing it across two machines would generate nothing but conflicts.
`fetch_recent_weather.py --if-new` is two minutes and safe to run on a timer.

Everything else — the rasters, the GeoTIFFs, the built HTML — stays out. The
generated *reports* under `data/processed/` are committed, because they are the
evidence for every number in FINDINGS.md.

## Reading order

| | |
|---|---|
| [FINDINGS.md](FINDINGS.md) | the running log: every result, including the ones that went the wrong way |
| [EXPANSION.md](EXPANSION.md) | what a national map would cost, measured rather than guessed |
| [data/SOURCES.md](data/SOURCES.md) | every endpoint, access date and licence, plus the bugs each one has |
| [backend/DECISION.md](backend/DECISION.md) | why the backend is what it is |
| [web/README.md](web/README.md) | deploying |
| [ATTRIBUTION.md](ATTRIBUTION.md) | the data licences, and what they require of you |

## Where it stands — September 2026

**Deployed and verified live**: the national map, the 25 km neighbourhood
ranking, the AR50 habitat mask cut at 32 m, place and address search, private
saved spots with Supabase sync, and an update banner so a reader is told when
the page they are looking at has been replaced.

**Waiting on the owner**, and nothing else can move it:

| | |
|---|---|
| `backend/migrations/004_schedule_tombstone_purge.sql` | not yet run. The privacy policy promises a deleted spot is gone within 30 days and nothing removes it until this is scheduled. |
| Supabase → Redirect URLs | must contain `https://soppspor.pages.dev/`, trailing slash included, or magic links bounce to localhost. |
| Custom SMTP | Supabase's built-in mailer does 2 messages an hour to team addresses only, so account recovery does not work for anyone else. See `backend/FIXING_SUPABASE.md` §4. |

**Waiting on the owner's judgement**, deliberately not settled alone:

- The neighbourhood radius is 25 km, chosen by a sweep on the real rasters
  (FINDINGS 25i). 45 km left Kristiansand at a median of 72; 25 km fills all
  five bands.
- With several species drawn at once the band fills thin as `1/sqrt(n)`. Whether
  that still reads clearly at four species is a question about how it looks in
  the hand.
- Om describes the rings as the top 30 / 15 / 5 % of the ground around you.
  Measured over Kristiansand they are 37.8 / 11.9 / 1.0 %. The top ring is
  rarer than advertised — conservative, but the copy states the numbers flatly.

**Not started**: no native wrapper exists. There is no `ios/` directory and no
Capacitor. Testers install it from the browser; the App Store is a separate
piece of work needing macOS, an Apple Developer account and a guideline 4.2
argument.

## Current state

| | |
|---|---|
| Record counts | done — [data/processed/record_counts.md](data/processed/record_counts.md) |
| Pre-registered predictions | recorded, [FINDINGS.md](FINDINGS.md) |
| Spatial block CV harness | done — blocked + buffered, self-tested |
| Harness self-test | passing, 3 scenarios — [scripts/harness_selftest.py](scripts/harness_selftest.py) |
| Exclusion buffer | 4 km, a declared trade-off — [data/processed/autocorrelation_range.md](data/processed/autocorrelation_range.md) |
| Target-group background | built — 82k Agder fungal records, [data/processed/background.md](data/processed/background.md) |
| Environmental layers | 7/7 endpoints verified — [data/processed/env_source_verification.md](data/processed/env_source_verification.md) |
| Terrain rasters | Agder 32 m: slope, aspect, curvature, TWI |
| Climate | autumn rainfall climatology, 1 km, 1991–2020 — [data/processed/climate_raster.md](data/processed/climate_raster.md) |
| Forest structure | SR16 16 m, 8 products, in the stack |
| Substrate | NGU løsmasser → 10 groups at 32 m — [data/processed/losmasser.md](data/processed/losmasser.md) |
| Feature stack | 31 features — terrain, climate, SR16, substrate |
| Blocked CV | done — [data/processed/model_results.md](data/processed/model_results.md) |
| Transferability | done — Agder → Kristiansand, [data/processed/transferability.md](data/processed/transferability.md) |
| Boyce index | published implementation found biased; both reported — [data/processed/boyce_diagnosis.md](data/processed/boyce_diagnosis.md) |
| Suitability maps | 16 m GeoTIFF + PNG — [data/processed/suitability_maps.md](data/processed/suitability_maps.md) |
| Red-list suppression | **withdrawn as a requirement** in September 2026; `src/features/suppress.py` stays in the tree unused |
| Land cover (AR5) | blocked — needs a Geonorge FKB login |

### Headline numbers

Blocked CV on Agder, then fitted on Agder-minus-Kristiansand and evaluated once
on Kristiansand. Registered ordering was *tubaeformis > cibarius > muscaria*.

The three species below the rule were added in September 2026 for the product.
They run the identical pipeline but were never part of the registered ordering
test, and do not change its verdict — see [FINDINGS.md](FINDINGS.md) section 13.

| species | blocked AUC (maxent) | transfer AUC (maxent) | held-out records |
|---|---|---|---|
| *Craterellus tubaeformis* | 0.705 | 0.626 | 53 |
| *Cantharellus cibarius* | 0.690 | **0.737** | 45 |
| *Amanita muscaria* | 0.658 | 0.686 | 47 |
| synthetic null | 0.492 | 0.496 | 48 |
| — | | | |
| *Craterellus cornucopioides* | **0.766** | 0.662 | 17 |
| *Boletus edulis* | 0.695 | 0.613 | 35 |
| *Suillus luteus* | 0.653 | 0.704 | 22 |
| *Leccinum versipelle* | 0.574 | 0.709 | 16 |
| *Hydnum repandum* | 0.666 | 0.519 | 7 |

Better than chance, consistently, with a clean null throughout. **The predicted
ordering passes under blocked CV on one model and fails under transfer on
both.** See [FINDINGS.md](FINDINGS.md) section 9.

*H. repandum* does not clear the null on transfer (0.519 against 0.496, on
seven held-out records) and so is **not shown in the interface**. It stays in
every report and artefact with its number printed; the rule and its rationale
are in section 13. Where blocked and transfer disagree sharply — *L.
versipelle* most of all — the blocked figure is the better-supported one.

## Running

```
python -m pytest tests/ -q
python scripts/report_counts.py          # cached; --refresh to re-download
python scripts/check_cv_blocks.py        # fold adequacy on the real counts
python scripts/harness_selftest.py       # proves the harness catches leakage
python scripts/derive_buffer.py          # spatial dependence and the buffer trade-off
python scripts/report_background.py      # target-group background + null control
python scripts/verify_env_sources.py     # checks all 7 environmental endpoints
python scripts/build_terrain.py          # DTM download + slope/aspect/curvature/TWI
python scripts/download_rasters.py       # SR16 + løsmasser bulk download
python scripts/build_climate.py          # seNorge autumn rainfall climatology
python scripts/build_losmasser.py        # NGU polygons → 10 groups on the terrain grid
python scripts/check_boyce.py            # is the Boyce index measuring the models or itself?
python scripts/run_models.py             # blocked CV, 8 species + null, both models
python scripts/run_transfer.py           # fit on Agder, evaluate once on Kristiansand
python scripts/build_maps.py             # 16 m suitability GeoTIFF + PNG
```

`harness_selftest.py` must pass before any real model result is interpreted.
It exits non-zero if the validation scheme fails to distinguish a
location-memorising model from a genuine signal.

Everything up to and including the target-group background runs on the
standard library plus PyYAML. The wider geospatial stack in `pyproject.toml`
was installed on 2026-09-09 for the topographic wetness index and the raster
map export; the seven environmental endpoints themselves are reachable with
the standard library alone.

## Layout

```
config/      study area extents, species codes, CV params
data/
  raw/       untouched downloads (gitignored)
  interim/   reprojected, clipped
  processed/ model-ready outputs
  SOURCES.md every URL, access date, licence
src/
  ingest/    Artskart, boundaries, environmental point queries
  features/  thinning, target-group background, terrain derivatives, TWI
  features/  + substrate grouping, red-list suppression
  model/     maxent-style + lightgbm, grid prediction
  validate/  spatial block CV, exclusion buffer, held-out extent, AUC and Boyce
scripts/     runnable entry points
tests/
```

## Non-negotiables

These are the rules the project is held to. They are restated here because
they are easy to erode one convenient exception at a time:

- **No random train/test splits on occurrence data.** Spatial block CV,
  5–10 km blocks. Any metric from random CV is labelled untrustworthy in output.
- **Blocking alone is not enough.** Use the exclusion buffer. Measured on
  synthetic data with no ecology, unbuffered 7.5 km blocking still returned
  AUC 0.66 from edge leakage.
- **The buffer is not sufficient either, and is not a derived quantity.** These
  records have no autocorrelation range: dependence declines smoothly out to
  30 km without a floor. At the configured 4 km, a third to a half of it
  survives. Blocked numbers here are optimistic, and must be read next to the
  null control and the blocked-versus-random gap, never alone.
- **The synthetic null runs on every model run.** If it scores meaningfully
  above 0.5 on blocked CV, background sampling is broken and nothing else in
  that run means anything. Null presences and background are drawn *disjointly
  from the same target-group pool*, so 0.5 is the answer by construction.
- **Background is never uniform.** Fungal records touch under 5% of Agder's
  square kilometres; uniform background would let a model score well by
  learning visited-versus-unvisited ground alone.
- **Never fabricate data.** A failed download or a changed endpoint is a stop
  and a report, not a placeholder raster.
- **EPSG:25833 everywhere**, reprojected on ingest, never mid-pipeline.
- **Training and prediction extents stay separate.** Agder trains, Kristiansand
  validates. That is a transferability test, not just a bigger training set.
- **No red-to-green colour ramps.** Single-hue sequential only. The maps use a
  blue light→dark ramp; red/green would read as danger/safe, which is exactly
  the wrong signal for a mushroom product.
- **Suppression is no longer a constraint.** This file used to require every
  raster to leave through `src/features/suppress.py`, blurring output near
  red-listed habitat. The owner withdrew that requirement in September 2026 and
  called the in-app blur option "stupid". The module stays in the tree unused.
  Do not reintroduce it or treat it as live — read the current `CLAUDE.md`
  rather than assuming the constraint list is what it once was.
- **Two Boyce columns, always.** The published equal-width implementation
  returns about −0.42 for a model with no skill on scores this skewed, so its
  sign cannot be read alone.
- **Ask before adding a dependency.**

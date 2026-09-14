# What it would cost to map the rest of Norway

Costing only. Nothing here has been started. Written 2026-09-12, from measured
sizes and timings on this machine rather than from estimates — every figure
below is either read off disk or scaled from a run that actually happened, and
the ones that are extrapolated say so.

The short version: **the modelling is affordable and the delivery format is
not.** Those are separable problems and only one of them is hard.

---

## 1. The occurrence data is already here, and it argues for doing this

`data/raw/artskart/*.json` holds **national** pulls. Agder is applied as a clip
at model time, not at download time. So the national training sets need no new
download at all:

| species | national, precision-filtered, thinned at 1 km | what we fit on now (Agder) | ratio |
|---|---|---|---|
| kantarell | 3,505 | 396 | 8.9× |
| rød fluesopp | 3,148 | 260 | 12.1× |
| steinsopp | 2,498 | 205 | 12.2× |
| rødskrubb | 2,357 | 126 | 18.7× |
| traktkantarell | 2,302 | 248 | 9.3× |
| smørsopp | 1,727 | 150 | 11.5× |
| svart trompetsopp | 670 | 136 | 4.9× |
| blek piggsopp | 669 | 89 | 7.5× |

The bottom row matters. *H. repandum* was cut from the interface for failing to
clear the null control on seven held-out Kristiansand records. Nationally it has
669 thinned presences — more than **any** species currently has in Agder. A
national refit is the only honest route to bringing it back.

## 2. Nothing we need is access-restricted

Checked against the Geonorge catalogue, 2026-09-12:

| layer | access |
|---|---|
| SR16 Skogressurskart 16×16 m, raster | no restrictions |
| AR50 Arealressurskart | no restrictions |
| NGU Løsmasser | no restrictions |
| DTM 10 Terrengmodell (UTM33) | no restrictions |
| Naturbase naturtyper | no restrictions |

The only restricted thing nearby is FKB-AR5, which we already knew about and
already do without. **There is no licensing wall and no Norge digitalt account
needed.**

## 3. The modelling side is a week, not a quarter

The feature stack already covers **all of Agder** — 6,633 × 6,708 cells at 32 m,
45,562 km². Norway's mainland is 323,808 km², so this is a **7.1× scale-up**, not
the 111× that the *display* window faces.

### Storage

**Corrected 2026-09-13 against the actual downloads.** The estimate below was
built by scaling Agder by land area, 7.1x. That was the wrong multiplier and
SR16 came in at **2.6x the predicted figure**.

| | Agder now | predicted (7.1x) | **measured** |
|---|---|---|---|
| SR16 raw, all 23 layers | 1.85 GB | 13.1 GB | **34.77 GB** |
| SR16, the 8 layers actually fitted | 0.62 GB | - | **11.81 GB** |
| AR50 raw | 0.25 GB | 1.8 GB | **0.83 GB** |
| Løsmasser raw | 0.05 GB | 0.4 GB | **0.88 GB** |
| Artskart background | 0.15 GB | 2.5 GB | in progress |

Why the miss: **area is not what a raster covers, its bounding box is.** Agder
is compact and its land fills essentially 100% of its bounding box. Norway is
long, thin and diagonal - mainland land area is 323,808 km2 inside a bounding
box of 1,221 x 1,537 km = 1,877,062 km2, so **land fills 17% of it**. The bbox
ratio is 41.2x where the area ratio is 7.1x, and the measured 18.8x sits
between them: nodata compresses well but is not free.

Two things follow. Scale a raster estimate by bounding box, not by area, and
treat the result as an upper bound. And take only the layers that are actually
fitted - SR16's other fifteen layers are 23 GB of disk holding variants of
columns already in the stack (see `scripts/extract_sr16.py`).

Everything still fits comfortably: about 49 GB of raw downloads against 150 GB
free when this started.

Plus one genuinely new download: the target-group background. All fungal records
in Agder is 82,129 rows and 0.15 GB; nationally it is 1,325,029 rows and about
**2.5 GB**. It streams to JSON Lines, so it is slow rather than difficult.

### Compute

Measured: `build_maps.py` predicted the Kristiansand 16 m grid — 11.4 M cells —
at about **1.5 minutes per species**.

| prediction grid | cells | seven species |
|---|---|---|
| Norway at 16 m | 1,265 M | **≈ 19 hours** |
| Norway at 32 m | 316 M | **≈ 5 hours** |

Model fitting and blocked CV scale with the number of presence and background
rows rather than with area. The national background pool is roughly 16× Agder's,
so the current ~25-minute run becomes several hours, not days.

Everything here is an overnight job on one machine. None of it needs a cluster.

#### CORRECTION, 2026-09-14: the arithmetic above mixed two different cells

The table divides by **total grid cells** and multiplies by **scored cells**,
which are not the same thing and differ by a factor of four.

Kristiansand's grid is 11.4 M cells but only **2.74 M were ever scored** — the
rest are sea and neighbouring kommuner, masked out and never handed to a model.
So the measured Agder rate was about 30,400 scored cells per second, not the
126,000 that 11.4 M ÷ 90 s implies. The "Norway at 32 m, 316 M" row, meanwhile,
is already a *land-only* figure — the measured national mask holds 317.3 M
cells, so that part was right.

Corrected against the same rate, Norway at 32 m was really **≈ 20 hours** for
seven species, not five. The first national run projected 18.6 hours and
confirmed it.

**The five-hour figure turned out to be right anyway, for a different reason.**
`_nearest_by_block` grouped points with a Python-level loop over every point,
which is invisible at 67,000 training points and dominant at 4.2 M grid cells
per block. Vectorising it took the same strip from 98 s to 28 s with
byte-identical output, and the national prediction now measures **36 minutes
per species, 5.3 hours for nine sets** — eight species plus the null, where the
table above costed seven.

Two lessons worth keeping, since this document exists to be trusted:

* **State which cells a rate is per.** A prediction rate quoted per grid cell
  and applied to land cells is out by whatever fraction of the grid is masked,
  and that fraction is not a constant between regions.
* **An estimate that lands on the right answer can still be wrong.** Had nobody
  measured, this would have read as a five-hour job that took twenty, and the
  optimisation that actually made it five would never have been looked for.

## 4. The validation has to be redone, and it might not come out well

This is the part that is not just a bigger button press.

The current models are fitted on Agder, and that is load-bearing. Transfer from
Agder to *Kristiansand* — a subset of the training region — already costs
accuracy: *C. tubaeformis* falls from 0.705 blocked to 0.626 on transfer. Agder is
boreonemoral coastal southern Norway. Predicting Finnmark, Lofoten or the high
mountains from that fit is extrapolation far outside the fitted envelope.

There is a specific reason to expect trouble rather than a general one.
**Elevation is the dominant feature in every model we have**, and FINDINGS
section 13 records the measured worry that in this landscape elevation is partly
a proxy for distance to sea and to settlement. Nationally that proxy gets
*stronger*: fungal records cluster in the populated lowlands, and a national
model has a 0–2,469 m range to exploit. A national fit could easily score
**better on paper and mean less**.

So a national map needs the whole apparatus re-run at national scale:

- blocked CV with the same 7.5 km blocks and 4 km exclusion buffer
- target-group background drawn from national fungal records
- the synthetic null alongside, as always
- **a held-out region chosen and written down in advance** — and it should be a
  region the model is likely to find hard, not an easy one. Trøndelag or
  Nordland would test the thing that is actually in doubt; another southern
  county would not.

The harness already does all of this. It has to be pointed at a bigger extent,
not rewritten.

### ANSWERED, 2026-09-14: it was run, and it came out well

Every item on that list happened, in that order. Nordland was registered as the
held-out region in FINDINGS section 16 before the national feature stack
existed. Full results are in FINDINGS section 22; the short version:

| | result |
|---|---|
| null, national blocked CV | 0.490 +/- 0.009 |
| null, transfer to Nordland | 0.514 [0.480-0.552] |
| registered ordering, blocked CV | **PASS** on both models |
| registered ordering, transfer | **PASS** on both models |
| *C. tubaeformis* on transfer | 0.600 → **0.808**, intervals not overlapping |

The specific worry above - that elevation is partly an accessibility proxy and
gets *stronger* nationally, so the fit could "score better on paper and mean
less" - was the right thing to worry about and was tested rather than argued
about. National AUC did rise, by a median of +0.07, exactly as section 16e
registered it would and for the extent reasons it gave. But **the gain is
uncorrelated with elevation**: across the eight species, the gain in elevation
importance predicts the gain in AUC at Spearman +0.024. Whatever produced the
improvement, it was not the model leaning harder on the suspect feature.

The one feature that *did* look like the answer - autumn rainfall, correlating
with the gain at +0.826 - was ablated out entirely, at a cost of 0.008 AUC on
blocked CV and 0.000 on transfer, with *C. tubaeformis* keeping first place on
both models and both harnesses. So no load-bearing conclusion rests on it
either. See FINDINGS 22h.

What this does **not** establish is that the national map is as trustworthy as
Kristiansand's. Phase 0's map was a prediction onto held-out ground over its
whole extent; the national map is fitted on fourteen fylker and drawn over
fifteen, so only Nordland is held-out and the rest is in-sample. The Nordland
number is what bounds trust in the other fourteen.

## 5. The delivery format is the hard stop

The interface is one HTML file with everything inlined, because a published
artifact runs under a content policy that blocks external images and network
calls. It has a **16 MB ceiling**. The current build is 14.77 MB:

| | |
|---|---|
| Basemap JPEGs, inlined | 10.41 MB |
| Suitability payload, 7 species at 128 m | 3.78 MB |
| Marked routes | 0.40 MB |
| Conditions | 0.07 MB |

The map window is 45 × 64 km. **Norway's mainland is 111 times that area.** Two
consequences, both fatal to the format:

**The basemap alone ends it.** 10.41 MB covers the current window. The same pixel
density nationally is over a gigabyte. No compression closes a 70× gap.

**The zones alone would also end it.** At the current 128 m grid, one species
costs 26.4 MB nationally — more than the entire page budget:

| grid | one species | seven species |
|---|---|---|
| 128 m (current) | 26.4 MB | 184.5 MB |
| 256 m | 6.6 MB | 46.1 MB |
| 512 m | 1.6 MB | 11.5 MB |
| 1000 m | 0.4 MB | 3.0 MB |

Only at 1 km do seven species fit — and a 1 km zone points at a valley rather
than at a stand of forest, which is not a product a forager can use.

### What replaces it

- **Basemap from Kartverket's tile service at runtime** instead of inlined
  JPEGs. Already proven: `scripts/serve_interface.py` serves a build where live
  Kartverket tiles work. It is only the artifact's content policy that blocks it.
- **Zone data fetched per region** rather than shipped whole — the same tiling
  logic the basemap uses.
- Page drops from 14.77 MB to roughly **4 MB**, and stops growing with area.

This is a deployment decision, not a modelling one, and it is worth doing on its
own merits: the conditions layer already refetches on every launch and that fetch
is CSP-blocked in the artifact too, so **the published link is frozen at whatever
weather was baked in at build time**. Hosting fixes the daily refresh as a side
effect.

### DONE, 2026-09-14: built, and it came in under the estimate

`scripts/export_zone_tiles.py` cuts the national surfaces into 256-cell tiles
on the 128 m display grid, and `scripts/build_hosted.py --national` assembles
the page around them. The interface fetches the ground it is looking at.

| | estimated above | built |
|---|---|---|
| page | ~4 MB | **0.22 MB** |
| zones, per species nationally | 26.4 MB at 128 m | 17.7 MB average, fetched in pieces |
| zones fetched at launch | - | 9 tiles, ~350 kB |
| basemap | Kartverket at runtime | as planned |

The page came in an order of magnitude under the estimate because the model
payload is no longer in it at all - only a tile index, eight 256-entry lookups
and the prose. It does not grow with area.

Two things the estimate did not anticipate, both in FINDINGS section 24:

* **The ranking is the hard part, not the bytes.** A percentile computed over
  whatever is on screen changes meaning as the user pans, so the tile byte is a
  national rank and the post-smoothing re-rank is a shipped lookup. Without
  that, the same wood drifts in and out of a zone while the map looks fine.
* **The page had to be rewritten, not just re-pointed.** Every sentence naming
  Agder or Kristiansand is replaced from the payload, with the numbers read out
  of the run reports. That was more work than the tiling.

What this does **not** deliver: zones above about 9,800 km2 on screen (overview
levels are unbuilt), the fresh rainfall layer nationally (seNorge is fetched
for Agder only), and the card's "why this cell" layers nationally.

### What hosting costs

| | |
|---|---|
| Static hosting (Cloudflare Pages, Netlify, GitHub Pages) | £0 — free tiers are far above what this needs |
| Kartverket tiles | £0 — CC BY 4.0, attribution already in the interface |
| Supabase (spots sync) | £0 — already set up, free tier |
| Domain | ~£10/year, optional |
| Custom SMTP for account recovery (Resend) | £0 up to 3,000 mails/month |

**The recurring cost of going national is a domain name.** The real cost is the
work.

---

## Suggested order, if it goes ahead

1. **Host the current app.** Live tiles, tiled zone data, real PWA install.
   Removes the ceiling permanently and fixes the daily weather refresh. Nothing
   about the model changes, so nothing can break scientifically.
2. **Register the national validation design** — held-out region, expected
   outcomes — in FINDINGS *before* fitting anything, exactly as the three-species
   ordering was registered.
3. **Download and build the national feature stack.** ~31 GB, an overnight job.
4. **Refit and revalidate.** Blocked CV, national target-group background, null
   control, transfer to the pre-registered held-out region.
5. **Expand the map outward** — Agder first, since it is the current training
   extent and needs no new model, then nationally once step 4 says whether the
   national fit is worth trusting.

Step 1 delivers something visible on its own and is a prerequisite for the rest.
Steps 2–4 can run in parallel with it and change nothing the user sees until
they are finished.

---

# Recommendation, if the goal is Norway for every Norwegian user

Added 2026-09-12, in answer to that question directly.

## The thing that changes the plan: most of the app is already national

The suitability zones are the only part of this product tied to Kristiansand.
Everything else a forager actually opens the app for works anywhere in Norway
the moment the basemap does:

| feature | national today? |
|---|---|
| Private saved spots, notes, sync | **yes** — no geography in it at all |
| GPS, distance and bearing to a spot | **yes** |
| Topographic basemap | **yes**, from Kartverket's tile service — only the inlined copy is local |
| Marked routes | **yes** — Turrutebasen is a national dataset; we fetch one bbox of it |
| Rainfall vs normal | **yes** — seNorge is a national 1 km grid |
| Offline use, install to home screen | **yes** |
| Suitability zones | **no** — Kristiansand only |

And per the project's own framing, the saved spots are the retention feature,
not the zones. The zones are what makes someone try the app; their own places
are why they open it again next September.

So **"available to all Norwegian users" does not require the national model.**
It requires hosting. Those are months apart in effort and the cheap one delivers
almost all of the value.

## What I would do

### 1. Host it, and ship nationally without national zones

Live Kartverket tiles, zone data fetched per region, real PWA install. A forager
in Tromsø gets a proper topographic map, the marked routes around them, the
rainfall picture for where they are, GPS, and their own private spot list that
syncs across their devices. That is a useful app on day one.

The zones stay Kristiansand-only and the app says so plainly rather than showing
a blank map with no explanation.

This is the whole of the work that has to happen before anyone outside Agder can
use this, and none of it risks the science, because none of it touches the model.

### 2. Grow zone coverage region by region, gated on validation

Do **not** fit one national model and paint the whole country blue. Elevation
dominates every model here and is partly a proxy for distance to sea and to
settlement; nationally, a single fit would look most promising exactly where
people already live and walk. That is both wrong and the kind of wrong that is
easy to mistake for success.

Instead, reuse the rule already in the codebase. Species ship only if the lower
bound of their transfer interval clears the synthetic null. **Apply the same rule
to regions:**

- Fit one national model — more data and a far wider environmental gradient than
  Agder gives.
- Validate it *per region* with the existing blocked-CV harness.
- Publish zones in a region only when its held-out AUC clears the null there.
- Show each region's own measured accuracy in the app, the way each species
  already shows its own.

This reuses machinery that exists, it keeps the honesty framing consistent, and
it produces a map that grows outward as the evidence does instead of arriving
everywhere at once on trust.

Pre-register the held-out region before fitting, and make it somewhere hard —
Trøndelag or Nordland, not another southern county. A model that transfers to
Nordland has learned ecology. One that only transfers to Vestfold has learned
that Norwegians live in the south.

### 3. What only shows up at national consumer scale

These are cheap to handle now and expensive to retrofit.

**Kartverket tiles: 10,000 calls per IP per 24 hours** on the open cache service
(`cache.kartverket.no/v1/wmts/`, CC BY 4.0, commercial use permitted). Per user
that is generous — a day's walking is a few hundred tiles. The risk is carrier-
grade NAT: thousands of users on one mobile operator can share an egress IP and
hit the ceiling collectively. Mitigations, in order: make the service worker's
tile cache aggressive and long-lived (it already exists), ship the low-zoom tiles
with the app so the common case never hits the network, and if it still bites,
talk to Kartverket — they have arrangements for this.

**Supabase free tier has a monthly-active-user ceiling.** Every user gets an
anonymous account on first run, so MAU tracks installs, not sign-ups. Find the
exact number before it is hit rather than after.

**GDPR.** Saved spots are personal location data about identifiable people. A
privacy policy, EU data residency and a working deletion path stop being good
manners and become law. The schema is most of the way there already — row-level
security, `export_my_spots()`, tombstones — but the policy text and the delete
path need to exist and be findable.

**Liability.** A national app used by beginners to decide where to go picking.
The two permanent constraints — never identify a species from a photograph,
never say whether anything is edible — stop being principles and become the
legal posture. Keep the Soppkontrollen pointer prominent; it is the correct
answer to "is this one safe" and it is free in season.

**Two people supporting a national app.** Support email, incident response, and
someone to answer when seNorge changes its endpoint on a Saturday in October.

## What I would not do

- **Not** fit a national model and ship zones everywhere on the strength of a
  single national AUC. The number would probably go up and mean less.
- **Not** wait for the national model before letting people outside Agder use
  the app. That is optimising the slowest, least necessary part.
- **Not** lower the zone resolution to make a national map fit the current
  format. A 1 km zone is not a place, it is a valley.

## What this does not fix

CLAUDE.md's "No absences, ever" still holds. More area means more presence
records, not better evidence: the model stays presence-only, the effort bias
stays, and the honest ceiling on AUC does not move because the map got bigger.
A national map would be a **wider** estimate, not a more trustworthy one, and it
should keep saying so on every screen.

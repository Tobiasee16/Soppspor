# Data sources

Every URL resolved and tested, with the date it was checked. Endpoints change.
Re-verify before trusting anything here, and update the access date when you do.

Status legend: **VERIFIED** = fetched successfully on the access date.
**NOT YET FETCHED** = listed in the plan, not yet touched by any code.

---

## Occurrence — Artskart (Artsdatabanken / GBIF-Norge)

| | |
|---|---|
| Resolved URL | `https://artskart.artsdatabanken.no/publicapi/api/observations/list` |
| Taxon lookup | `https://artskart.artsdatabanken.no/publicapi/api/taxon?term=<name>` |
| Access date | 2026-09-09 |
| Status | **VERIFIED** |
| Licence | CC BY 4.0 for most datasets; per-dataset attribution required. Confirm before any publication. |
| Ingest module | [src/ingest/artskart.py](../src/ingest/artskart.py) |

### Verified API behaviour — read before changing the ingest code

1. **The path is `/observations/list`.** Bare `/observations` returns HTTP 400
   for every parameter combination tried.

2. **Unknown query parameters are silently ignored.** This is the dangerous
   one. A misspelled filter returns an unfiltered count with no error and no
   warning, which looks exactly like a filter that worked. **Never add a filter
   without confirming it changes `TotalCount`.**

   A request carrying no filter at all returns HTTP 500.

3. **CORRECTION, later on 2026-09-09 — there *are* server-side spatial
   filters.** An earlier version of this file said there were none. That was
   wrong, and it was wrong because of point 2: the parameters are namespaced
   under `filter.`, and the bare names tried first (`Counties`, `County`,
   `Fylker`, `Municipalities`, `BBox` …) were silently ignored for that reason
   rather than because no such filter existed.

   The real parameter list is published and should have been read first:

   > https://artskart.artsdatabanken.no/publicapi/swagger/docs/v1

   Useful parameters it documents: `filter.taxons`, `filter.taxongroups`,
   `filter.countys`, `filter.areas`, `filter.wktPolygon`,
   `filter.bounds.leftBottom` / `.rightTop`, `filter.fromDate` / `.toDate`,
   `filter.includeChildTaxons`, `filter.basisOfRecords`.

   Verified to change `TotalCount`, and cross-checked against our own
   client-side clip of the national pull — **the two agree exactly**, which
   validates the boundary geometry and the point-in-polygon code as much as it
   validates the filter:

   | Query | Records | Our polygon clip |
   |---|---|---|
   | `filter.taxons=39039` | 4277 (national) | — |
   | `filter.taxons=39039&filter.countys=42` | 380 | 380 |
   | `filter.taxons=39039&filter.areas=4204` | 85 | 85 |

   The per-species pulls already on disk were national-plus-clip and remain
   valid; they are simply larger than they needed to be.

4. **The `crs` parameter does not work and must not be used.** `crs=25833`
   returns `East`/`North` as `null`; `crs=EPSG:25833` returns integer-rounded
   coordinates while still reporting `Projection: 32633`. Coordinates are taken
   as delivered and the datum note in point 6 applies.

5. **Counts drift day to day.** This is a live database. *Craterellus
   tubaeformis* read 4276 national in the morning of 2026-09-09 and 4277 in the
   afternoon. Record the access date with any count; a small change is normal
   and is not corruption.

6. **Paging** is `PageIndex` (0-based) and `PageSize`. Both honoured. We use
   `PageSize=1000`.

7. **Coordinates**: `East`/`North` are floats with `Projection: 32633`
   (WGS84 / UTM 33N), *not* 25833 (ETRS89 / UTM 33N). The datum difference in
   Norway is roughly half a metre and grows with plate motion. We treat the
   values as 25833 because the offset is immaterial at our thinning distances
   (250–1000 m) and below the 16 m raster cell. It is recorded here rather than
   hidden, and it must not be cited as sub-metre accurate.

   Confirms the note in CLAUDE.md: Artskart returns UTM33 even for zone-32
   localities (an Ålesund record came back as E 75 469, N 6 959 686).

5. **`Latitude`/`Longitude` are strings with a comma decimal separator**
   (`"62,523116"`). Use `East`/`North` instead.

6. **Records are duplicated per administrative unit.** An observation whose
   uncertainty circle straddles a boundary is returned once per intersected
   municipality/county, with identical coordinates and differing
   `Municipality`/`County`. Verified on
   `urn:uuid:9ca0c111-2ad5-4de1-a611-bb7660ac3c12` (precision 1364 m, returned
   four times, listed under both Nordre Follo and Oslo). This is why
   `TotalCount` (6792) can be lower than the number of rows paged (6793).
   `usable_points()` deduplicates on `Id`. Without that, coarse records would
   carry several times the weight of precise ones.

### Resolved taxon IDs (2026-09-09)

| Species | Artskart TaxonId | GBIF usageKey | National records (Artskart) | National records (GBIF, country=NO) |
|---|---|---|---|---|
| *Craterellus tubaeformis* | 39039 | 2554536 | 4276 | 4409 |
| *Cantharellus cibarius* | 160585 | — | 6792 | 6831 |
| *Amanita muscaria* | 199186 | — | 5869 | 5922 |

GBIF counts are a cross-check only, not a second ingest path. They run 1–3%
above Artskart, consistent with GBIF holding some records Artskart does not
surface through this API. Not investigated further; not load-bearing.

### Species added for the product (2026-09-12)

Fitted, scored and mapped through the same pipeline, but outside the
pre-registered three-species ordering test — see the header comment in
`config/species.yaml`. Agder is the training extent, Kristiansand the
prediction extent; both counts are server-side (`filter.countys=42`,
`filter.areas=4204`) on the access date.

| Species | Norwegian | TaxonId | National | Agder | Kristiansand |
|---|---|---|---|---|---|
| *Boletus edulis* | steinsopp | 96845 | 4565 | 330 | 52 |
| *Craterellus cornucopioides* | svart trompetsopp | 146812 | 1157 | 237 | 34 |
| *Leccinum versipelle* | rødskrubb | 38817 | 4140 | 175 | 21 |
| *Suillus luteus* | smørsopp | 38895 | 2995 | 220 | 37 |
| *Hydnum repandum* | blek piggsopp | 219592 | 1309 | 121 | 8 |

Considered and rejected on the same day, recorded so the decision is not
quietly revisited:

| Species | Why not |
|---|---|
| *Albatrellus ovinus* (fåresopp) | 117 in Agder but **0 in Kristiansand**. A suitability map for a species never recorded in the prediction extent has nothing to validate against. |
| *Macrolepiota procera* (parasollsopp) | 31 in Agder, below the 50-record advisory floor. |
| *Hydnum rufescens* (rødgul piggsopp) | 98 / 7, and routinely recorded as *H. repandum*; the pair is not cleanly separated in the source. |

### `TotalCount` is not a completeness check (measured 2026-09-12)

The earlier note above recorded that paged rows can *exceed* `TotalCount`. The
reverse also happens, and the endpoint is internally inconsistent in three ways
at once. Measured on *Boletus edulis* (taxon 96845), identically on three
consecutive attempts:

| | |
|---|---|
| `TotalCount` | 4565 |
| Page sizes returned for `PageSize=1000` | 1000, 1005, 1000, 994, 563 |
| Rows paged | 4562 |
| Unique `Id` values among them | 4557 |

So `PageSize` is a request rather than a promise, the paged rows total three
fewer than the reported count, and five of those rows are the administrative
fan-out already documented above. *Cantharellus cibarius* shows the same shape
in the other direction: 6800 reported, 6801 rows, 6795 unique.

Consequence for the ingest code: `len(rows) == TotalCount` cannot be the
completeness assertion — it fails on healthy downloads — and a fixed page size
cannot be used to detect a dropped page either. `download_taxon` now fails on
an empty page before the last (the signature of a genuinely lost page, which
server-side miscounting does not produce) and on unique records falling more
than 2% below `TotalCount`. All three figures are written into the raw file
rather than reconciled.

The three original species were checked for fan-out inside the Agder extent
after this was found: **zero duplicated `Id` values** in any of them, so no
published figure changes.

---

## Marked routes — Kartverket Turrutebasen (WFS)

| | |
|---|---|
| Endpoint | `https://wfs.geonorge.no/skwms1/wfs.turogfriluftsruter` |
| Request | `service=WFS&version=2.0.0&request=GetFeature&typeNames=<type>&srsName=urn:ogc:def:crs:EPSG::25833&bbox=<extent>,urn:ogc:def:crs:EPSG::25833` |
| Feature types | `app:Fotrute`, `app:Sykkelrute`, `app:AnnenRute` |
| Accessed | 2026-09-11 |
| Licence | CC BY 4.0, Kartverket. Attribution shown in the interface. |
| Script | `scripts/fetch_trails.py` |
| Raw | `data/raw/turrutebasen/*.gml` (17.4 MB) |
| Product | `data/interim/trails.json`, 5804 routes, 60,986 vertices, 395 kB |

Used for display only. Nothing from this layer is a model feature, and it must
not become one: marked paths are a direct measure of where people walk, so
feeding them to a presence-only model would reintroduce exactly the effort bias
the target-group background exists to remove.

Verified behaviour, worth keeping:

* **GML 3.2 only.** The service does not offer GeoJSON; the response is parsed
  as GML.
* **Axis order is easting-first** with `srsName=urn:ogc:def:crs:EPSG::25833`,
  confirmed by checking the decoded centroid falls over Kristiansand rather
  than in the sea. `fetch_trails.py` refuses to write the file if it does not,
  because a northing-first swap produces a plausible-looking network in the
  wrong place rather than an error.
* **A bbox filter returns whole features that intersect the box**, not features
  clipped to it. Routes crossing the edge run up to 5 km past the extent. This
  is harmless here — panning is clamped well inside — but an early version of
  `tests/test_trails.js` asserted all geometry lay within the extent and failed
  on correct data. The test was wrong, not the data.

Simplified with Douglas–Peucker at 1 m, which removes 74% of vertices
(237,016 → 60,986) and stays well inside the width of an actual marked path.
Packed as int32 start points plus int16 deltas at half-metre units.

## Target-group background — Artskart, all fungi in Agder

| | |
|---|---|
| Query | `observations/list?filter.taxongroups=9&filter.countys=42` |
| Access date | 2026-09-09 |
| Status | **VERIFIED, FETCHED** |
| Stored | `data/raw/artskart/target_group_sopper_agder.jsonl` (gitignored, ~130 MB) |
| Format | JSON Lines, one observation per line, plus a `.meta.json` sidecar |
| Ingest | `artskart.download_filtered()` / `artskart.iter_jsonl()` |

`filter.taxongroups=9` is Sopper (fungi). Counts on the access date:

| Extent | Records |
|---|---|
| Norway | 1,325,029 |
| Agder (`filter.countys=42`) | 82,129 |
| Kristiansand (`filter.areas=4204`) | 11,668 |

Streamed to JSON Lines rather than assembled into one JSON object: the file is
around 130 MB, a line-per-record keeps memory flat in both directions, and a
truncated file is visible on inspection instead of being a silently short
array. The download refuses to leave a partial file behind — a short read
deletes it and raises, because a partial background set would bias every model
built on it without ever looking wrong.

This is the effort surface required by CLAUDE.md hard constraint 3, and the
same pool supplies the synthetic null of hard constraint 5. See
[data/processed/background.md](processed/background.md).

---

## Administrative boundaries — Kartverket via Geonorge

| | |
|---|---|
| Kommune | `https://ws.geonorge.no/kommuneinfo/v1/kommuner/4204/omrade?utkoordsys=25833` |
| Fylke | `https://ws.geonorge.no/kommuneinfo/v1/fylker/42/omrade?utkoordsys=25833` |
| Access date | 2026-09-09 |
| Status | **VERIFIED** |
| Licence | NLOD / CC BY 4.0 (Kartverket) |
| Ingest module | [src/ingest/boundaries.py](../src/ingest/boundaries.py) |

`utkoordsys=25833` is honoured — geometry arrives already in ETRS89 / UTM 33N,
so nothing is reprojected. The response declares its CRS in
`omrade.crs.properties.name`; the ingest code asserts on it rather than
assuming.

Resolved extents (EPSG:25833 bounding boxes, metres):

- Agder fylke (42): `(-21809, 6426048, 190404, 6640698)`
- Kristiansand kommune (4204): `(62544, 6428946, 107684, 6493405)`

Kristiansand 4204 is the post-2020 merged kommune. The API confirms
`fylkesnummer: 42`, `fylkesnavn: Agder`. Former Søgne (1018) and Songdalen
(1017) are included; no separate query is needed.

Note the negative easting on the Agder bbox — the western edge of Agder falls
west of the UTM 33 central meridian, which is expected and not an error.

---

## Environmental layers — all verified 2026-09-09

Every endpoint below was probed live against a **known location**: a real
*Craterellus tubaeformis* record at `(93674.87, 6466470.01)` EPSG:25833 in
inland Kristiansand, which the elevation service independently confirms is
forest at 38 m. Checking against a point where a fungus actually grew matters,
because several of these services answer an out-of-coverage or malformed query
with something that looks like data rather than an error.

Re-verify with `python scripts/verify_env_sources.py`, which asserts on content
rather than HTTP status and exits non-zero on any failure. Client:
[src/ingest/env.py](../src/ingest/env.py).

| Layer | Endpoint | Status | Verified value at the probe point |
|---|---|---|---|
| Terrain elevation | `ws.geonorge.no/hoydedata/v1/punkt` | **VERIFIED** | 38.05 m, source `dtm1`, terrain `Skog` |
| Forest structure | `wms.nibio.no/cgi-bin/sr16`, layer `SRVVOLMB` | **VERIFIED** | volume 300.5 m³/ha [248.5–352.5], species 2, age 106.6 yr, LAI 514.3 — 74 fields |
| Land cover | `wms.nibio.no/cgi-bin/ar5`, layer `Arealtype` | **VERIFIED** | artype 30 Skog, Barskog, bonitet Middels, grunnforhold Jorddekt |
| Superficial deposits | `geo.ngu.no/mapserver/LosmasserWMS2`, layer `Losmasse_flate` | **VERIFIED** | type 100, "Tynt dekke av organisk materiale over berggrunn" |
| Habitat types (NiN) | `kart.miljodirektoratet.no/arcgis/rest/services/naturtyper_nin/MapServer/0/query` | **VERIFIED** | no polygon at this point, which is the normal case |
| Weather | `thredds.met.no/thredds/dodsC/senorge/seNorge_2018/Archive/seNorge2018_{year}.nc` | **VERIFIED** | rr 0.0, 0.0, 3.71, 1.89, 16.85 mm; tg 19.9→12.1 °C, Sept 2024 |
| Soil moisture / hydrology | `gts.nve.no/api/GridTimeSeries/{x}/{y}/{from}/{to}/{theme}.json` | **VERIFIED** | `gwb_sssrel` 45, 44, 44, 48, 48 % |

**All seven are reachable as point queries using the standard library alone.**
No raster tooling is needed to build the feature stack for presence and
background points. See "What still needs a dependency" below for what is not
covered by that.

### Traps, each found the hard way

1. **WMS axis order.** In WMS 1.3.0 with EPSG:25833 the BBOX is easting-first.
   Reversed, the query lands in the sea off Sweden and MapServer answers
   `Search returned no results` — indistinguishable from a real location with
   no data. We use **WMS 1.1.1**, where the order is unambiguously x,y.

2. **INFO_FORMAT.** `text/plain` is accepted and returns a feature ID with **no
   attributes whatsoever**, which reads as a successful query on an empty
   feature. Only `application/vnd.ogc.gml` carries the values.

3. **SR16: use the vector layer.** The raster layers (`SRRTRESLAG`,
   `SRRBONITET`, `SRRHOYDEO`, `SRRTREANTALL`) return nothing through
   GetFeatureInfo. The vector layer **`SRVVOLMB`** returns the entire stack in
   one request — and every variable carries `_s`, `_l`, `_u` uncertainty
   bounds, which is exactly what CLAUDE.md asks us to propagate rather than
   hide.

4. **NGU.** `Losmasse_flate` is queryable; `Losmasse_Norge` is a group layer and
   `Losmasseflate_detaljkart_full` returns nothing. The older
   `mapserver/LosmasserWMS` (no `2`) responds 200 with **zero layers** — dead,
   not broken.

5. **seNorge is OPeNDAP, not NetcdfSubset.** The NCSS service returns HTTP 503.
   Square brackets in the OPeNDAP constraint must be percent-encoded or the
   server returns HTTP 400.

6. **The seNorge Y axis descends.** X runs from −74 500 upward, Y from
   7 999 500 **downward**, both at 1 000 m. Assuming Y ascends reads a cell
   about 1 500 km away, which returns the `-999.99` fill value rather than an
   error. Verified against the file's own coordinate arrays: for our probe
   point, `X[168] = 93500` and `Y[1533] = 6466500`.

7. **The seNorge fill value is −999.99.** Averaged in as a number rather than
   treated as missing, it would be catastrophic and would not look wrong.

8. **Elevation over water** is answered from `dybdekurver` (depth contours) as
   a negative height. A real answer to a different question. The client returns
   the source alongside the value so a caller can tell.

### seNorge does not cover all of Agder

The grid's southern edge is **Y = 6 450 500**, while Agder reaches down to
6 426 048 and Kristiansand to 6 428 946. Measured against our actual data:

| Set | Points | Outside the seNorge grid |
|---|---|---|
| Thinned presences, all three species | 904 | **0** |
| Target-group background pool | 3,882 | **5** (0.13%) |

Small, but not nothing. `senorge_index()` raises rather than clamping to the
nearest cell, because a clamped value there is real climate data from
somewhere else. Those points must be dropped or flagged, never filled.

Measured again 2026-09-09 against the terrain raster rather than against the
point sets, since a hole in the *prediction* extent would matter more than a
handful of dropped points:

| Extent | Land cells | Below the seNorge grid |
|---|---|---|
| Agder | 16,120,248 | 1,625 = 0.01% (2 km²) |
| Kristiansand | 632,297 | **0** |

The alarming-sounding boundary numbers above are outer skerries, which the sea
mask has already removed. There is no hole in the prediction extent.

### Bulk raster routes, resolved 2026-09-09

Point queries were verified first because they prove an endpoint answers with
real values. The rasters below are what the model actually consumes.

**Kartverket DTM, via WCS**

```
https://wcs.geonorge.no/skwms1/wcs.hoyde-dtm-nhm-25833
  coverage nhm_dtm_topo_25833, EPSG:25833, resampled server-side
```

Requested at 32 m in 9 tiles capped at 2500 px a side; the whole Agder extent
took 100 s. The server does the resampling, so a 1 m national DTM never has to
be moved. A short read deletes the partial file rather than leaving something
that looks like a finished raster.

**seNorge_2018, via OPeNDAP binary**

```
https://thredds.met.no/thredds/dodsC/senorge/seNorge_2018/Archive/seNorge2018_{year}.nc
  .dds   structure      rr[time][Y][X], Float32, kg/m² , _FillValue -999.99
  .das   metadata       "daily total precipitation (06UTC-06UTC)"
  .dods  binary data    ?rr[t0:1:t1][y0:1:y1][x0:1:x1]
```

X ascends from −74 500 to 1 119 500; **Y descends** from 7 999 500 to
6 450 500. Both are cell centres on a 1 km grid, 1195 × 1550. Reading Y as
ascending flips the country north–south and yields a raster that looks fine.

`.ascii` is used for point queries and is unusable in bulk — a 30-year
climatology over Agder is ~92 million values, over a gigabyte as text. `.dods`
is XDR: the DDS header, the literal bytes `Data:\n`, then per array two
big-endian `uint32` element counts followed by big-endian elements. Both counts
are checked against the requested shape, because reading past a mismatch
produces plausible-looking garbage rather than an error. 30 years took 284 s.

### What point queries did not cover — resolved 2026-09-09

Point queries reach every environmental endpoint, but two things needed real
raster tooling, and `rasterio` was added with explicit approval on 2026-09-09
for exactly these:

- **Topographic wetness index.** TWI needs flow accumulation over a DTM, which
  is a raster operation and cannot be assembled from point lookups. Resolved by
  pulling a DTM tile from `wcs.geonorge.no/skwms1/wcs.hoyde-dtm-nhm-25833`,
  resampled server-side to 32 m over Agder, and deriving slope, aspect,
  curvature and TWI in numpy. See `src/features/terrain.py`.
- **Phase 0 artefact 3**, the static suitability maps at 16 m exported as
  GeoTIFF and PNG.

Bulk raster download also spared NIBIO and NGU roughly 19,000 point requests.

---

## Bulk raster downloads — Geonorge / NIBIO / NGU, 2026-09-09

Ordered through the Geonorge download API and its NIBIO and NGU equivalents by
`src/ingest/geonorge_download.py`, driven by `scripts/download_rasters.py`.
Every order is Agder, EPSG:25833. Each download writes a `.meta.json` beside
the archive recording the resolved URL, byte count and timestamp, so the
provenance survives even though `data/raw/` is gitignored.

| Dataset | Resolved URL | Metadata UUID | Downloaded | Size |
|---|---|---|---|---|
| SR16 — Skogressurskart 16×16 m, raster | `https://kart8.nibio.no/uttak_Download/sr16raster/42_25833_sr16_RASTER.zip` | `5de45872-f534-4e97-840e-3cfd8db04398` | 2026-09-09T18:40:42 | 1,849,687,119 B |
| Løsmasser (NGU), SHAPE | `https://nedlasting.ngu.no/api/v2/download/order/7944616a-466c-4530-9a30-352b5b3dbec4/e3f2dff8-28a2-4e8a-9160-e68d5176ebfa` | `3de4ddf6-d6b8-4398-8222-f5c47791a757` | 2026-09-09T18:41:04 | 51,922,927 B |

**Licence:** both are Norsk lisens for offentlige data (NLOD) / CC BY 4.0 as
published on Geonorge. Attribution: NIBIO for SR16, NGU for løsmasser.

**The NGU order URL is single-use.** It is a resolved order against a
server-side basket, not a stable address, and it will not fetch anything a
second time. Re-downloading means placing a new order; the UUID is the durable
identifier, the URL is not. Recorded here because it is what was actually
fetched, not because it can be fetched again.

### SR16, as extracted

Eight of the raster products are used, all int16 with nodata −9999, all at
16 m in EPSG:25833, extracted to `data/interim/sr16/`:

`SRRTRESLAG` (tree species, 1 spruce / 2 pine / 3 deciduous), `SRRVOLMB`
(standing volume, with bands 2 and 3 carrying the lower and upper bounds),
`SRRGRFLATE` (basal area), `SRRMHOYDE` (Lorey's height), `SRRLAI`,
`SRRTREALDER` (stand age), `SRRKRONEDEK` (crown cover), `SRRBONITET` (site
index).

Absence is meaningful in every one of them: SR16 maps productive forest, so a
cell with no value is water, farmland, bog, bare rock or ground above the
forest line. That is true of 19% of the target-group pool and is encoded rather
than dropped — see `RasterLayer` in `src/features/stack.py`.

### Løsmasser, as extracted

Only `Losmasse/LosmasseFlate_20240621` (the polygon coverage) is used;
`LosmasseGrense` is a 116 MB boundary-line layer with no role here. Data
version 2024-06-21. The dbf is **latin1**, not UTF-8 — read as UTF-8 the
Norwegian class names come back mojibaked.

34,714 polygons carry 30 distinct `jordart` codes across Agder. These are
collapsed into ten depositional groups by `src/ingest/losmasser.py` and burned
onto the terrain grid by `scripts/build_losmasser.py`, giving
`data/interim/losmasser_agder_32m.tif` — uint8, 32 m, nodata 0, aligned
cell-for-cell with the terrain stack. Group shares reproduce the polygon area
shares to within 0.2 percentage points. Full breakdown:
[../data/processed/losmasser.md](../data/processed/losmasser.md).

---

## Layer status

**FETCHED** = pulled, reprojected where needed, and on disk in `data/interim/`.
**NOT YET FETCHED** = listed in the plan, not yet touched by any code.

| Layer | Source | Status | Notes |
|---|---|---|---|
| Forest structure | NIBIO SR16, via Geonorge / Kilden | **FETCHED 2026-09-09** | 16×16 m raster, 8 products. `data/interim/sr16/`. Tree species, volume (with bounds), basal area, Lorey's height, LAI, site index, stand age. Treat as suitability input, never ground truth — see limits below |
| Terrain | Kartverket høydedata, 1 m LiDAR DTM | **FETCHED 2026-09-09** | WCS, resampled server-side to 32 m over Agder. Slope, aspect, curvature and TWI derived. `data/interim/terrain_agder_32m.tif`. Kristiansand also held at 16 m: `data/interim/dtm_kristiansand_16m.tif` |
| Weather | MET seNorge_2018 via THREDDS/OPeNDAP | **FETCHED 2026-09-09** | Autumn rainfall climatology, 1 km. `data/interim/autumn_rain_agder_1km.tif`. NLOD |
| Superficial deposits | NGU løsmassekart | **FETCHED 2026-09-09** | 34,714 polygons → ten depositional groups at 32 m. `data/interim/losmasser_agder_32m.tif`. Calcareous vs. acidic substrate strongly structures fungal communities |
| Land cover | NIBIO **AR50** (open stand-in for FKB-AR5) | **FETCHED 2026-09-10** | AR50 at 1:50 000, NLOD. `data/interim/ar50_agder_32m.tif`. FKB-AR5 at 1:5 000 is **WILL NOT FETCH** - Norge digitalt restricted, see below |
| Soil moisture | NVE gridded HBV output | **NOT YET FETCHED** | 1 km soil moisture deficit, groundwater, runoff. Verified reachable as point queries; not pulled as a raster. Largely redundant with TWI plus the rainfall climatology, so low priority |
| Habitat types | Miljødirektoratet Naturbase (NiN naturtyper) | **NOT YET FETCHED** | Context layer, not a model feature. Needed for the red-list suppression policy rather than for prediction |



---

## FKB-AR5: closed as "will not fetch", not "not yet fetched"

Earlier revisions of this file recorded AR5 as blocked on a Geonorge login and
implied the blocker was a missing access route. It is not. Geonorge's own
metadata API answers the question directly:

```
GET https://kartkatalog.geonorge.no/api/getdata/166382b4-82d6-4ea9-a68e-6fd0c87bf788
  AccessIsOpendata   : false
  AccessIsRestricted : true
  DataAccess         : "Norge digitalt begrenset"
  OtherConstraints   : "Geovekst"
  licence            : https://www.kartverket.no/geodataarbeid/norge-digitalt/
                       partsinformasjon/avtaler-og-vilkar/norge-digitalt-lisens
```

Checked 2026-09-10. **This is a licensing restriction, not a technical one.**
There is no anonymous route to discover, and discovering one would mean using
data the project is not licensed for. The item is therefore closed as *will not
do* rather than left open as *not yet done*.

What was ruled out on the way, and why each is not a workaround:

- `wfs.nibio.no/cgi-bin/ar5` — the mapfile does not exist (`msLoadMap(): Unable
  to access file`). Neither do `ar5_wfs`, `arealressurs`, `ar5flate` or
  `arealressurskart`.
- WFS on `wms.nibio.no/cgi-bin/ar5` — the server answers, and refuses:
  `msWFSDispatch(): WFS server error. WFS request not enabled.`
- `wcs.nibio.no` — does not resolve.
- The AR5 **WMS** does work anonymously, and section 5's point query against it
  is verified. That is a public map service for looking things up. It is not a
  licence to pull the layer into a model and redistribute derived rasters, and
  19,000 GetFeatureInfo calls to harvest a licensed dataset would be both rude
  and a circumvention.

### AR50, the open substitute — FETCHED 2026-09-10

| | |
|---|---|
| Dataset | Arealressurskart - AR50 |
| Metadata UUID | `4bc2d1e0-f693-4bf2-820d-c11830d849a3` |
| Resolved URL | `https://kart8.nibio.no/uttak_Download/ar50/42_25832_ar50_gml.zip` |
| Licence | **Open data, NLOD** (`AccessIsOpendata: true`) |
| Size | 238.8 MB zipped, 888 MB GML, 183,956 polygons |
| Ordered as | GML, Agder (fylke 42) |

**Delivered in EPSG:25832, not 25833.** AR50 offers Agder only in 4258 and
25832, because Agder genuinely falls in UTM zone 32. Reprojected to 25833 on
ingest by `src/ingest/ar50.py` — the same zone-32 trap this file's CRS note
warns about, arriving from the opposite direction. Nothing downstream sees
25832.

**What the substitution costs:** a factor of ten in scale. AR50's minimum
mapping unit is far larger than AR5's, so small clearings, narrow bogs and
field edges are not represented. It answers "roughly what kind of ground is
this", never "where is the boundary".

**One code-list trap, checked rather than assumed.** AR50 codes agriculture as
`arealtype = 20`; AR5 uses `21`/`22`/`23` for agriculture and puts transport at
`12`. Reading 20 the AR5 way would have relabelled 5,363 agricultural polygons
in Agder as roads, and nothing would have failed. Verified against the
delivery: every polygon with arealtype 20 carries a `jordbruk` code (24
fulldyrka, 25 innmarksbeite) and no other arealtype does.

---

## Known data quality limits — carry these into the writeup

- **SR16**: published validation reports models explaining ~70% of variance with
  relative RMSE near 50%; independent assessment found tree species correct in
  roughly two-thirds of pixels, volume error 30–60% of the mean, site index off
  by 2–3 m. Suitability input, never ground truth. Propagate this uncertainty
  where feasible rather than hiding it.
- **seNorge**: effective resolution is coarser than the 1 km grid spacing, and
  quality degrades with local station density. Coastal Kristiansand is better
  instrumented than the inland valleys — do not assume uniform quality across
  the extent.
- **Artskart**: presence-only. There are no true absences. Red-listed species
  are published with restricted precision. Records are heavily effort-biased
  toward roads, trails and settlements — this is the single largest threat to
  the hypothesis and the reason target-group background sampling is mandatory.

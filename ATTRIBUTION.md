# Data sources and attribution

Every map layer, model input and tile in this project comes from a public
Norwegian source, and all of them require attribution. This file lists what
that obligation is, per source, for anyone running or deploying this.

Nothing here is optional. CC BY 4.0 and NLOD both make attribution a condition
of use, not a courtesy — and the attributions must survive into anything built
from this repository, not just live in it.

`data/SOURCES.md` is the fuller record: every resolved URL, access date, and
the specific quirks and bugs each endpoint has. This file is only the licence
summary.

---

## What must be shown to a user

The interface carries **©Kartverket** on the map itself, which is the one with
a per-view obligation, and names every source in its *Om kartet* page. If you
change the interface, that has to keep being true.

| shown | where |
|---|---|
| `Kartverket CC BY 4.0` | on the map, always visible |
| all sources, named | *Om kartet* → Kilder |

---

## The sources

### Kartverket — basemap tiles, terrain model, marked routes

**CC BY 4.0.** Attribution `©Kartverket`, with a link to kartverket.no where
possible. Commercial use is permitted.

- Background map tiles, `cache.kartverket.no/v1/wmts/1.0.0/` — fetched live by
  the app, at most 10,000 requests per IP per day
- DTM 10 terrain model — slope, aspect, curvature and topographic wetness index
  are derived from it
- Turrutebasen, via `wfs.geonorge.no` — the marked routes drawn on the map
- Administrative boundaries for Agder and Kristiansand
- AR50 land cover, via NIBIO/Geonorge — decides which ground is scored at all,
  and is drawn at 32 m as the habitat mask that cuts the zones
- Sentralt stedsnavnregister, `api.kartverket.no/stedsnavn/v1/` — answers the
  search field, called live from the browser
- Adresse-API, `ws.geonorge.no/adresser/v1/` — the same, for street addresses

### Artsdatabanken and GBIF Norway — occurrence records

**CC BY 4.0**, per-dataset. Artskart aggregates many contributing collections,
each with its own citation. The counts, coordinates and species identities that
every model here is fitted on come from them.

Red-listed species are published at deliberately reduced precision by the
source. That is their decision and it is respected rather than worked around.

### MET Norway — weather and climate

**NLOD** (Norsk lisens for offentlige data). seNorge_2018 via THREDDS/OPeNDAP:
the 1 km daily precipitation and temperature grid behind both the autumn
rainfall climatology in the model and the live conditions layer in the app.

### NIBIO — forest structure and land cover

**NLOD.** SR16 Skogressurskart at 16 m (tree species, volume, basal area,
Lorey's height, LAI, site index, stand age) and AR50 land cover at 1:50 000.

Note that FKB-AR5, the finer land cover product, is **not** used: it is
restricted to Norge digitalt participants and this project is not one. AR50 is
the open stand-in.

### NGU — superficial deposits

**NLOD / open data.** Løsmassekart, reduced to ten substrate groups on the
terrain grid.

### Miljødirektoratet — habitat types

**NLOD.** Naturbase naturtyper, used as a context layer.

---

## What this repository's own code is licensed as

**Not yet decided.** Without a licence file, default copyright applies: others
may read the code on GitHub but have no right to use, modify or redistribute
it.

That may well be what you want — it keeps every option open, including a
commercial one — but it should be a decision rather than an oversight, and it
is worth making before anyone finds the repository useful.

The data licences above are unaffected either way. They govern the data, not
this code, and they follow the data wherever it goes.

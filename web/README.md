# Deploying the hosted build

```
python scripts/export_web_preview.py      # only if the model was refitted
python scripts/fetch_recent_weather.py    # --if-new, safe on a timer
python scripts/build_hosted.py            # writes dist/
python scripts/serve_interface.py --dist  # check it locally first
```

`dist/` is a plain static directory. Nothing in it needs a server-side build
step, a runtime, or a database. Upload it and you are done.

| | |
|---|---|
| `index.html` | the whole app, ~4.4 MB |
| `conditions.json` | the daily rainfall layer, refetched on every launch |
| `sw.js` | service worker: offline shell, tile cache, network-first weather |
| `manifest.webmanifest`, `icons/` | install to home screen |
| `_headers` | cache policy for hosts that read it (Cloudflare Pages, Netlify) |

## Why this build exists

The artifact build inlines everything into one file, because a published
artifact blocks external requests. That costs 10.4 MB of basemap covering a
45 × 64 km window, which is why the map has never been able to leave
Kristiansand. This build fetches tiles instead, so the page is 4.4 MB and
**stops growing with area**. See `EXPANSION.md`.

Two things only work here:

- **The weather is actually current.** The app asks for a fresher
  `conditions.json` on every launch. In the artifact that request is blocked, so
  the published link is frozen at whatever was baked in at build time.
- **You can leave Kristiansand.** Panning is bounded by mainland Norway. The
  zones are not — they exist where the model was fitted, and the app says so
  everywhere else rather than showing blank ground.

## Hosting

Any static host. Free tiers are far above what this needs.

**Cloudflare Pages** — unlimited bandwidth on the free plan, reads `_headers`.
```
npx wrangler pages deploy dist --project-name soppkart
```

**Netlify** — reads `_headers`.
```
npx netlify deploy --dir dist --prod
```

**GitHub Pages** — works, but ignores `_headers`, and a project site is served
from a subpath. Everything here uses relative paths for that reason; do not
change them to absolute without testing under a subpath.

Serve over **HTTPS**. Geolocation and service workers both require it, so the
app silently loses GPS and offline support on plain HTTP.

## Map tiles

Kartverket's WMTS cache, `cache.kartverket.no/v1/wmts/1.0.0/`, matrix set
`utm33n` — EPSG:25833, the same projection everything else in this project uses,
so tiles need no reprojection.

- **Licence** CC BY 4.0. `©Kartverket` is shown on the map; keep it there.
- **Budget: 10,000 requests per IP per 24 hours.** Generous per person — a day
  out is a few hundred tiles — but users behind one mobile operator's NAT share
  an egress address, so at scale a whole carrier can approach it together. The
  service worker's tile cache is the mitigation and it is why the app uses WMTS
  rather than WMS: WMTS URLs are stable and cacheable, a WMS `GetMap` URL is
  unique per viewport and caches nothing.
- Tiles are 60–115 kB, larger than a typical slippy-map tile, and carry a
  five-day `Cache-Control`, so the browser's own cache does some of this too.

If the budget ever becomes a real limit, talk to Kartverket before engineering
around it — they have arrangements for higher volume.

## Before real users

Not blockers for putting it up, but each is a promise the app currently cannot
keep at scale:

- **Custom SMTP.** Supabase's built-in mailer rate-limits at 429, so account
  recovery does not work for real users. Resend is free to 3,000/month; then run
  `python scripts/check_auth_email.py din@epost.no`.
- **Supabase redirect URLs.** Add the deployed origin, or magic links bounce to
  `localhost:3000`.
- **A privacy policy and a delete path.** Saved spots are personal location
  data. The schema already has row-level security, `export_my_spots()` and
  tombstones; what is missing is the text and a button.
- **Schedule `purge_tombstones`** so deleted spots do not accumulate forever.

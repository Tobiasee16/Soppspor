"""Build the hosted version of the interface, for a static host.

    python scripts/build_hosted.py [outdir]             # Kristiansand -> dist/
    python scripts/build_hosted.py --national [outdir]  # Norway -> dist_norge/

The artifact build (`build_interface.py`) inlines everything into one file
because a published artifact blocks external requests. That works and it caps
the product at one kommune: 10.4 MB of the 14.8 MB page is basemap covering a
45x64 km window, and Norway's mainland is 111 times that area. No amount of
compression closes that gap - see EXPANSION.md.

A hosted build drops the inlined basemap entirely and fetches tiles from
Kartverket's cache at runtime, in EPSG:25833, the map's own projection. Three
things follow:

* **The page stops growing with area.** It is the shell, the model payload and
  the routes - the basemap is no longer part of the download at all.
* **The conditions layer actually refreshes.** The app already asks for a
  fresher `conditions.json` on every launch; in the artifact that request is
  blocked by the content policy, so the published link is frozen at whatever
  weather was baked in at build time. Here it works.
* **It can roam.** Panning is bounded by mainland Norway rather than by the
  edge of the sheets we happened to ship.

Two builds come out of here, and they differ in where the estimate exists.

The **Kristiansand** build carries its surfaces inside the page: the model is
fitted on Agder and validated on Kristiansand, and everywhere else the app
shows the map, the routes, the rainfall and your own places while saying
plainly that there are no zones yet.

The **national** build (`--national`) carries a tile index instead, and the
app fetches the ground it is looking at. Its model is fitted on fourteen
fylker and tested once on Nordland, which is a weaker claim than Phase 0's -
only Nordland is out of sample - and the page says so. Every region-specific
sentence in the Om page is replaced from the payload, with the numbers read
back out of the run reports rather than typed, because a stale accuracy claim
on a map is worse than no claim: the reader cannot tell that it drifted.

One thing the national build deliberately leaves out is the fresh rainfall
layer. The weather is fetched for Agder only, and an Agder number laid over a
map of Norway would be wrong everywhere else, so the build ships no
conditions file and the Om page says the layer is not in this edition.

Output is a plain static directory. Any host will serve it - Cloudflare Pages,
Netlify, GitHub Pages - and none of them need a build step of their own.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_interface as bi
import export_web_preview as ewp

from src import report_tables

SHELL = ROOT / "web" / "shell.html"
WEB = ROOT / "web"
PAYLOAD = bi.PAYLOAD
COND = bi.COND
TRAILS = bi.TRAILS

# Mainland Norway in EPSG:25833, with a 20 km margin, computed from the
# extreme points rather than eyeballed: Lindesnes, Nordkinn, Utsira and
# Hornoya. 1,215 km wide by 1,525 km tall.
NORWAY = [-95223, 6434491, 1120013, 7959232]


# --- the national build -----------------------------------------------------
# Kristiansand's payload carries its surfaces; Norway's carries a tile index and
# the app fetches the ground it is looking at. Everything below exists to turn
# the exporter's meta.json into that payload, and - the larger half - to make
# sure the page says true things about the model it is actually showing.

NAT_TILES = ROOT / "data" / "processed" / "zone_tiles"
NAT_META = NAT_TILES / "meta.json"
NAT_TRANSFER = ROOT / "data" / "processed" / "transferability_norge.md"
NAT_MODELS = ROOT / "data" / "processed" / "model_results_norge.md"
NAT_MAPS = ROOT / "data" / "processed" / "suitability_maps_norge.md"
NAT_POOL = ROOT / "data" / "interim" / "target_group_pool_norge.json"


def nb(n: int) -> str:
    """A Norwegian thousands-separated integer: 1234631 -> '1 234 631'."""
    return f"{n:,}".replace(",", " ")


def dec(x: float, places: int = 3) -> str:
    return f"{x:.{places}f}".replace(".", ",")


# Small numbers are spelled out in Norwegian prose; "fjorten fylker" reads as a
# sentence and "14 fylker" reads as a table cell. Beyond twenty, digits.
TALL = ["null", "én", "to", "tre", "fire", "fem", "seks", "sju", "åtte", "ni",
        "ti", "elleve", "tolv", "tretten", "fjorten", "femten", "seksten",
        "sytten", "atten", "nitten", "tjue"]


def tall(n: int) -> str:
    return TALL[n] if 0 <= n < len(TALL) else nb(n)


def national_facts() -> dict:
    """Every number the national About copy quotes, read from its own report.

    A number typed into a sentence goes stale exactly as quietly as a number
    typed into a table, and a stale accuracy claim on a map is worse than none:
    the reader cannot tell it drifted. `src/report_tables.py` already says this
    about the AUC table; the prose deserves the same treatment, so the build
    fails rather than shipping a paragraph it cannot substantiate.
    """
    for p in (NAT_META, NAT_TRANSFER, NAT_MODELS, NAT_MAPS, NAT_POOL):
        if not p.exists():
            raise SystemExit(f"missing {p.relative_to(ROOT)} - the national run "
                             f"has not produced it")
    models = NAT_MODELS.read_text(encoding="utf-8")
    m = re.search(r"\*\*Features \((\d+)\):\*\*", models)
    if not m:
        raise SystemExit(f"{NAT_MODELS.name}: no feature count - the report "
                         f"layout changed; fix the parser rather than guessing")
    maps = NAT_MAPS.read_text(encoding="utf-8")
    g = re.search(r"at (\d+) m, EPSG", maps)
    if not g:
        raise SystemExit(f"{NAT_MAPS.name}: no model cell size")
    transfer = NAT_TRANSFER.read_text(encoding="utf-8")
    h = re.search(r"\*\*Held-out extent:\*\* ([A-Za-zÆØÅæøå]+)", transfer)
    b = re.search(r"minus the held-out extent and everything within (\d+) m", transfer)
    if not (h and b):
        raise SystemExit(f"{NAT_TRANSFER.name}: no held-out extent or buffer")
    pool = json.loads(NAT_POOL.read_text(encoding="utf-8"))["pools"]["training"]

    fitted = report_tables.fitted_counts(NAT_TRANSFER)
    real = {k: v for k, v in fitted.items() if not k.startswith("SYNTHETIC")}
    if len(real) < 3:
        raise SystemExit(f"{NAT_TRANSFER.name}: only {len(real)} fitted counts")
    return {
        "features": int(m.group(1)),
        "cell_m": int(g.group(1)),
        "held_out": h.group(1),
        "buffer_m": int(b.group(1)),
        # A list of fylke names in the pool file, a count in the prose.
        "fylker": len(pool["fylker"]) if isinstance(pool["fylker"], list)
                  else int(pool["fylker"]),
        "pool_raw": pool["in_extent"],
        "pool_thinned": pool["thinned"],
        "fitted": real,
    }


def national_about(f: dict, null_auc: float | None, excluded: list) -> dict:
    """The paragraphs that name a region or quote a number, rewritten.

    Only these. The rest of the Om page describes the method, which is the same
    method, and duplicating it here would create a second copy to keep true -
    with the one nobody is reading being the one that goes stale.
    """
    tub = f["fitted"].get("Craterellus tubaeformis")
    others = sorted(v for k, v in f["fitted"].items()
                    if k != "Craterellus tubaeformis")
    held = f["held_out"]
    about = {
        # No fresh-rainfall layer in this build, so the lead cannot promise one.
        "ab_lead":
            "Kartet er et <b>anslag</b>, ikke en fasit. Det viser ikke hvor "
            "det står sopp. Det viser hvor terrenget <b>ligner på steder der "
            "arten faktisk er funnet før</b> &mdash; og det er noe annet.",
        "ab_intro":
            "Ingen har gått over Norge og registrert hva som vokser hvor. Det "
            "finnes bare spredte funn, samlet inn av folk som gikk der de "
            "pleier å gå. Modellen tar de funnene, lærer hva slags terreng de "
            "ligger i, og farger resten av landet etter hvor godt det ligner.",
        "ab_s1":
            "Fra <b>Artskart</b> (Artsdatabanken og GBIF-Norge), én art om "
            f"gangen. For traktkantarell er det {nb(tub)} funn i landet utenom "
            f"{held} etter at funn som ligger oppå hverandre er tynnet bort; "
            f"de andre artene ligger mellom {nb(others[0])} og "
            f"{nb(others[-1])}. Det finnes <b>ingen registrerte fravær</b> "
            "&mdash; ingen har notert «her lette jeg og fant ingenting». Det "
            "er den viktigste begrensningen i hele prosjektet.",
        "ab_s2":
            "Soppfunn klumper seg langs stier, veier og nær folk. En modell "
            "som får tilfeldige punkter å sammenligne med, lærer bare «nær "
            "vei». Derfor trekkes sammenligningspunktene fra <b>alle soppfunn "
            f"i de {tall(f['fylker'])} fylkene</b> &mdash; {nb(f['pool_raw'])} "
            f"registreringer, {nb(f['pool_thinned'])} etter tynning. Da har "
            "både funnene og sammenligningsgrunnlaget samme skjevhet, og "
            "modellen må finne noe annet enn tilgjengelighet for å skille dem.",
        "ab_s3":
            f"Hver {f['cell_m']}&nbsp;m rute får <b>{f['features']} tall</b> "
            "knyttet til seg, fra offentlige kartdata:",
        "ab_s4":
            "En <b>MaxEnt-type modell</b> (L1-straffet logistisk regresjon "
            "med kvadratiske ledd) lærer hvilke kombinasjoner av de "
            f"{f['features']} tallene som skiller funnstedene fra "
            f"sammenligningspunktene. Den tilpasses på <b>{tall(f['fylker'])} "
            f"fylker</b> &mdash; hele fastlandet utenom {held} &mdash; pluss "
            f"en {f['buffer_m'] // 1000} km sone rundt funnene som holdes "
            f"unna. <b>{held} er den eneste delen av dette kartet modellen "
            "aldri har sett.</b> Resten er terreng den er tilpasset på, og det "
            "er en reell svakhet: Kristiansand-kartet var holdt unna i hele "
            "sin utstrekning, dette er det ikke.",
        "ab_s6h": "Flaten deles i soner",
        "ab_s6":
            "Ruta havner blant de øverste <b>30&nbsp;%, 15&nbsp;% og "
            "5&nbsp;%</b> i landet, eller utenfor alle tre. Grensene er regnet "
            "ut over hele Norge én gang, slik at «topp 5&nbsp;%» betyr det "
            "samme uansett hvor på kartet du står &mdash; ikke topp 5&nbsp;% "
            "av det som tilfeldigvis er på skjermen.",
        "ab_zonewarn":
            "<b>Sonene er en rangering, ikke en sannsynlighet.</b> «Topp» "
            "betyr at ruta er blant de 5&nbsp;% mest lovende <i>i Norge</i>. "
            "Det sier ingenting om hvor mange sopp som står der, eller om det "
            "står noen i det hele tatt.",
        # The fresh-conditions layer is Agder-only, so this build has one clock.
        "ab_clocks":
            "<h2>To klokker: habitat og forhold</h2>"
            "<p>Kartet bygger på to lag som endrer seg i helt ulikt tempo, og "
            "i denne utgaven er bare det ene med.</p>"
            "<ul>"
            "<li><b>Habitat</b> endrer seg knapt. Skog, løsmasser og terreng "
            "er de samme neste år. Dette laget er modellen, og det er det som "
            "er testet.</li>"
            "<li><b>Forhold</b> endrer seg hele tiden. Sopp fruktifiserer "
            "etter fukt, og det hjelper ikke at et sted er perfekt hvis det "
            "ikke har regnet på tre uker.</li>"
            "</ul>"
            "<p>Nedbøren inne i modellen er et <b>30-årssnitt</b> for høsten. "
            "Det er riktig for en modell: det beskriver hva et sted "
            "<i>pleier</i> å være. Men det sier ingenting om denne uka.</p>"
            '<div class="warn"><p><b>Det ferske nedbørslaget er ikke med i '
            "denne utgaven.</b> Værdataene er foreløpig bare hentet for Agder, "
            "og et tall fra Agder lagt utenpå et kart av hele landet ville "
            "vært feil overalt ellers. Kartet du ser er derfor rent habitat.</p>"
            "</div>",
        "ab_transfer":
            f"<b>Overføringstest.</b> Modellen tilpasses på {tall(f['fylker'])} "
            f"fylker og testes én gang på <b>{held}</b>, som den aldri har "
            "sett.",
        "ab_auc":
            "AUC 0,50 er myntkast. 1,00 er perfekt. <b>Under 0,75 er svakt "
            "til moderat</b> &mdash; bedre enn tilfeldig, langt fra pålitelig. "
            f"Kolonnen til høyre er hvor mange funn i {held} tallet er målt "
            "på; er den lav, er treffsikkerheten en pekepinn og ikke et mål."
            + (f" Kontrollen landet på {dec(null_auc)}, så korreksjonen for "
               "innsats holder." if null_auc is not None else ""),
        # Agder's registered ordering failed; nationally it held. Both stand.
        "ab_order":
            "<h2>Det som var spådd på forhånd</h2>"
            "<p>Før noen modell ble kjørt, ble det skrevet ned hvilken "
            "rekkefølge vi ventet: traktkantarell best (den er kresen på "
            "fuktig mosegran), kantarell i midten, rød fluesopp dårligst. "
            "<b>På landsbasis slo det til</b> &mdash; både i den blokkdelte "
            f"testen og på {held}, og med god avstand mellom de tre.</p>"
            "<p>Den samme spådommen slo <b>ikke</b> til da modellen bare hadde "
            "Agder å gå på: der kom traktkantarell sist. Begge resultatene "
            "står, og det eldre står i rapportene. Et kart som bare viste fram "
            "det som stemte ville vært et dårligere kart.</p>"
            "<p>Tallene ble også høyere med hele landet enn med Agder alene, i "
            "snitt 0,07. <b>Det betyr ikke at modellen ble bedre.</b> Et "
            "større land gir større spenn i høyde, klima, skog og løsmasser på "
            "én gang, og da er det lettere å skille gode steder fra dårlige "
            "uten at modellen har lært noe nytt. At gevinsten ville komme, og "
            "hvorfor, var skrevet ned før kjøringen.</p>"
            "<p>De øvrige artene i listen kom til <b>etter</b> at den testen "
            "var gjort. De går gjennom nøyaktig samme mølle: samme "
            "sammenligningsgrunnlag, samme oppdeling i 7,5 km ruter, samme "
            "kontroll uten økologi ved siden av, og treffsikkerheten er målt "
            f"på {held} på samme måte. Men det ble aldri skrevet ned på "
            "forhånd hvordan <i>de</i> skulle rangere, så de inngår ikke i "
            "testen over.</p>",
        "ab_drop":
            "Regelen er satt på forhånd: <b>en art vises bare hvis den slår "
            "kontrollen på terreng modellen aldri har sett.</b> Kontrollen er "
            "falske funn uten økologi i seg. Klarer ikke en art å skille seg "
            f"fra den, har den ikke vist at den kan noe om {held}, og da hører "
            "den ikke hjemme på et kart noen skal gå etter.",
        "ab_thin":
            "<b>Flere av artene hviler på få funn.</b> Kolonnen «Funn» i "
            f"tabellen over er hvor mange registreringer i {held} "
            "treffsikkerheten er målt på. Er den lav, er tallet en pekepinn og "
            "ikke et mål &mdash; artslisten merker de tynneste med «få funn».",
        "ab_weak":
            "<li><b>Skogstruktur og høyde bærer modellen.</b> Grunnflate, "
            "volum og høyde over havet betyr mest. Høyde henger tett sammen "
            "med avstand til sjø og til folk, så noe av «økologien» kan være "
            "geografi i forkledning.</li>"
            "<li><b>Skogdataene er modellerte, ikke målte.</b> SR16 treffer "
            "riktig treslag i omtrent to av tre ruter, og volumavviket er "
            "30&ndash;60&nbsp;% av snittet.</li>"
            "<li><b>Ingen fravær.</b> Uten steder noen har lett forgjeves, kan "
            "modellen ikke skille «dårlig sted» fra «ingen har vært der».</li>"
            f"<li><b>Bare {held} er holdt unna.</b> Resten av kartet er "
            "terreng modellen er tilpasset på. Treffsikkerheten i tabellen er "
            f"målt på {held}, og det er det tallet som sier noe om hvor mye "
            "resten er verdt.</li>"
            "<li><b>Kartet vet ingenting om denne uka.</b> Nedbøren i "
            "modellen er et 30-årssnitt for høsten, og det ferske "
            "nedbørslaget er ikke med i denne utgaven.</li>"
            f"<li><b>Rutene er 128 m på skjermen.</b> Modellen regner på "
            f"{f['cell_m']} m, men vises grovere. En sone peker på en "
            "skogteig, ikke på et tre.</li>",
        "ab_species":
            "Det store tallet er <b>treffsikkerhet</b>: AUC målt på "
            f"{held} etter at modellen er tilpasset på de {tall(f['fylker'])} andre "
            "fylkene. 0,50 er ren gjetning, 1,00 er perfekt. Under står "
            f"<b>hvor mange funn i {held} tallet er målt på</b>. Står det «få "
            "funn», hviler treffsikkerheten på en håndfull registreringer og "
            "bør leses som en pekepinn, ikke som et mål.",
    }
    if not excluded:
        # The markup's section describes an exclusion that did not happen here.
        about["ab_drop"] = (
            about["ab_drop"] + " <b>I denne utgaven kom alle artene gjennom.</b>")
    return about


def national_payload() -> tuple[str, dict, list[str]]:
    """`(payload script, meta, shipped species keys)` for a tiled build."""
    meta = json.loads(NAT_META.read_text(encoding="utf-8"))
    facts = national_facts()
    null_auc = meta.get("null_transfer_auc")

    payload = {
        "meta": {
            "crs": meta["crs"], "cell_m": meta["cell_m"],
            "origin_x": meta["origin_x"], "origin_y": meta["origin_y"],
            "tile_px": meta["tile_px"], "percentile_bytes": True,
            "null_transfer_auc": null_auc,
            "excluded_species": [],
            # Where the tiles live, relative to the page. The app substitutes
            # {sp}, {row} and {col}; a CDN or a levelled layout is a change to
            # this string and to nothing in the interface.
            "tiles": {"url": "zones/{sp}/{row}_{col}.png",
                      "cols": meta["tiles"]["cols"], "rows": meta["tiles"]["rows"]},
            "area_label": "Norge · anslag",
            "tested_on": facts["held_out"],
            "fitted_on": f"{tall(facts['fylker'])} fylker",
            "source_cell_m": facts["cell_m"],
        },
        "grid": meta["grid"],
        "species": [dict(sp) for sp in meta["species"] if not sp.get("is_null")],
        # The card's "why" layers are Kristiansand-only so far: 39 national
        # feature rasters at 32 m is another 100 GB of delivery, and it is a
        # separate piece of work from putting the estimate on the map.
        "context": {}, "categorical": {},
    }
    # The same pre-registered bar the Kristiansand build applies, applied to
    # the national numbers rather than re-decided for them.
    ewp.drop_species_that_failed_validation(payload, null_auc)
    payload["about"] = national_about(facts, null_auc,
                                      payload["meta"]["excluded_species"])

    n_tiles = sum(len(sp.get("tiles", [])) for sp in payload["species"])
    print(f"  national: {len(payload['species'])} species, {n_tiles:,} tiles, "
          f"{facts['cell_m']} m model on a {meta['cell_m']:.0f} m display grid")
    print(f"  tested on {facts['held_out']}, fitted on {facts['fylker']} fylker, "
          f"{facts['features']} features")
    js = "window.SOPP=" + json.dumps(payload, separators=(",", ":"),
                                     ensure_ascii=False) + ";"
    return js, payload["meta"], [sp["key"] for sp in payload["species"]]


def copy_tiles(out: pathlib.Path, keys: list[str]) -> tuple[int, int]:
    """Copy the zone tiles the payload references. Returns (files, bytes).

    Only those. The exporter cuts tiles for the synthetic null as well, because
    the null map is one of the checks that the surfaces mean anything - but it
    is a validation device and never ships as a species, so copying its 455
    tiles would be 18 MB of a static host that nothing can ever request. The
    same goes for any species the pre-registered bar drops.
    """
    dest = out / "zones"
    if dest.exists():
        shutil.rmtree(dest)
    n, size = 0, 0
    for key in keys:
        src_dir = NAT_TILES / key
        if not src_dir.is_dir():
            raise SystemExit(f"missing tiles for {key} in {NAT_TILES}")
        (dest / key).mkdir(parents=True, exist_ok=True)
        for src in sorted(src_dir.glob("*.png")):
            shutil.copyfile(src, dest / key / src.name)
            n += 1
            size += src.stat().st_size
    return n, size


HEAD = """<!doctype html>
<html lang="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f2f2f7" media="(prefers-color-scheme:light)">
<meta name="theme-color" content="#000000" media="(prefers-color-scheme:dark)">
<meta name="description" content="Soppkart - et anslag fra offentlige kartdata om hvor terrenget ligner på steder sopp faktisk er funnet.">
<meta name="robots" content="noindex">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icons/icon-180.png">
<!-- iOS honours the manifest for `display` from 16.4, but the status bar is
     still controlled here and nowhere else. Left unset it defaults to the same
     thing, and defaults are what break quietly when Apple changes them.

     `default` rather than `black-translucent` on purpose. Translucent would let
     the map run underneath the Dynamic Island, which looks better in a
     screenshot and puts the white system clock on top of a pale grey basemap,
     where it cannot be read. The status bar instead takes theme-color: light
     grey with black text, or black with white, whichever matches. Legibility
     beats edge-to-edge on a thing people use outdoors in sunlight. -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Soppkart">
<meta name="mobile-web-app-capable" content="yes">
<title>Soppkart</title>
</head>
<body>
"""

TAIL = """
<script>
/* Registered last so a failure here can never stop the app rendering. The
   worker is what makes the map usable with no signal: it keeps the shell and
   every Kartverket tile already looked at, which is why the tile source is a
   WMTS cache with stable URLs rather than a WMS with a bbox per view. */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("sw.js").catch(function () {});
  });
}
</script>
</body>
</html>
"""


def strip_shell_title(shell: str) -> str:
    """Drop the shell's own <title>; HEAD supplies the document head.

    Left in, it is a second title element sitting in the body - parsed, kept,
    and naming one kommune on a map of the country. `document.title` returns
    the first in tree order, which is why this was invisible rather than
    visibly wrong, but a page should not carry a title it does not mean.
    """
    if not shell.startswith("<title>"):
        return shell
    return shell[shell.index("</title>") + len("</title>"):].lstrip("\r\n")


def main() -> int:
    national = "--national" in sys.argv
    rest = [a for a in sys.argv[1:] if not a.startswith("--")]
    default = "dist_norge" if national else "dist"
    out = pathlib.Path(rest[0]) if rest else ROOT / default
    if not national and not PAYLOAD.exists():
        raise SystemExit(f"missing {PAYLOAD} - run scripts/export_web_preview.py")
    if not SHELL.exists():
        raise SystemExit(f"missing {SHELL}")

    out.mkdir(parents=True, exist_ok=True)

    # --- the page -------------------------------------------------------
    shell = strip_shell_title(SHELL.read_text(encoding="utf-8"))
    marker = "<script>\n(function(){"
    if marker not in shell:
        raise SystemExit("shell has no '<script>\\n(function(){' insertion point")

    env = {"hosted": True, "roaming": True, "bounds": NORWAY}
    env_js = "window.SOPP_ENV=" + json.dumps(env, separators=(",", ":")) + ";"

    # No inlined basemap. This is the whole point of the hosted build, and it
    # is stated rather than left as an empty object someone later "fixes".
    base_js = ("/* hosted build: the basemap comes from Kartverket's WMTS cache "
               "at runtime, not from bytes in this file */\n"
               "window.SOPP_BASE={};")

    nat_meta = None
    if national:
        payload, nat_meta, nat_keys = national_payload()
    else:
        payload = PAYLOAD.read_text(encoding="utf-8")

    # Conditions and routes stay external files rather than inlined script.
    # Weather is on a daily clock and the app refetches it on launch; making it
    # a file means a weather update is a one-file upload, not a rebuild.
    cond_js = "window.SOPP_COND=null;"
    if national:
        # Not an oversight and not a TODO in disguise. seNorge is fetched for
        # Agder, and Agder's rainfall laid over a map of Norway would be wrong
        # everywhere else; the Om page says the layer is not in this edition.
        print("  conditions: none - the weather is fetched for Agder only")
    elif COND.exists():
        shutil.copyfile(COND, out / "conditions.json")
        cond = json.loads(COND.read_text(encoding="utf-8"))
        cond_js = "window.SOPP_COND=" + json.dumps(cond, separators=(",", ":")) + ";"
        print(f"  conditions: {cond['last_day']} ({cond['lag_days']}d behind)")

    trails_js = "window.SOPP_TRAILS=null;"
    if TRAILS.exists() and not national:
        tr = json.loads(TRAILS.read_text(encoding="utf-8"))
        trails_js = "window.SOPP_TRAILS=" + json.dumps(tr, separators=(",", ":")) + ";"
        print(f"  trails: {tr['lines']} routes, {tr['vertices']} vertices")

    injected = ("<script>" + payload + "</script>\n"
                "<script>" + env_js + "</script>\n"
                "<script>" + base_js + "</script>\n"
                "<script>" + cond_js + "</script>\n"
                "<script>" + trails_js + "</script>\n"
                "<script>" + bi.supabase_config() + "</script>\n" + marker)
    page = HEAD + shell.replace(marker, injected, 1) + TAIL
    (out / "index.html").write_text(page, encoding="utf-8")

    if national:
        n, size = copy_tiles(out, nat_keys)
        print(f"  zones: {n:,} tiles, {size / 1e6:.1f} MB")

    # --- everything else the host serves --------------------------------
    for name in ("manifest.webmanifest", "sw.js"):
        src = WEB / name
        if src.exists():
            shutil.copyfile(src, out / name)
        else:
            print(f"  WARNING: {name} missing from web/")
    if (WEB / "icons").exists():
        shutil.copytree(WEB / "icons", out / "icons", dirs_exist_ok=True)

    # Headers for hosts that read them. Long-lived caching for the immutable
    # assets, none for the page and the weather.
    (out / "_headers").write_text(
        "/*\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  X-Content-Type-Options: nosniff\n"
        "/index.html\n"
        "  Cache-Control: no-cache\n"
        "/sw.js\n"
        "  Cache-Control: no-cache\n"
        "/conditions.json\n"
        "  Cache-Control: no-cache\n"
        "/icons/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n"
        # A rebuild writes a new tile rather than editing one in place, so
        # these are as immutable as the icons and considerably heavier.
        "/zones/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n",
        encoding="utf-8")

    # Single-page fallback. The app never creates a deep path itself - it reads
    # the magic link's hash and then replaceState's back to where it started -
    # but a bookmark, a stray link or a shared URL would otherwise land on the
    # host's 404 page instead of the map. `serve_interface.py --dist` already
    # does this; without the file the deployed copy would differ from the one
    # that was tested, which is the worst kind of difference.
    #
    # Real files win over this rule, so conditions.json and the icons are
    # unaffected.
    (out / "_redirects").write_text("/*  /index.html  200" + chr(10),
                                    encoding="utf-8")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    page_mb = len(page.encode("utf-8")) / 1e6
    print(f"\n  index.html        {page_mb:6.2f} MB")
    for f in sorted(out.rglob("*")):
        if f.is_file() and f.name != "index.html" and "zones/" not in \
                f.relative_to(out).as_posix():
            print(f"  {f.relative_to(out).as_posix():18s}{f.stat().st_size / 1e6:6.2f} MB")
    print(f"\nhosted build -> {out}  ({total / 1e6:.2f} MB total)")
    if national:
        print("compare: the Kristiansand hosted build is one page and no tiles.")
    else:
        print("compare: the artifact build is 14.77 MB and cannot leave Kristiansand.")
    print("\nserve it locally with:  python scripts/serve_interface.py --dist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

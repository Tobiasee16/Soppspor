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

import hashlib
import json
import os
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
NAT_CTX = ROOT / "data" / "processed" / "context_tiles"
NAT_COND = ROOT / "data" / "processed" / "conditions_norge.json"
NAT_TRAILS = ROOT / "data" / "processed" / "trails"
NAT_TRAILS_INDEX = NAT_TRAILS / "index.json"

# Where the national build's conditions layer comes from. "static" ships the
# file inside the deployment, which is how this worked before there was
# anywhere else to put it; "kv" leaves the path to the Pages Function.
#
# It is an environment variable rather than a constant because the switch has
# an order to it and is reversible: the KV namespace has to exist, be bound to
# the Pages project as CONDITIONS, and have something written to it *before*
# the build stops shipping the file, or the app loses its conditions layer
# between one deploy and the next. Flip it in deploy.sh once that is true.
CONDITIONS_FROM_KV = os.environ.get("SOPP_CONDITIONS", "static").lower() == "kv"


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


def national_about(f: dict, null_auc: float | None, excluded: list,
                   cond: dict | None = None) -> dict:
    """The paragraphs that name a region or quote a number, rewritten.

    Only these. The rest of the Om page describes the method, which is the same
    method, and duplicating it here would create a second copy to keep true -
    with the one nobody is reading being the one that goes stale.
    """
    tub = f["fitted"].get("Craterellus tubaeformis")
    others = sorted(v for k, v in f["fitted"].items()
                    if k != "Craterellus tubaeformis")
    held = f["held_out"]
    km = f"{cond['grid']['step'] / 1000:.0f}" if cond else ""
    about = {
        "ab_lead":
            "Kartet er et <b>anslag</b>, ikke en fasit. Det viser ikke hvor "
            "det står sopp. Det viser hvor terrenget <b>ligner på steder der "
            "arten faktisk er funnet før</b>"
            + (", justert for hvor mye det har regnet i det siste" if cond else "")
            + " &mdash; og det er noe annet.",
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
        # The markup's list names a fuktighetsindeks. The national model does
        # not have one - flow accumulation over 1.83 billion cells does not fit
        # in memory - and a feature list that claims it would be describing the
        # Kristiansand model on a map of Norway.
        "ab_layers":
            "<li><b>Terreng</b> fra Kartverkets høydemodell: høyde, helning, "
            "himmelretning og krumning. <b>Uten fuktighetsindeks</b> &mdash; "
            "den krever at vannet spores over hele landet i én sammenhengende "
            "beregning, og den får ikke plass i maskinen. Kristiansand-kartet "
            "har den; dette har den ikke.</li>"
            "<li><b>Skog</b> fra NIBIO SR16: treslag, alder, kronedekning, "
            "volum, grunnflate, trehøyde, bonitet</li>"
            "<li><b>Løsmasser</b> fra NGU: morene, breelv, marin leire, torv, "
            "bart fjell og fem til. Laget sier hva slags <i>avsetning</i> "
            "grunnen er, ikke hvor kalkrik den er &mdash; og det skiller "
            "artene svakt.</li>"
            "<li><b>Arealtype</b> fra NIBIO AR50: skog, myr, dyrka mark, "
            "bebygd, vann</li>"
            "<li><b>Høstnedbør</b> fra MET seNorge: 9. august&ndash;20. "
            "oktober, snitt 1991&ndash;2020</li>",
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
        "ab_s6h": ("Ferske forhold legges oppå" if cond
                   else "Flaten deles i soner"),
        "ab_s6":
            (("Nedbøren de siste 21 døgnene hentes fra <b>MET seNorge</b> og "
              "sammenlignes med hva som er normalt der på den datoen. Ruter "
              "som har fått mer enn vanlig vektes opp, ruter som har fått "
              "mindre vektes ned. " ) if cond else "")
            + "Ruta får først en poengsum <b>mot terrenget rundt seg</b> &mdash; "
            "omtrent 25&nbsp;km i hver retning. Målt mot hele Norge ble store "
            "deler av Sør- og Østlandet liggende i toppsjiktet samtidig, og da "
            "forsvant nettopp den forskjellen du står og lurer på: er det bedre "
            "her enn i skogen ved siden av?<br><br>"
            "Sonene er så de <b>30&nbsp;%, 15&nbsp;% og 5&nbsp;% høyeste av "
            "disse poengsummene i hele landet</b> &mdash; eller utenfor alle "
            "tre. Begge leddene teller: poengsummen er lokal, grensene er "
            "nasjonale. Det betyr at et område med lite god soppmark får få "
            "soner i stedet for å få sine egne beste ruter farget uansett hvor "
            "svake de er. Grensene er regnet ut på forhånd, så en rute skifter "
            "ikke farge når du panorerer eller zoomer.",
        "ab_zonewarn":
            "<b>Sonene er en rangering, ikke en sannsynlighet.</b> «Topp» "
            "betyr at ruta er blant de 5&nbsp;% som scorer høyest <i>i "
            "landet</i>, der scoren er målt mot terrenget innenfor omtrent "
            "25&nbsp;km. Det sier ingenting om hvor mange sopp som "
            "står der, eller om det står noen i det hele tatt. "
            "<b>Vann, dyrket mark, bebyggelse og isbre får ingen sone.</b> "
            "Skogdataene sier ingenting om slik grunn, og et anslag der ville "
            "vært en gjetning uten noe bak seg.",
        "ab_clocks":
            "<h2>To klokker: habitat og forhold</h2>"
            "<p>Kartet kombinerer to lag som oppdateres i helt ulikt tempo.</p>"
            "<ul>"
            "<li><b>Habitat</b> endrer seg knapt. Skog, løsmasser og terreng "
            "er de samme neste år. Dette laget er modellen, og det er det som "
            "er testet.</li>"
            "<li><b>Forhold</b> endrer seg hele tiden. Sopp fruktifiserer "
            "etter fukt, og det hjelper ikke at et sted er perfekt hvis det "
            "ikke har regnet på tre uker."
            + (f" Dette laget hentes fra <b>MET seNorge</b> for hele "
               f"fastlandet, regnet ut på deres eget kilometersrutenett og "
               f"vist på {km} km ruter." if cond else "")
            + "</li></ul>"
            "<p>Nedbøren inne i modellen er et <b>30-årssnitt</b> for høsten. "
            "Det er riktig for en modell: det beskriver hva et sted "
            "<i>pleier</i> å være. Men det sier ingenting om denne uka, og "
            "derfor ligger det ferske laget utenpå i stedet for inni.</p>"
            + ("<p>Værlaget <b>hentes på nytt hver gang du åpner appen</b>, så "
               "lenge du har dekning. seNorge publiseres ett til to døgn "
               "etterskuddsvis, og appen skriver hvor gammelt tallet er i "
               "stedet for å la deg tro at det er sanntid.</p>"
               '<div class="warn"><p><b>Det ferske laget er ikke testet.</b> '
               "Ingen soppfunn er brukt til å finne ut hvor mye nedbør skal "
               "telle. Vekten er satt etter skjønn og er bevisst liten "
               "&mdash; forholdene kan flytte rangeringen noe, ikke snu den. "
               "Habitatlaget er det som er validert; dette er en "
               "tommelfingerregel oppå.</p>"
               "<p>Trykk på en rute, så ser du begge tallene hver for seg.</p>"
               "</div>"
               if cond else
               '<div class="warn"><p><b>Det ferske nedbørslaget er ikke med i '
               "denne utgaven.</b> Kartet du ser er rent habitat.</p></div>"),
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
            "<li><b>Skogstruktur og terreng bærer modellen.</b> Grunnflate, "
            "høyde over havet, helning og himmelretning ligger så tett sammen "
            "på toppen at det ikke er grunnlag for å rangere dem. Høyde "
            "henger tett sammen med avstand til sjø og til folk, så noe av "
            "«økologien» kan være geografi i forkledning.</li>"
            "<li><b>Skogdataene er modellerte, ikke målte.</b> SR16 treffer "
            "riktig treslag i omtrent to av tre ruter, og volumavviket er "
            "30&ndash;60&nbsp;% av snittet.</li>"
            "<li><b>Ingen fravær.</b> Uten steder noen har lett forgjeves, kan "
            "modellen ikke skille «dårlig sted» fra «ingen har vært der».</li>"
            "<li><b>Underlaget skiller artene dårlig.</b> Kalkrik grunn regnes "
            "som viktig for sopp, og vi har målt det mot NGUs kart over "
            "kalkinnhold i berggrunnen: alle artene her står på litt rikere "
            "fjell enn soppen rundt dem, men forskjellen <i>mellom</i> artene "
            "er for liten til å tegne et kart av. Modellen går derfor på "
            "løsmasser, ikke på kalk.</li>"
            f"<li><b>Bare {held} er holdt unna.</b> Resten av kartet er "
            "terreng modellen er tilpasset på. Treffsikkerheten i tabellen er "
            f"målt på {held}, og det er det tallet som sier noe om hvor mye "
            "resten er verdt.</li>"
            + (f"<li><b>Nedbøren vises på {km} km ruter.</b> Den er regnet "
               "ut på seNorges eget kilometersrutenett og slått sammen for "
               "visning, fordi hele landet på én kilometer er 1,85 millioner "
               "ruter i en fil appen henter hver gang den åpnes. Et "
               "nedbøravvik endrer seg lite over noen kilometer; "
               "millimetertallet på en fjellside gjør det.</li>"
               if cond else
               "<li><b>Kartet vet ingenting om denne uka.</b> Nedbøren i "
               "modellen er et 30-årssnitt for høsten, og det ferske "
               "nedbørslaget er ikke med i denne utgaven.</li>")
            + 
            f"<li><b>Rutene er 128 m på skjermen når du er zoomet inn</b>, "
            "og grovere når du zoomer ut. Modellen regner på "
            f"{f['cell_m']} m, men vises grovere uansett. En sone peker på en "
            "skogteig, ikke på et tre.</li>",
        "ab_species":
            "Det store tallet er <b>treffsikkerhet</b>: AUC målt på "
            f"{held} etter at modellen er tilpasset på de {tall(f['fylker'])} andre "
            "fylkene. 0,50 er ren gjetning, 1,00 er perfekt. Under står "
            f"<b>hvor mange funn i {held} tallet er målt på</b>. Står det «få "
            "funn», hviler treffsikkerheten på en håndfull registreringer og "
            "bør leses som en pekepinn, ikke som et mål.",
    }
    # The markup describes an exclusion that happened in one build and not the
    # other. Agder dropped blek piggsopp; nationally every species cleared the
    # control, and for as long as that was true the live page carried the
    # heading "Én art kom ikke gjennom" directly above a sentence saying they
    # all did. The heading and the closing paragraph are addressable now, so
    # the three pieces cannot disagree.
    n_ex = len(excluded)
    about["ab_droph"] = (
        "Alle artene kom gjennom kontrollen" if not n_ex else
        "Én art kom ikke gjennom" if n_ex == 1 else
        f"{tall(n_ex).capitalize()} arter kom ikke gjennom")
    if n_ex:
        about["ab_dropkeep"] = (
            ("Den er" if n_ex == 1 else "De er")
            + " ikke slettet fra analysen &mdash; tallet står i rapportene, "
            "og det ville vært juks å fjerne en art fordi den gjorde det "
            "dårlig. Men det er forskjell på hva som skal stå i en tabell og "
            "hva som skal tegnes som en blå sone til noen som skal bruke "
            "lørdagen på å gå dit.")
    else:
        about["ab_drop"] += " <b>I denne utgaven kom alle artene gjennom.</b>"
        about["ab_dropkeep"] = (
            "Regelen har bitt før. I den første utgaven av dette kartet, som "
            "bare hadde Agder å gå på, kom <b>blek piggsopp</b> ikke gjennom, "
            "og da ble den ikke tegnet. Tallene for hver art står i rapportene "
            "uansett hvordan den gjorde det &mdash; det ville vært juks å "
            "fjerne en art fra analysen fordi den gjorde det dårlig. Men det "
            "er forskjell på hva som skal stå i en tabell og hva som skal "
            "tegnes som en blå sone til noen som skal bruke lørdagen på å gå "
            "dit.")
    return about


def tiles_stamp() -> str:
    """A short digest of every tile byte that is about to ship.

    Tiles are served with `Cache-Control: immutable` for a year and the service
    worker keeps them cache-first with no revalidation, both of which rest on
    the claim that a rebuild writes a new tile rather than changing one in
    place. That claim was false: the exporter writes
    `zones/<species>/<z>/<row>_<col>.png` from the row and column, so a
    re-export puts different bytes at exactly the same address. When the
    ranking changed from a national percentile to a 25 km neighbourhood, every
    tile's contents changed and not one URL did - so anybody who had opened the
    app before that could have gone on being shown the old estimate, under a
    page whose own text described the new one, for up to a year.

    Stamping the URL with this makes the claim true. The digest is over the
    bytes rather than over the clock, so a rebuild that changes only the page -
    which is most of them - leaves every tile URL alone and costs a reader
    nothing.
    """
    h = hashlib.sha256()
    roots = [(NAT_TILES, "zones"), (NAT_CTX, "context")]
    for root, label in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.png"), key=lambda q: q.as_posix()):
            h.update(f"{label}/{p.relative_to(root).as_posix()}".encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def mask_meta(meta: dict, stamp: str) -> dict | None:
    """The mask block for the payload, with the tile url made page-relative.

    The exporter writes ids under `mask/`; the build puts them under `zones/`
    alongside the species, so the one string that says where they live is
    rewritten here rather than in the interface.
    """
    m = meta.get("mask")
    if not m:
        return None
    out = dict(m)
    out["url"] = ("zones/" + str(m.get("url", "mask/{z}/{row}_{col}.png"))
                  + "?v=" + stamp)
    return out


def national_payload() -> tuple[str, dict, list[str]]:
    """`(payload script, meta, shipped species keys)` for a tiled build."""
    meta = json.loads(NAT_META.read_text(encoding="utf-8"))
    facts = national_facts()
    null_auc = meta.get("null_transfer_auc")
    stamp = tiles_stamp()

    payload = {
        "meta": {
            "crs": meta["crs"], "cell_m": meta["cell_m"],
            "origin_x": meta["origin_x"], "origin_y": meta["origin_y"],
            "tile_px": meta["tile_px"], "percentile_bytes": True,
            # Carried through from the exporter so the shipped payload says
            # what its own bytes mean. The interface reads none of these - it
            # goes by `percentile_bytes` - but an artefact that cannot describe
            # itself is how "national percentile" stayed written in three
            # places after it had stopped being true.
            "encoding": meta.get("encoding"),
            "neighbourhood_m": meta.get("neighbourhood_m"),
            "habitat_ar50": meta.get("habitat_ar50"),
            "null_transfer_auc": null_auc,
            "excluded_species": [],
            # Where the tiles live, relative to the page. The app substitutes
            # {sp}, {row} and {col}; a CDN or a levelled layout is a change to
            # this string and to nothing in the interface.
            "tiles": {"url": "zones/{sp}/{z}/{row}_{col}.png?v=" + stamp,
                      "cols": meta["tiles"]["cols"], "rows": meta["tiles"]["rows"]},
            # What the stamp above is, so the shipped artefact can say it.
            "tiles_build": stamp,
            # The habitat mask, four times finer than the zone tiles, which
            # the interface erases with so that a lake keeps its own shape
            # instead of the 128 m diamond the grid would otherwise draw
            # around it. Absent from a build that has not exported one, and
            # the interface then falls back to leaving blank cells as holes.
            "mask": mask_meta(meta, stamp),
            # One entry per pyramid level, coarsest last. Without these the app
            # holds a single resolution and the zones stop when a screenful of
            # them stops fitting in its cell budget.
            "levels": meta.get("levels"),
            "area_label": "Norge · anslag",
            "tested_on": facts["held_out"],
            "fitted_on": f"{tall(facts['fylker'])} fylker",
            "source_cell_m": facts["cell_m"],
        },
        "grid": meta["grid"],
        "species": [dict(sp) for sp in meta["species"] if not sp.get("is_null")],
        # Empty rather than absent: these are the *inlined* surfaces, which a
        # tiled build does not have. The same seven facts arrive per tapped
        # neighbourhood through `ctx` below.
        "context": {}, "categorical": {},
    }
    ctx_meta = NAT_CTX / "meta.json"
    if ctx_meta.exists():
        cm = json.loads(ctx_meta.read_text(encoding="utf-8"))
        # The tile index is deliberately not shipped. A card only opens on a
        # cell that has a score, and a scored cell always has context, so
        # there is nothing for the app to check against.
        payload["ctx"] = {"url": "context/{row}_{col}.png?v=" + stamp,
                          "cell_m": cm["cell_m"], "tile_px": cm["tile_px"],
                          "origin_x": cm["origin_x"], "origin_y": cm["origin_y"],
                          "grid": cm["grid"], "layers": cm["layers"]}
        print(f"  context: {len(cm['layers'])} layers, {len(cm['tiles']):,} tiles")
    else:
        print("  context: none - run scripts/export_context_tiles.py")
    # The same pre-registered bar the Kristiansand build applies, applied to
    # the national numbers rather than re-decided for them.
    ewp.drop_species_that_failed_validation(payload, null_auc)
    cond = None
    if NAT_COND.exists():
        cond = json.loads(NAT_COND.read_text(encoding="utf-8"))
    payload["about"] = national_about(facts, null_auc,
                                      payload["meta"]["excluded_species"], cond)

    def count(sp):
        t = sp.get("tiles") or []
        return sum(len(x) for x in t) if t and isinstance(t[0], list) else len(t)

    n_tiles = sum(count(sp) for sp in payload["species"])
    levels = payload["meta"].get("levels") or []
    print(f"  national: {len(payload['species'])} species, {n_tiles:,} tiles, "
          f"{facts['cell_m']} m model on a {meta['cell_m']:.0f} m display grid")
    if levels:
        print("  levels: " + ", ".join(f"{lv['cell_m']:.0f} m" for lv in levels))
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
    # The mask is one set for every species - it describes the ground, not a
    # prediction about it - so it rides under zones/ where the immutable cache
    # header and the service worker's tile rule already reach it.
    mask_src = NAT_TILES / "mask"
    if mask_src.is_dir():
        for src in sorted(mask_src.rglob("*.png")):
            out_file = dest / "mask" / src.relative_to(mask_src)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out_file)
            n += 1
            size += src.stat().st_size
    for key in keys:
        src_dir = NAT_TILES / key
        if not src_dir.is_dir():
            raise SystemExit(f"missing tiles for {key} in {NAT_TILES}")
        for src in sorted(src_dir.rglob("*.png")):
            out_file = dest / key / src.relative_to(src_dir)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out_file)
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
<meta name="description" content="Soppspor - et anslag fra offentlige kartdata om hvor terrenget ligner på steder sopp faktisk er funnet.">
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
<meta name="apple-mobile-web-app-title" content="Soppspor">
<meta name="mobile-web-app-capable" content="yes">
<title>Soppspor</title>
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


# The manifest is what an installed app shows in the launcher and in the
# install prompt, so its description is product copy like any other - and it was
# being copied into both builds unchanged, where it made two claims that are
# false of the national one. It promised "merkede stier", which that build does
# not ship (Turrutebasen is packed into the page, which is the right shape for
# one kommune and tens of megabytes for the mainland), and it said the zone
# estimate was worked out for Kristiansand, which stopped being true when the
# pyramid went national. Both read as a product describing a different product.
MANIFEST_DESC = {
    False: ("Turkart for soppturer rundt Kristiansand: merkede stier, "
            "nedb\u00f8ren de siste ukene, og dine egne steder lagret privat. "
            "Anslaget er regnet ut fra offentlige kartdata, ikke en fasit."),
    True: ("Turkart for soppturer i hele Norge: soneanslag for hele landet, "
           "nedb\u00f8ren de siste ukene, og dine egne steder lagret privat. "
           "Anslaget er regnet ut fra offentlige kartdata, ikke en fasit."),
}


def manifest_text(national: bool) -> str:
    """web/manifest.webmanifest with the description this build can stand behind."""
    m = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
    m["description"] = MANIFEST_DESC[bool(national)]
    return json.dumps(m, ensure_ascii=False, indent=2) + "\n"


def copy_flat(src_dir: pathlib.Path, dest: pathlib.Path) -> tuple[int, int]:
    """Copy one flat directory of PNGs. Returns (files, bytes)."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    n, size = 0, 0
    for src in sorted(src_dir.glob("*.png")):
        shutil.copyfile(src, dest / src.name)
        n += 1
        size += src.stat().st_size
    return n, size


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
    if national and CONDITIONS_FROM_KV:
        # The route is functions/conditions.json.js, served out of Workers KV
        # and refreshed by .github/workflows/conditions.yml. Writing the file
        # here as well would silently win: a real file beats a function at the
        # same path, so the daily refresh would keep working, keep reporting
        # success, and never reach a single reader.
        cond = (json.loads(NAT_COND.read_text(encoding="utf-8"))
                if NAT_COND.exists() else None)
        print("  conditions: from KV at /conditions.json, not shipped as a file"
              + (f" (last built locally: {cond['last_day']})" if cond else ""))
    elif national and NAT_COND.exists():
        shutil.copyfile(NAT_COND, out / "conditions.json")
        cond = json.loads(NAT_COND.read_text(encoding="utf-8"))
        g = cond["grid"]
        # Shipped as a file, not inlined. Kristiansand's weather is 70 kB and
        # riding along inside the page buys a working conditions layer on a
        # first launch with no signal; the national file is 1.2 MB, which would
        # be most of the page, and a first launch with no signal has no map
        # tiles either - so there is nothing for it to be the floor of.
        # `refreshConditions` fetches it on every launch regardless, and the
        # service worker keeps the last copy for the ones after that.
        print(f"  conditions: {cond['last_day']} ({cond['lag_days']}d behind), "
              f"{g['w']} x {g['h']} at {g['step']:.0f} m, "
              f"{(out / 'conditions.json').stat().st_size / 1e6:.2f} MB fetched "
              f"on launch")
    elif national:
        print("  conditions: none - run fetch_recent_weather.py --national")
    elif COND.exists():
        shutil.copyfile(COND, out / "conditions.json")
        cond = json.loads(COND.read_text(encoding="utf-8"))
        cond_js = "window.SOPP_COND=" + json.dumps(cond, separators=(",", ":")) + ";"
        print(f"  conditions: {cond['last_day']} ({cond['lag_days']}d behind)")

    trails_js = "window.SOPP_TRAILS=null;window.SOPP_TRAIL_INDEX=null;"
    if national and NAT_TRAILS_INDEX.exists():
        # Per fylke, copied out as files. Only the index rides in the page -
        # about 2 kB of names and bounding boxes - and the interface fetches a
        # fylke when the view reaches it and the switch is on. Inlining the
        # geometry is what the Kristiansand build does and what the mainland
        # cannot: 296 kB against roughly 8 MB, on a 0.27 MB page.
        idx = json.loads(NAT_TRAILS_INDEX.read_text(encoding="utf-8"))
        dst = out / "trails"
        dst.mkdir(parents=True, exist_ok=True)
        total = 0
        for f in idx["fylker"]:
            src = NAT_TRAILS / f["file"]
            if not src.exists():
                raise SystemExit(
                    f"{src} is named in the trail index and is not on disk - "
                    "run scripts/fetch_trails.py --national")
            shutil.copyfile(src, dst / f["file"])
            total += src.stat().st_size
        trails_js = ("window.SOPP_TRAILS=null;window.SOPP_TRAIL_INDEX="
                     + json.dumps(idx, separators=(",", ":")) + ";")
        print(f"  trails: {len(idx['fylker'])} fylker, "
              f"{sum(f['lines'] for f in idx['fylker']):,} routes, "
              f"{total / 1e6:.1f} MB fetched per region on demand")
    elif national:
        print("  trails: none - run fetch_trails.py --national")
    elif TRAILS.exists():
        tr = json.loads(TRAILS.read_text(encoding="utf-8"))
        trails_js = ("window.SOPP_TRAILS=" + json.dumps(tr, separators=(",", ":"))
                     + ";window.SOPP_TRAIL_INDEX=null;")
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
        if (NAT_CTX / "meta.json").exists():
            n2, size2 = copy_flat(NAT_CTX, out / "context")
            print(f"  context: {n2:,} tiles, {size2 / 1e6:.1f} MB")

    # --- everything else the host serves --------------------------------
    if (WEB / "manifest.webmanifest").exists():
        (out / "manifest.webmanifest").write_text(manifest_text(national),
                                                  encoding="utf-8")
    else:
        print("  WARNING: manifest.webmanifest missing from web/")
    if (WEB / "sw.js").exists():
        shutil.copyfile(WEB / "sw.js", out / "sw.js")
    else:
        print("  WARNING: sw.js missing from web/")
    # The privacy policy is a real page rather than a panel inside the app, so
    # that it has an address: somebody deciding whether to sign up has to be
    # able to read it without installing anything first, and a login e-mail or
    # a store listing needs a link it can point at. A build without it is a
    # build that promises things in settings it does not deliver, so this is
    # loud rather than silent.
    # Stale pages from an earlier build get uploaded along with everything
    # else, and a file that is no longer written is not a file that goes away.
    # Renaming 404.html to ikke-funnet.html left the old one sitting in the
    # output, where Cloudflare Pages went on treating it as the site's
    # automatic not-found page - so the rename appeared to change nothing.
    # Only the top-level pages: the tile directories are the bulk of the build
    # and clearing them would mean re-copying 154 MB every time.
    keep = {"index.html", "personvern.html"}
    for stale in out.glob("*.html"):
        if stale.name not in keep:
            stale.unlink()
            print(f"  removed stale {stale.name} from a previous build")

    if (WEB / "personvern.html").exists():
        shutil.copyfile(WEB / "personvern.html", out / "personvern.html")
    else:
        print("  WARNING: personvern.html missing from web/ - "
              "the app links to a page that will 404")
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
        "  Cache-Control: public, max-age=31536000, immutable\n"
        "/context/*\n"
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
    # The zone rule comes first and is a passthrough: a tile that exists is a
    # static file and never reaches the rules at all, and one that does not
    # rewrites to itself and 404s instead of being swallowed by the fallback
    # below. Without it a failed tile upload returns the page, with a 200, and
    # the map shows no zones while nothing anywhere reports a problem.
    (out / "_redirects").write_text(
        # One rule, because Cloudflare Pages will not give us two.
        #
        # A missing tile ideally 404s: a rewrite that returns the whole app
        # with a 200 makes a failed upload indistinguishable from a working
        # site. Three deploys were spent trying to get that, and the platform
        # does not offer it alongside a single-page fallback:
        #
        #   * `/zones/*  /zones/:splat  200` - a rewrite of a path to itself -
        #     does not stop `/*` claiming the path when no file matches.
        #   * `/zones/*  /some.html  404` is ignored. What appeared to work was
        #     Cloudflare serving a root 404.html of its own accord.
        #   * and that automatic 404.html overrides `/*  /index.html  200` for
        #     every unmatched path, so shipping one broke deep links: /steder
        #     came back 404 instead of opening the app.
        #
        # The fallback wins, because the app genuinely needs it and a reader
        # who opens a shared link must land on the map. The failure it used to
        # guard against is checked directly instead: scripts/check_deployed.py
        # samples real tiles out of the payload's own manifest and requires a
        # PNG back, which tests an incomplete upload rather than a proxy for
        # one. The app only ever requests tiles the payload lists, so a missing
        # tile is not a path it takes in normal use.
        "/*  /index.html  200" + chr(10),
        encoding="utf-8")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    page_mb = len(page.encode("utf-8")) / 1e6
    print(f"\n  index.html        {page_mb:6.2f} MB")
    for f in sorted(out.rglob("*")):
        rel = f.relative_to(out).as_posix()
        # The tile directories are thousands of files and are already reported
        # by count and total above; listing them buries everything else.
        if f.is_file() and f.name != "index.html" \
                and not rel.startswith(("zones/", "context/")):
            print(f"  {rel:18s}{f.stat().st_size / 1e6:6.2f} MB")
    print(f"\nhosted build -> {out}  ({total / 1e6:.2f} MB total)")
    if national:
        print("compare: the Kristiansand hosted build is one page and no tiles.")
    else:
        print("compare: the artifact build is 14.77 MB and cannot leave Kristiansand.")
    print("\nserve it locally with:  python scripts/serve_interface.py --dist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

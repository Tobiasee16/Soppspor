"""Draw the zone geometry the app actually produces, over a real Kartverket tile.

    node scripts/dump_zones.js <species-key>... out.json
    python scripts/preview_map.py out.json out.png "Title" [dark]


Not a mock: the rings come from the shipped marching-squares code via
dump_zones.js, and the fills and strokes are the exact rgba strings the canvas
would use. The point is to look at the result, because the palette was chosen
by measuring colour distance and never by seeing it on a map.
"""
import json, sys, io, math, urllib.request
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from matplotlib.path import Path
from matplotlib.patches import PathPatch
from PIL import Image

SRC, OUT, TITLE = sys.argv[1], sys.argv[2], sys.argv[3]
DARK = len(sys.argv) > 4 and sys.argv[4] == "dark"
z = json.load(open(SRC, encoding="utf-8"))
GW, GH = z["gw"], z["gh"]

# The model grid in EPSG:25833, from the payload's own metadata.
pl = open("data/processed/web_preview/payload.js", encoding="utf-8").read()
meta = json.loads(pl[pl.index("=")+1:].rstrip().rstrip(";"))["meta"]
CELL, OX, OY = meta["cell_m"], meta["origin_x"], meta["origin_y"]

# Kartverket tiles behind it, same service the app uses.
TOX, TOY, RES0, TSZ = -2500000.0, 9045984.0, 21664.0, 256
lvl = 10
res = RES0 / 2**lvl; span = TSZ * res
x0, y1 = OX, OY
x1, y0 = OX + GW*CELL, OY - GH*CELL
c0, c1 = int((x0-TOX)//span), int((x1-TOX)//span)
r0, r1 = int((TOY-y1)//span), int((TOY-y0)//span)
canvas = Image.new("RGB", ((c1-c0+1)*TSZ, (r1-r0+1)*TSZ), "#eef1f4")
layer = "topograatone"
for r in range(r0, r1+1):
    for c in range(c0, c1+1):
        url = (f"https://cache.kartverket.no/v1/wmts/1.0.0/{layer}"
               f"/default/utm33n/{lvl}/{r}/{c}.png")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "soppspor-preview (support@soppspor.no)"})
            im = Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=60).read()))
            canvas.paste(im.convert("RGB"), ((c-c0)*TSZ, (r-r0)*TSZ))
        except Exception as e:
            print("tile miss", r, c, e)
ext = [TOX + c0*span, TOX + (c1+1)*span, TOY - (r1+1)*span, TOY - r0*span]

fig, ax = plt.subplots(figsize=(7.2, 9.6), dpi=140)
ax.imshow(np.asarray(canvas), extent=ext, origin="upper", interpolation="bilinear")
if DARK:
    ax.add_patch(plt.Rectangle((ext[0], ext[2]), ext[1]-ext[0], ext[3]-ext[2],
                               facecolor=(10/255, 12/255, 16/255), alpha=.42, zorder=2))

def rgba(s):
    v = s[s.index("(")+1:s.index(")")].split(",")
    return (int(v[0])/255, int(v[1])/255, int(v[2])/255,
            float(v[3]) if len(v) > 3 else 1.0)

def to_world(ring):
    # grid cell coords -> EPSG:25833, the same transform drawZones applies
    return [(OX + 0.5*CELL + x*CELL, OY - 0.5*CELL - y*CELL) for x, y in ring]

for sp in z["species"]:
    for lv in sp["levels"]:
        rings = [to_world(r) for r in lv["rings"] if len(r) > 2]
        if not rings:
            continue
        verts, codes = [], []
        for r in rings:
            verts.extend(r + [r[0]])
            codes.extend([Path.MOVETO] + [Path.LINETO]*(len(r)-1) + [Path.CLOSEPOLY])
        pp = PathPatch(Path(verts, codes), facecolor=rgba(lv["fill"]),
                       edgecolor=rgba(lv["line"]), linewidth=lv.get("w", 1.2)*0.9,
                       zorder=3)
        ax.add_patch(pp)

ax.set_xlim(OX, OX + GW*CELL); ax.set_ylim(OY - GH*CELL, OY)
ax.set_xticks([]); ax.set_yticks([])
ax.set_title(TITLE + ("  (mørkt tema)" if DARK else ""), fontsize=11)
handles = [plt.Line2D([0], [0], marker="s", linestyle="", markersize=10,
                      markerfacecolor=sp["colour"], markeredgecolor="none",
                      label=sp["no"]) for sp in z["species"]]
if len(handles) > 1:
    ax.legend(handles=handles, loc="lower left", fontsize=9, framealpha=.92)
fig.tight_layout()
fig.savefig(OUT, bbox_inches="tight")
print("wrote", OUT)

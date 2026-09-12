/* Boot the real shell, select species, and dump the zone geometry the app
   would actually stroke.
 *
 * Half of this interface can only be judged by looking at it, and until this
 * existed there was no way to look without a phone in your hand. The palette
 * for several species at once was chosen by measuring colour distance under
 * three kinds of colour blindness, all of which it passed - and the first
 * render showed the map turning to mud anyway, because the species are
 * spatially correlated and three translucent fills stacked into one wash. No
 * amount of unit testing would have said so.
 *
 * Pairs with scripts/preview_map.py, which draws the output over a real
 * Kartverket tile:
 *
 *   node scripts/dump_zones.js cantharellus_cibarius boletus_edulis out.json
 *   python scripts/preview_map.py out.json out.png "Two species"
 *
 * With no species named it dumps the first one. The geometry comes from the
 * shipped marching-squares code and the colours are the exact rgba strings the
 * canvas would use, so what you are looking at is the app, not a mock.
 */
const fs = require("fs"), path = require("path");
const REPO = path.join(__dirname, "..");

// Record what Path2D is asked to draw, instead of discarding it.
class RecPath {
  constructor(){ this.rings=[]; this.cur=null; }
  moveTo(x,y){ this.cur=[[x,y]]; this.rings.push(this.cur); }
  lineTo(x,y){ if(this.cur) this.cur.push([x,y]); }
  closePath(){}
}
const stub = require(path.join(REPO,"tests","domstub.js"));
global.Path2D = RecPath;

const payload = fs.readFileSync(path.join(REPO,"data/processed/web_preview/payload.js"),"utf8");
(0,eval)(payload.replace(/^window\./,"global.window."));
global.window.SOPP = global.window.SOPP || global.SOPP;
global.window.SOPP_BASE = {};
global.window.SOPP_COND = JSON.parse(
  fs.readFileSync(path.join(REPO,"data/processed/conditions.json"),"utf8"));
global.window.SOPP_TRAILS = null;

const shell = fs.readFileSync(path.join(REPO,"web","shell.html"),"utf8")
  .split("\r\n").join("\n");
const js = shell.slice(shell.indexOf("<script>")+8, shell.lastIndexOf("</script>"));
const at = js.lastIndexOf("})();");
(0,eval)(js.slice(0,at) + `
;globalThis.__a={SEL:()=>SEL,D,setSel,toggleSpecies,spColour,
  levelsFor,levelsForOverlay,GW:()=>GW,GH:()=>GH,repaint};` + js.slice(at));
const a = globalThis.__a;
stub.flush(2);

const want = process.argv.slice(2);
const pick = want.length
  ? want.map(k => a.D.species.find(s=>s.key===k)).filter(Boolean)
  : [a.D.species[0]];
a.setSel(pick);
a.repaint();

const out = { gw:a.GW(), gh:a.GH(), species:[] };
a.SEL().forEach(sp => {
  const z = sp._zones;
  out.species.push({
    key: sp.key, no: sp.no,
    colour: a.spColour(sp, a.D.species.indexOf(sp)),
    levels: z.levels.map((lv,i) => ({
      p: lv.p, fill: lv.fill, line: lv.line,
      rings: z.paths[i] ? z.paths[i].rings : []
    }))
  });
});
fs.writeFileSync(process.argv[process.argv.length-1].endsWith(".json")
  ? process.argv[process.argv.length-1] : "zones.json", JSON.stringify(out));
console.log("dumped", out.species.map(s=>s.no+" ("+s.colour+")").join(", "));

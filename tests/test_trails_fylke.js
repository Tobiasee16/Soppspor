/* Marked routes, once they stopped fitting in the page.
 *
 * Kristiansand inlines one 296 kB pack. The mainland is 150,215 lines and
 * about 8 MB, so it ships as one file per fylke and `ensureTrails` fetches the
 * ones the view touches. That selection is the new risk, and every way it can
 * go wrong is quiet:
 *
 *   - too eager, and switching the layer on downloads all of Norway on a
 *     phone, which is the thing splitting per fylke was meant to avoid;
 *   - too shy, and a reader pans into Telemark and the paths simply stop, with
 *     nothing on screen to say a file was never asked for;
 *   - no once-only guard, and a fylke is re-requested every animation frame -
 *     sixty times a second over a bad connection, which is how a battery
 *     disappears in a wood.
 *
 * None of those throws. So the selection is driven here against a stubbed
 * fetch and a movable view, and what is asserted is which files were asked for
 * and how many times.
 */
const fs = require("fs");
const path = require("path");
const REPO = path.join(__dirname, "..");

const shell = fs.readFileSync(path.join(REPO, "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");

function grab(name) {
  const start = shell.indexOf("function " + name + "(");
  if (start < 0) throw new Error("no function " + name + " in the shell");
  let i = shell.indexOf("{", start), depth = 0;
  for (; i < shell.length; i++) {
    if (shell[i] === "{") depth++;
    else if (shell[i] === "}") { depth--; if (!depth) break; }
  }
  return shell.slice(start, i + 1);
}

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

/* Four fylker on a line, 100 km wide each, so which ones a view touches is
   arithmetic rather than geography. */
const INDEX = {
  fylker: [
    { slug: "a", file: "a.json", bbox: [0, 0, 100000, 100000] },
    { slug: "b", file: "b.json", bbox: [100000, 0, 200000, 100000] },
    { slug: "c", file: "c.json", bbox: [200000, 0, 300000, 100000] },
    { slug: "d", file: "d.json", bbox: [300000, 0, 400000, 100000] },
  ],
};

function harness(opts) {
  const asked = [];
  const env = {
    TR_INDEX: INDEX,
    TR_STATE: {},
    TR_PACKS: [],
    PREF: { trails: true },
    CW: 400, CH: 400,
    view: { cx: 50000, cy: 50000, mpp: 50 },   // 20 km across, inside "a"
    requestDraw: function () {},
    trailPack: function (d) { return { d: d, buckets: [] }; },
    fetch: function (u) {
      asked.push(u);
      return Promise.resolve(opts && opts.fail
        ? { ok: false }
        : { ok: true, json: function () { return Promise.resolve({ stub: u }); } });
    },
  };
  const fn = new Function("env", "with(env){" + grab("ensureTrails")
    + "\nreturn ensureTrails;}")(env);
  return { env: env, asked: asked, run: fn };
}

(async function () {
  /* ---- only what the view touches ---- */
  {
    const h = harness();
    h.run();
    check("a view inside one fylke asks for one file",
      h.asked.length === 1 && h.asked[0] === "trails/a.json", h.asked.join(","));
  }

  /* ---- a view straddling a border takes both ---- */
  {
    const h = harness();
    h.env.view.cx = 100000;          // exactly on the a/b boundary
    h.run();
    check("a view on a border asks for both fylker",
      h.asked.length === 2 && h.asked.indexOf("trails/b.json") >= 0,
      h.asked.join(","));
  }

  /* ---- and never the whole country ---- */
  {
    const h = harness();
    h.run();
    check("switching the layer on does not download Norway",
      h.asked.indexOf("trails/d.json") < 0, h.asked.join(","));
  }

  /* ---- the once-only guard ---- */
  {
    const h = harness();
    h.run(); h.run(); h.run();
    check("a fylke is asked for once however many frames pass",
      h.asked.length === 1, h.asked.length + " requests");
  }

  await new Promise(function (r) { setTimeout(r, 0); });

  /* ---- a failure is remembered, not retried forever ---- */
  {
    const h = harness({ fail: true });
    h.run();
    await new Promise(function (r) { setTimeout(r, 0); });
    const after = h.asked.length;
    h.run(); h.run();
    check("a failed fetch is not retried on every frame",
      h.asked.length === after, h.asked.length + " requests after failure");
    check("and the failure is recorded rather than left pending",
      h.env.TR_STATE.a === "fail", h.env.TR_STATE.a);
  }

  /* ---- the switch gates it ---- */
  {
    const h = harness();
    h.env.PREF.trails = false;
    h.run();
    check("nothing is fetched while the layer is switched off",
      h.asked.length === 0, h.asked.length + " requests");
  }

  /* ---- a build with no index does nothing at all ---- */
  {
    const h = harness();
    h.env.TR_INDEX = null;
    h.run();
    check("a build with no index asks for nothing", h.asked.length === 0);
  }

  console.log(fails ? "\n" + fails + " FAILED" : "\nall passed");
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });

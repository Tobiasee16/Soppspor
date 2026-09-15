/* The Pages Function that serves the conditions layer out of Workers KV.
 *
 * This route exists because the conditions layer stopped being part of the
 * deployment. That buys a daily refresh that cannot damage the map, and it
 * costs the one guarantee a static file gave for free: that the bytes are
 * there. Everything that can now go wrong goes wrong at runtime, in a place no
 * deployment check reaches, so each failure is pinned here against stubs that
 * can tell the wrong answer from the right one.
 *
 * The precedence rule is the whole reason this file is as careful as it is.
 * **A function beats a static asset at the same path.** The first version of
 * this route was written on the opposite assumption, shipped, and answered
 * every request with a 503 because no KV namespace existed yet - the live
 * conditions layer went down and the deployment check passed, because it was
 * reading an edge-cached copy of the previous deployment. So the case that
 * gets the most attention here is the one nobody thought was a case at all:
 * no binding, which is simply what this project looked like before KV.
 *
 * The four states that matter:
 *
 *   - a populated namespace, which is the ordinary day once KV exists;
 *   - a bound but empty one, the window between creating the namespace and the
 *     first scheduled run;
 *   - no binding at all, which must serve whatever the build shipped rather
 *     than failing, or deploying this file is a regression;
 *   - HEAD, because the scheduled job decides whether to write by reading the
 *     served day out of a header. If HEAD and GET ever disagree, the job that
 *     keeps this layer fresh is the thing that breaks.
 *
 * The module is ESM, as Pages Functions must be, and there is no package.json,
 * so it is loaded the way the other suites load the shell: read the source,
 * drop the export keywords, and evaluate it.
 */
const fs = require("fs");
const path = require("path");
const REPO = path.join(__dirname, "..");

const SRC = fs.readFileSync(
  path.join(REPO, "functions", "conditions.json.js"), "utf8");

/* eslint-disable no-new-func */
const load = new Function(
  SRC.split("export async function").join("async function")
     .split("export function").join("function")
   + "\nreturn { onRequestGet, onRequestHead };");
const route = load();

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

const BODY = JSON.stringify({ last_day: "2026-09-14", lag_days: 1, grid: {} });
const FILE = JSON.stringify({ last_day: "2026-09-13", lag_days: 1, grid: {} });

/* A namespace that records how it was asked, so "one read per request" can be
   asserted rather than hoped for: a 1.19 MB value fetched twice to answer one
   request is a bug you only notice on the bill. */
function kv(value, metadata) {
  const calls = { get: 0, getWithMetadata: 0, opts: null };
  return {
    calls,
    get(k, opts) { calls.get++; calls.opts = opts; return Promise.resolve(value); },
    getWithMetadata(k, opts) {
      calls.getWithMetadata++;
      calls.opts = opts;
      if (k !== "norge") throw new Error("unexpected key " + k);
      return Promise.resolve({ value, metadata: metadata || null });
    },
  };
}

/* `context.next()` hands the request to the asset server. Two answers matter:
   the file the build shipped, and the SPA fallback that `_redirects` turns
   every unknown path into - status 200, body HTML. */
const servesFile = (env) => ({
  env,
  next: () => Promise.resolve(new Response(FILE, {
    status: 200, headers: { "content-type": "application/json; charset=utf-8" } })),
});
const servesTheApp = (env) => ({
  env,
  next: () => Promise.resolve(new Response("<!doctype html><title>Soppspor</title>", {
    status: 200, headers: { "content-type": "text/html; charset=utf-8" } })),
});

(async function () {
  /* ---- no binding: what this project looked like before KV existed ---- */
  {
    const r = await route.onRequestGet(servesFile({}));
    const text = await r.text();
    check("with no KV binding the shipped file is served, not an error",
      r.status === 200, "got " + r.status);
    check("and it is the file's bytes", text === FILE);
  }
  {
    const r = await route.onRequestGet(servesTheApp({}));
    check("a build with no conditions file answers 404, not the app as JSON",
      r.status === 404, "got " + r.status);
    check("and it is JSON, so nothing tries to parse a page",
      (r.headers.get("content-type") || "").indexOf("application/json") === 0);
  }

  /* ---- the ordinary day, once KV is populated ---- */
  {
    const store = kv(BODY, { last_day: "2026-09-14", written: "2026-09-15T05:41:00Z" });
    const r = await route.onRequestGet(servesFile({ CONDITIONS: store }));
    const text = await r.text();
    check("a populated namespace answers 200", r.status === 200, "got " + r.status);
    check("KV wins over the shipped file", text === BODY);
    check("it is served as JSON",
      (r.headers.get("content-type") || "").indexOf("application/json") === 0,
      r.headers.get("content-type"));
    check("it is not cached, as the static file was not",
      r.headers.get("cache-control") === "no-cache",
      r.headers.get("cache-control"));
    check("the served day is in a header, so nothing pulls 1.19 MB to read it",
      r.headers.get("x-conditions-last-day") === "2026-09-14",
      r.headers.get("x-conditions-last-day"));
    check("one KV read answers one request",
      store.calls.getWithMetadata === 1 && store.calls.get === 0,
      "getWithMetadata " + store.calls.getWithMetadata + ", get " + store.calls.get);
    check("the read is cached at the edge",
      store.calls.opts && store.calls.opts.cacheTtl >= 60,
      JSON.stringify(store.calls.opts));
  }

  /* ---- bound, but nothing written yet ---- */
  {
    const r = await route.onRequestGet(servesFile({ CONDITIONS: kv(null) }));
    check("an empty namespace falls back to the shipped file",
      r.status === 200 && (await r.text()) === FILE, "got " + r.status);
  }
  {
    const r = await route.onRequestGet(servesTheApp({ CONDITIONS: kv(null) }));
    check("empty namespace and no file is a clean 404", r.status === 404,
      "got " + r.status);
    check("404 is a status the app treats as 'no conditions'", !r.ok);
  }

  /* ---- HEAD, which the scheduled job depends on ---- */
  {
    const g = await route.onRequestGet(servesFile({ CONDITIONS: kv(BODY, { last_day: "2026-09-14" }) }));
    const h = await route.onRequestHead(servesFile({ CONDITIONS: kv(BODY, { last_day: "2026-09-14" }) }));
    check("HEAD agrees with GET on status", h.status === g.status,
      h.status + " vs " + g.status);
    check("HEAD agrees with GET on the served day",
      h.headers.get("x-conditions-last-day") === g.headers.get("x-conditions-last-day"));
    check("HEAD carries no body", (await h.text()) === "");
  }
  {
    const g = await route.onRequestGet(servesTheApp({}));
    const h = await route.onRequestHead(servesTheApp({}));
    check("HEAD agrees with GET when there is nothing to serve",
      h.status === g.status && h.status === 404, h.status + " vs " + g.status);
    check("and reports no day, so the scheduler does not think it is fresh",
      !h.headers.get("x-conditions-last-day"));
  }

  /* ---- the regression this route shipped with, pinned ---- */
  {
    const r = await route.onRequestGet(servesFile({}));
    check("an absent binding is never a 5xx - that took the layer down once",
      r.status < 500, "got " + r.status);
  }

  console.log(fails ? "\n" + fails + " FAILED" : "\nall passed");
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });

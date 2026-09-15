/**
 * The conditions layer, served from KV when there is one, and out of the
 * deployment when there is not.
 *
 * Every other file this site serves is static and is replaced by rebuilding
 * the whole site. That does not work for the weather. A Cloudflare Pages
 * deployment is an immutable, whole-directory snapshot, and this site is about
 * 196 MB of zone, mask and context tiles that come from an overnight
 * prediction run and are not in the repository. Redeploying all of it daily to
 * change one 1.19 MB file means a scheduled job that can, on a bad run, remove
 * the map from the live site. CLAUDE.md asks for the conditions layer to
 * "track seNorge on a schedule rather than being baked in at build time", and
 * this is the part that makes it a different thing from the build.
 *
 * So: the daily job writes only to KV, touches no deployment, and cannot
 * damage the map. The worst it can do is leave the previous reading in place.
 *
 * **A function beats a static asset at the same path, not the other way
 * round.** This file was first written on the opposite assumption and shipped
 * on it. The static `conditions.json` did not shadow this route; this route
 * shadowed the file, and answered every request with the 503 it used to return
 * for a missing binding - taking the live conditions layer down until it was
 * measured. See FINDINGS 26c.
 *
 * Hence `passThrough`. An absent binding is not an error here: it is the state
 * this project is in before the KV namespace exists, and the honest answer is
 * the file the build already ships. That makes the route additive rather than
 * a switch that has to be thrown in the right order, and makes SOPP_CONDITIONS
 * a question of what to ship rather than of whether the app works.
 */

/* The app reads the body and the age; nothing reads the headers. They are set
   because a 1.19 MB body is an expensive way to answer "how old is this", and
   the scheduled job can ask with HEAD instead. */
function headers(meta) {
    const h = {
        "content-type": "application/json; charset=utf-8",
        /* Same as the _headers rule the static file has. The app also asks
           with cache: "no-store", and the service worker is network-first
           here, so a reader offline still gets the last reading it saw. */
        "cache-control": "no-cache",
        "x-content-type-options": "nosniff",
        "access-control-allow-origin": "*",
    };
    if (meta && meta.last_day) h["x-conditions-last-day"] = meta.last_day;
    if (meta && meta.written) h["x-conditions-written"] = meta.written;
    return h;
}

/* Hand the request back to the asset server: whatever the build shipped at
   this path, which before KV exists is the real conditions file.
 *
 * `_redirects` ends with `/*  /index.html  200`, so a path with no file behind
 * it does not 404 - it comes back as the app, with status 200 and a body of
 * HTML. Returning that as the conditions layer would have the app parse a page
 * as JSON, so the content type is checked rather than the status. */
async function passThrough(context) {
    const r = await context.next();
    const ct = r.headers.get("content-type") || "";
    if (r.ok && ct.indexOf("application/json") === 0) return r;
    return new Response(JSON.stringify({ error: "no conditions layer in this build" }),
                        { status: 404, headers: headers(null) });
}

async function fromKv(env) {
    /* getWithMetadata in one call: the alternative is two KV reads to answer
       one request. cacheTtl is the edge cache in front of KV - 60 s is the
       minimum it accepts, and the data changes daily, so this costs nothing
       and takes almost every request off the origin. */
    return env.CONDITIONS.getWithMetadata("norge", { type: "text", cacheTtl: 60 });
}

export async function onRequestGet(context) {
    const { env } = context;
    if (!env.CONDITIONS) return passThrough(context);
    const got = await fromKv(env);
    /* A bound namespace with nothing in it is the window between creating it
       and the first scheduled run. The file is still the better answer. */
    if (got.value === null) return passThrough(context);
    return new Response(got.value, { status: 200, headers: headers(got.metadata) });
}

/* The scheduled job reads the served day from a header, so HEAD has to agree
   with GET. Without a handler it would fall through to the asset router and
   answer differently from GET, which is how the thing that keeps this layer
   fresh would break. */
export async function onRequestHead(context) {
    const { env } = context;
    if (!env.CONDITIONS) {
        const r = await passThrough(context);
        return new Response(null, { status: r.status, headers: headers(null) });
    }
    const got = await fromKv(env);
    if (got.value === null) {
        const r = await passThrough(context);
        return new Response(null, { status: r.status, headers: headers(null) });
    }
    return new Response(null, { status: 200, headers: headers(got.metadata) });
}

/* Deleting an account, which is the one control in the app that destroys
 * something irreplaceable.
 *
 * It exists because the team decided spots are stored as readable rows rather
 * than encrypted, protected by row-level security and a policy of not looking.
 * That is a legitimate choice and it makes two things load-bearing: saying
 * plainly what is held, and letting somebody take all of it back. A "we don't
 * look" promise with a delete button that half-works is worse than no button.
 *
 * So: it must be hard to hit by accident, it must clear the device even when
 * the network fails, and it must never report success it did not achieve.
 */
const fs = require("fs");
const path = require("path");
const stub = require("./domstub.js");

const REPO = path.join(__dirname, "..");
const payload = fs.readFileSync(
  path.join(REPO, "data/processed/web_preview/payload.js"), "utf8");
(0, eval)(payload.replace(/^window\./, "global.window."));
global.window.SOPP = global.window.SOPP || global.SOPP;
global.window.SOPP_BASE = {};
global.window.SOPP_COND = null;
global.window.SOPP_TRAILS = null;
global.window.SOPP_SUPABASE = { url: "https://example.supabase.co", anon: "anon-key" };

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

// A session must exist before boot, or the app is signed out and the server
// path is never exercised.
const store = global.localStorage;
store.setItem("soppkart.session", JSON.stringify({
  access_token: "tok", refresh_token: "ref",
  expires_at: Math.floor(Date.now() / 1000) + 3600,
  user_id: "u1", anonymous: true }));
store.setItem("soppkart.spots", JSON.stringify([
  { id: "a", e: 76000, n: 6467000, name: "Ved bekken", note: "mye", d: "2026-09-01" },
  { id: "b", e: 76500, n: 6467500, name: "Under grana", note: "", d: "2026-09-02" }]));
store.setItem("soppkart.pulledAt", "2026-09-01T00:00:00Z");

// Record what the app asks the network for.
const calls = [];
let mode = "ok";
global.fetch = (url, opts) => {
  calls.push({ url: String(url), opts });
  if (mode === "fail") return Promise.reject(new Error("offline"));
  if (mode === "http500")
    return Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve("boom") });
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}),
                           text: () => Promise.resolve("") });
};

const shell = fs.readFileSync(path.join(REPO, "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");
const js = shell.slice(shell.indexOf("<script>") + 8, shell.lastIndexOf("</script>"));
const at = js.lastIndexOf("})();");
const probe = js.slice(0, at) + `
;globalThis.__app = { deleteAccount, SPOTS: () => SPOTS,
                      session: () => SESSION, sync: () => SYNC,
                      /* test scaffolding: restore a signed-in state between
                         cases, since deleteAccount deliberately clears it */
                      signIn: (tok) => saveSession({ access_token: tok }),
                      setSpots: (a) => { SPOTS = a; saveSpots(); },
                      syncAfterErase: () => syncNow(true) };` + js.slice(at);
(0, eval)(probe);
const app = globalThis.__app;
stub.flush(2);

check("the app starts with the stored spots", app.SPOTS().length === 2,
      app.SPOTS().length + " spots");
check("and with a session", !!app.session(), JSON.stringify(app.session() || {}).slice(0, 40));

// ------------------------------------------------------ the happy path --
calls.length = 0;
let result = null, threw = null;
app.deleteAccount().then(r => { result = r; }).catch(e => { threw = e; });
setTimeout(() => {
  const rpc = calls.filter(c => c.url.indexOf("delete_my_account") >= 0);
  check("it calls the server exactly once", rpc.length === 1,
        calls.map(c => c.url.replace(/^.*rest\/v1/, "")).join(", "));
  if (rpc.length) {
    const c = rpc[0];
    check("via the definer function, not a bare table delete",
          c.url.endsWith("/rest/v1/rpc/delete_my_account"), c.url);
    check("as a POST", c.opts.method === "POST", c.opts.method);
    check("with the user's own token, not the anon key",
          c.opts.headers.Authorization === "Bearer tok", c.opts.headers.Authorization);
    check("and sends no account identifier of any kind",
          c.opts.body === "{}", String(c.opts.body));
  }
  check("the spots are gone from memory", app.SPOTS().length === 0);
  check("the spots are gone from storage",
        store.getItem("soppkart.spots") === null, String(store.getItem("soppkart.spots")));
  check("the session is gone from storage",
        store.getItem("soppkart.session") === null);
  check("the sync cursor is gone too, so a reinstall cannot resurrect anything",
        store.getItem("soppkart.pulledAt") === null);
  check("it did not throw", !threw, threw && threw.message);
  check("it reports that the server was reached", result === "server", String(result));

  // ----------------------------------------------------- offline --
  app.signIn("tok2");
  app.setSpots([{ id: "c", e: 76000, n: 6467000, name: "x", note: "", d: "2026-09-03" }]);
  mode = "fail";
  let offThrew = null;
  app.deleteAccount().catch(e => { offThrew = e; });
  setTimeout(() => {
    check("with no signal the device is still wiped",
          store.getItem("soppkart.spots") === null && app.SPOTS().length === 0);
    check("and the failure is surfaced rather than swallowed", !!offThrew,
          offThrew && offThrew.message);

    // -------------------------------------------- a server error --
    app.signIn("tok3");
    app.setSpots([{ id: "d", e: 76000, n: 6467000, name: "y", note: "", d: "2026-09-04" }]);
    mode = "http500";
    let errThrew = null;
    app.deleteAccount().catch(e => { errThrew = e; });
    setTimeout(() => {
      check("an HTTP error is surfaced, not reported as success", !!errThrew,
            errThrew && errThrew.message);
      check("and the device is wiped anyway",
            store.getItem("soppkart.spots") === null);

      // ------------------------------------- the confirmation gate --
      const settings = shell.slice(shell.indexOf('id="wipeShow"'),
                                   shell.indexOf('id="wipeShow"') + 1400);
      check("the control is two steps, not one",
            /id="wipeBox"/.test(settings) && /hidden/.test(settings));
      check("it requires a typed word",
            /wipeWord/.test(shell) && /!=="SLETT"/.test(shell));
      check("it warns that it cannot be undone",
            /for godt/.test(shell) || /ikke angres/.test(shell));
      check("the privacy text admits we can technically read the spots",
            /kan<\/i> teknisk sett lese dem/.test(shell));

      // Nothing may sync after somebody has asked to be forgotten. The foreign
      // key would reject a late push anyway, but it would surface as a sync
      // error to someone just told their data was deleted.
      calls.length = 0;
      mode = "ok";
      app.setSpots([{ id: "e", e: 76000, n: 6467000, name: "z", note: "", d: "2026-09-05" }]);
      app.signIn("tok4");
      Promise.resolve(app.sync && null);
      const before = calls.length;
      globalThis.__app.syncAfterErase();
      setTimeout(() => {
        check("no sync is attempted after erasure", calls.length === before,
              calls.map(c => c.url.replace(/^.*rest\/v1/, "")).join(", ") || "none");
        console.log(fails ? "\n" + fails + " FAILED"
                          : "\nall erasure checks passed");
        process.exit(fails ? 1 : 0);
      }, 20);
      return;

      console.log(fails ? `\n${fails} FAILED` : "\nall erasure checks passed");
      process.exit(fails ? 1 : 0);
    }, 20);
  }, 20);
}, 20);

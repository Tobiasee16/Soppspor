/* The first thirty seconds, which is all a tester gives you.
 *
 * The app asks the browser for a position so it can move the map to where the
 * reader actually is. On iOS the permission dialog appears the instant
 * getCurrentPosition is called - and this used to be called during boot, which
 * put a system prompt on top of the welcome screen before the reader had been
 * told what the app is or why it would want to know where they are.
 *
 * That is worse than untidy. Asked cold, people decline; and on iOS a declined
 * location permission cannot be requested again from inside the app, it takes
 * a trip to Settings. Somebody who meant "not now" would have lost the locate
 * button permanently, during the exact minute they were deciding what they
 * thought of the thing.
 *
 * So: no prompt while the welcome is up, one prompt once it closes, and for a
 * returning user - who sees no welcome - a prompt straight away, as before.
 *
 * Runs both cases: this process covers the first launch, then re-runs itself
 * with OB_SEEN=1 for the returning user.
 */
const fs = require("fs");
const path = require("path");
const stub = require("./domstub.js");

const REPO = path.join(__dirname, "..");
const RETURNING = !!process.env.OB_SEEN;

const payload = fs.readFileSync(
  path.join(REPO, "data/processed/web_preview/payload.js"), "utf8");
(0, eval)(payload.replace(/^window\./, "global.window."));
global.window.SOPP = global.window.SOPP || global.SOPP;
global.window.SOPP_BASE = {};
global.window.SOPP_COND = null;
global.window.SOPP_TRAILS = JSON.parse(
  fs.readFileSync(path.join(REPO, "data/interim/trails.json"), "utf8"));

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

const shell = fs.readFileSync(path.join(REPO, "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");
const js = shell.slice(shell.indexOf("<script>") + 8, shell.lastIndexOf("</script>"));

const at = js.lastIndexOf("})();");
if (at < 0) throw new Error("could not find the end of the shell IIFE");
const probe = js.slice(0, at) + `
;globalThis.__app = {
  welcomeOpen: () => document.getElementById("ob").classList.contains("on"),
  closeWelcome: () => obClose(),
};` + js.slice(at);
(0, eval)(probe);
const app = globalThis.__app;
const asks = () => global.navigator.geolocation.calls.length;

if (!RETURNING) {
  console.log("— first launch —");
  check("the welcome is shown", app.welcomeOpen() === true);
  check("no location prompt while the reader is still being introduced",
        asks() === 0, asks() + " call(s)");

  app.closeWelcome();
  check("the welcome is gone", app.welcomeOpen() === false);
  check("the position is asked for once the introduction is done",
        asks() === 1, asks() + " call(s)");

  // Closing again must not raise a second dialog: on iOS that is a second
  // system prompt for a permission the user has already answered.
  app.closeWelcome();
  check("it is asked exactly once", asks() === 1, asks() + " call(s)");

  const opts = global.navigator.geolocation.calls[0].opts || {};
  check("it does not wait forever for a fix",
        typeof opts.timeout === "number" && opts.timeout > 0 && opts.timeout <= 30000,
        "timeout " + opts.timeout + " ms");
  check("a refusal is handled rather than thrown",
        typeof global.navigator.geolocation.calls[0].err === "function");
} else {
  console.log("— returning user —");
  check("no welcome for somebody who has seen it", app.welcomeOpen() === false);
  check("the position is asked for straight away, as before",
        asks() === 1, asks() + " call(s)");
}

if (!RETURNING && !fails) {
  const { spawnSync } = require("child_process");
  const r = spawnSync(process.execPath, [__filename],
                      { env: { ...process.env, OB_SEEN: "1" },
                        encoding: "utf8" });
  process.stdout.write("\n" + (r.stdout || ""));
  if (r.status !== 0) {
    process.stderr.write(r.stderr || "");
    fails++;
  }
}

if (!RETURNING) {
  console.log(fails ? `\n${fails} FAILED` : "\nall first-launch checks passed");
}
process.exit(fails ? 1 : 0);

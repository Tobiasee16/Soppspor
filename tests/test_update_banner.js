/* The banner that says the map you are looking at has been replaced.
 *
 * The shell is served stale-while-revalidate: the cached copy instantly, a
 * fresh one fetched behind you for next launch. That is the right trade for an
 * app used where there is no signal, and it means a reader can be looking at a
 * version that no longer exists anywhere else. The worker notices and posts a
 * message; this is the half that turns that into something on screen.
 *
 * Three properties, and the second is the one that would be got wrong:
 *
 *   - it appears when the worker says the shell moved;
 *   - it never reloads on its own, because the reader may be halfway through
 *     writing a note about where they found something;
 *   - it is offered once, not once per background fetch.
 */
const fs = require("fs");
const path = require("path");

const shell = fs.readFileSync(path.join(__dirname, "..", "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

function grab(name) {
  const start = shell.indexOf("function " + name + "(");
  if (start < 0) throw new Error("no function " + name);
  let i = shell.indexOf("{", start), depth = 0;
  for (; i < shell.length; i++) {
    if (shell[i] === "{") depth++;
    else if (shell[i] === "}") { depth--; if (!depth) break; }
  }
  return shell.slice(start, i + 1);
}

function harness(withWorker = true) {
  const on = new Set();
  const el = { classList: { add: (c) => on.add(c), remove: (c) => on.delete(c) } };
  const clicks = [];
  const go = { addEventListener: (t, fn) => clicks.push(fn) };
  let listener = null;
  const navigator = withWorker
    ? { serviceWorker: { addEventListener: (t, fn) => { if (t === "message") listener = fn; } } }
    : {};
  let reloads = 0;
  const document = {
    getElementById: (id) => (id === "updBanner" ? el : id === "updGo" ? go : null),
  };
  new Function("document", "navigator", "location",
    grab("wireUpdateBanner") + "wireUpdateBanner();")(
    document, navigator, { reload: () => reloads++ });
  return {
    on, clicks,
    send: (data) => listener && listener({ data }),
    reloads: () => reloads,
    wired: () => listener !== null,
  };
}

{
  const h = harness();
  check("it is not showing to begin with", !h.on.has("on"));

  h.send({ type: "shell-updated" });
  check("a changed shell puts the banner up", h.on.has("on"));

  check("and nothing has reloaded", h.reloads() === 0);
}

{
  const h = harness();
  h.send({ type: "something-else" });
  check("another kind of message is ignored", !h.on.has("on"));
  h.send(null);
  check("a message with no data does not throw", true);
}

{
  const h = harness();
  h.send({ type: "shell-updated" });
  h.on.delete("on");                       // as if the reader had dismissed it
  h.send({ type: "shell-updated" });
  check("it is offered once, not once per background fetch", !h.on.has("on"));
}

{
  const h = harness();
  h.send({ type: "shell-updated" });
  h.clicks.forEach((fn) => fn());
  check("tapping it reloads", h.reloads() === 1, String(h.reloads()));
  check("and takes the banner down first", !h.on.has("on"));
}

{
  /* A browser with no worker at all - or the page opened from a file - must
     still boot. */
  const h = harness(false);
  check("with no service worker it wires nothing and does not throw", !h.wired());
}

console.log(fails ? `\n${fails} FAILED` : "\nall update-banner checks passed");
process.exit(fails ? 1 : 0);

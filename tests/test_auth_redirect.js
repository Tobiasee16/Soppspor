/* Where a magic link comes back to.
 *
 * The app used to send no redirect at all, which meant Supabase used the
 * project's single Site URL for every link. One setting, one destination: a
 * link requested from a laptop would open on the deployed site, and testing
 * anywhere meant editing a project-wide value and remembering to put it back.
 * Both auth calls now name their own origin, so the same project serves
 * localhost, a phone on the LAN and the real site at once.
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

function originFor(href) {
  const u = new URL(href);
  const loc = { origin: u.origin, pathname: u.pathname };
  return new Function("location", grab("appOrigin") + "return appOrigin();")(loc);
}

check("a site at the root returns the root",
      originFor("https://soppkart.no/") === "https://soppkart.no/",
      originFor("https://soppkart.no/"));
check("a page filename is stripped, so the link lands on the app not the file",
      originFor("https://soppkart.no/index.html") === "https://soppkart.no/",
      originFor("https://soppkart.no/index.html"));
check("a subpath deployment keeps its subpath",
      originFor("https://user.github.io/soppkart/index.html")
        === "https://user.github.io/soppkart/",
      originFor("https://user.github.io/soppkart/index.html"));
check("localhost with a port is preserved",
      originFor("http://localhost:8765/") === "http://localhost:8765/",
      originFor("http://localhost:8765/"));
check("a LAN address over https is preserved",
      originFor("https://192.168.1.50:8765/") === "https://192.168.1.50:8765/",
      originFor("https://192.168.1.50:8765/"));
check("a deep link back into the app still resolves to the app root",
      originFor("https://soppkart.no/kart") === "https://soppkart.no/",
      originFor("https://soppkart.no/kart"));

// It must survive a context with no location at all rather than throwing -
// the same shell runs inside a published artifact where auth cannot work.
{
  const fn = new Function(grab("appOrigin") + "return appOrigin();");
  let threw = null, val;
  try { val = fn(); } catch (e) { threw = e; }
  check("no location object is survivable, not fatal", !threw, threw && threw.message);
  check("and it yields nothing rather than a broken URL",
        val === null || val === undefined || typeof val === "string", String(val));
}

// Both calls have to send it, or the one that does not silently falls back to
// Site URL and the bug is half-fixed.
check("the sign-in call sends redirect_to",
      /auth\/v1\/otp"\+\(to\?"\?redirect_to="/.test(shell));
check("the add-an-email call sends it too",
      /auth\/v1\/user"\s*\n?\s*\+\(to\?"\?redirect_to="/.test(shell));
check("the value is percent-encoded",
      (shell.match(/encodeURIComponent\(to\)/g) || []).length === 2,
      String((shell.match(/encodeURIComponent\(to\)/g) || []).length) + " of 2");

console.log(fails ? "\n" + fails + " FAILED" : "\nall auth redirect checks passed");
process.exit(fails ? 1 : 0);

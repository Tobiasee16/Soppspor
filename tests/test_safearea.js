/* The notch, the Dynamic Island and the home indicator.
 *
 * These are only visible on real hardware, which is exactly why they need a
 * test: nobody notices a control sitting under a cutout until a user does. The
 * checks are structural - every overlay pinned to an edge must account for the
 * inset on that edge, and the insets must not be counted twice.
 *
 * `env(safe-area-inset-*)` is 0 everywhere that has no cutout, including the
 * published artifact, so none of this changes anything on a device without one.
 */
const fs = require("fs");
const path = require("path");

const shell = fs.readFileSync(path.join(__dirname, "..", "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");
const css = shell.slice(shell.indexOf("<style>"), shell.indexOf("</style>"));

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

function rule(sel) {
  const i = css.indexOf(sel + "{");
  if (i < 0) return null;
  return css.slice(i, css.indexOf("}", i));
}

/* Anything pinned to the top must clear the notch or the Dynamic Island.
   The search bar, the species filter and the conditions are one column now -
   `.topwrap` - and clear the cutout through it rather than each on their own,
   the same arrangement as the three at the bottom left. */
for (const sel of [".topwrap", ".banner", ".panel", ".ob"]) {
  const r = rule(sel);
  check(`${sel} clears the top cutout`, !!r && /safe-area-inset-top/.test(r),
        r ? "" : "rule not found");
}

// Anything pinned to a side must clear it in landscape, where the cutout moves.
// The bottom-left three - credit, legend, scale bar - are in one column now
// and clear the cutout through it rather than each on their own.
for (const sel of [".topwrap", ".botleft", ".banner", ".covernote"]) {
  const r = rule(sel);
  check(`${sel} clears a side cutout`, !!r && /safe-area-inset-left/.test(r),
        r ? "" : "rule not found");
}
check(".fabs clears a right-hand cutout",
      /safe-area-inset-right/.test(rule(".fabs") || ""));
/* The column has to pay for the right-hand side as well: the species filter is
   at that end of the top row now, where it was at the left. */
check(".topwrap clears a right-hand cutout",
      /safe-area-inset-right/.test(rule(".topwrap") || ""));
// And nothing inside it should pay a second time.
for (const sel of [".speciespill", ".condpill"]) {
  const r = rule(sel) || "";
  check(`${sel} does not count the inset twice`, !/safe-area-inset/.test(r), r);
}

// The tab bar is the only thing that sits on the home indicator, so it is the
// only thing that should pay for it.
check("the tab bar clears the home indicator",
      /safe-area-inset-bottom/.test(rule(".tabs") || ""));

// The overlays live inside .stage, which already ends above the tab bar. Adding
// the bottom inset to them as well counted it twice and floated them off the
// edge on every phone with a home indicator.
{
  const stageOverlays = [".botleft", ".scalepill", ".legend", ".credit",
                         ".covernote", ".fabs"];
  const doubled = stageOverlays.filter(
    s => /safe-area-inset-bottom/.test(rule(s) || ""));
  check("no overlay inside the stage counts the bottom inset twice",
        doubled.length === 0, doubled.join(", ") || "none");
}

// Sheets and panels hold text, which must not run under a cutout in landscape.
/* And they must not go back to holding themselves apart with hand-set
   offsets. That is what broke: scale at 14, legend at 40, credit at 74, which
   was correct only while the legend was one row tall. Four species wrapped it
   to three and the credit landed across the first one. */
for (const sel of [".scalepill", ".legend", ".credit"]) {
  const r = rule(sel) || "";
  check(`${sel} is stacked by layout, not by a bottom offset`,
        !/position:absolute/.test(r) && !/bottom:/.test(r),
        r.slice(0, 70));
}

check("the bottom sheet pads for side cutouts",
      /safe-area-inset-left/.test(rule(".card") || ""));
check("full-screen panels pad for side cutouts",
      /safe-area-inset-left/.test(rule(".panel") || ""));

// max() rather than a bare env(): the inset is 0 on most devices and a control
// flush against the screen edge would look broken there.
{
  const bare = [...css.matchAll(/left:env\(safe-area-inset-left\)/g)].length;
  check("side insets keep a minimum margin on devices with no cutout",
        bare === 0, bare + " bare env() on a left edge");
}

// The meta tags that make any of it apply on iOS.
{
  const head = fs.readFileSync(
    path.join(__dirname, "..", "scripts", "build_hosted.py"), "utf8");
  check("viewport-fit=cover is set, or every inset resolves to zero",
        /viewport-fit=cover/.test(head));
  // Read the tag's own content attribute, not the file - the comment above it
  // explains why black-translucent was rejected, and a naive search matches
  // the explanation as readily as the setting.
  const bar = head.match(
    /name="apple-mobile-web-app-status-bar-style"\s+content="([^"]+)"/);
  check("the iOS status bar style is stated rather than defaulted", !!bar,
        bar ? bar[1] : "tag not found");
  check("it is not black-translucent, which would put white system text "
        + "on a pale basemap",
        !!bar && bar[1] !== "black-translucent", bar ? bar[1] : "");
  check("the home-screen name is set", /apple-mobile-web-app-title/.test(head));
}
// --------------------------------------------------- the storage namespace --
// Not a safe-area concern, but the same class of problem: something that looks
// cosmetic and is not. The app was renamed from Soppkart to Soppspor in
// September 2026 and these five keys deliberately did not follow it - changing
// them silently destroys every existing user's saved spots, session and
// settings, and an anonymous account exists only as a refresh token under one
// of them. The namespace is history, not branding, and it stays that way.
{
  const keys = [...shell.matchAll(/localStorage[.][gs]etItem[(]"([^"]+)"/g)]
    .map(m => m[1]);
  const uniq = [...new Set(keys)].sort();
  check("every stored key shares one namespace",
        uniq.length > 0 && uniq.every(k => k.indexOf("soppkart.") === 0),
        uniq.join(", "));
  check("the namespace is the expected five keys", uniq.length === 5,
        uniq.length + ": " + uniq.join(", "));
  check("the reason they must not be renamed is written down",
        shell.indexOf("storage keys are a namespace, not a brand") >= 0);
}


console.log(fails ? "\n" + fails + " FAILED" : "\nall safe-area checks passed");
process.exit(fails ? 1 : 0);

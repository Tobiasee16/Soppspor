/* Does the search sheet keep the field on screen? Asked of a real browser.
 *
 * Every other suite here reads the shell as text or drives it against a stub
 * DOM. Neither can answer a layout question, and that gap cost three fixes for
 * two bugs in one week - each time the tests passed and the phone did not. The
 * reported fault was the same sentence twice: "the suggestions go off-screen
 * when too many pop up."
 *
 * So this one renders the built page in Chromium at phone size, fills the list
 * with more results than fit, and measures where things actually are.
 *
 * It ends by breaking the layout on purpose and checking that the measurements
 * notice. A layout test that cannot fail is the thing it is replacing.
 *
 * Skipped loudly, never silently, when Playwright or the build is missing:
 *   npm install playwright && npx playwright install chromium
 *   python scripts/build_hosted.py --national
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const PAGE = path.join(ROOT, "dist_norge", "index.html");

let chromium;
try {
  ({ chromium } = require("playwright"));
} catch (e) {
  console.log("SKIP  playwright is not installed - npm install playwright");
  process.exit(0);
}
if (!fs.existsSync(PAGE)) {
  console.log("SKIP  no built page - python scripts/build_hosted.py --national");
  process.exit(0);
}

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

/* Enough to overflow any phone. The bug only appears once the list is longer
   than the space for it, which is exactly when a search is going well. */
const RESULTS = 25;

async function measure(page) {
  return page.evaluate(() => {
    const q = document.getElementById("findQ").getBoundingClientRect();
    const sheet = document.getElementById("findSheet").getBoundingClientRect();
    const list = document.getElementById("findList");
    const first = list.querySelector("button");
    return {
      vh: window.innerHeight,
      sheetTop: Math.round(sheet.top),
      fieldTop: Math.round(q.top),
      fieldBottom: Math.round(q.bottom),
      firstRowTop: first ? Math.round(first.getBoundingClientRect().top) : null,
      listScrolls: list.scrollHeight > list.clientHeight + 1,
      listClient: list.clientHeight,
    };
  });
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },      // a phone held upright
    deviceScaleFactor: 2, isMobile: true, hasTouch: true,
  });
  const page = await ctx.newPage();
  // The welcome overlay would cover everything this is trying to measure.
  await page.addInitScript(() => {
    try { localStorage.setItem("soppkart.welcomed", "1"); } catch (e) {}
  });
  page.on("pageerror", (err) =>
    check("the page boots without throwing", false, String(err).slice(0, 120)));

  await page.goto("file:///" + PAGE.replace(/\\/g, "/"));
  await page.waitForSelector("#findBtn", { timeout: 20000 });

  await page.click("#findBtn");
  await page.waitForTimeout(450);               // the field is focused at 260ms
  check("the search sheet opens",
        await page.isVisible("#findSheet"), "");

  /* Straight into the list rather than through the network: this is a question
     about layout, and the ranking is tested against the real registers
     elsewhere. */
  await page.evaluate((n) => {
    const list = document.getElementById("findList");
    list.innerHTML = "";
    for (let i = 0; i < n; i++) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "findRow";
      b.innerHTML = '<span class="nm">Storvatnet ' + (i + 1) +
                    '</span><span class="sub">Innsjø · Narvik · 12 km</span>';
      list.appendChild(b);
    }
  }, RESULTS);
  await page.waitForTimeout(120);

  let m = await measure(page);
  check(`${RESULTS} results do not push the sheet off the top`,
        m.sheetTop >= 0, `sheet top at ${m.sheetTop}px`);
  check("the field you are typing into stays on screen",
        m.fieldTop >= 0 && m.fieldBottom <= m.vh,
        `field ${m.fieldTop}-${m.fieldBottom} in ${m.vh}px`);
  check("the first result sits below the field, not above it",
        m.firstRowTop > m.fieldBottom - 1,
        `row at ${m.firstRowTop}, field ends ${m.fieldBottom}`);
  check("the list is the thing that scrolls",
        m.listScrolls, `list ${m.listClient}px tall`);

  /* A keyboard takes roughly half the screen. Android shrinks the window, which
     is this; iOS overlays it, which findLift handles and the unit tests cover.
     Either way the sheet must still fit what is left. */
  await page.setViewportSize({ width: 390, height: 420 });
  await page.waitForTimeout(250);
  m = await measure(page);
  check("with the keyboard up, the sheet still starts on screen",
        m.sheetTop >= 0, `sheet top at ${m.sheetTop}px of ${m.vh}px`);
  check("and the field is still visible above the results",
        m.fieldTop >= 0 && m.fieldBottom <= m.vh,
        `field ${m.fieldTop}-${m.fieldBottom} in ${m.vh}px`);
  check("and the list still scrolls rather than overflowing",
        m.listScrolls, `list ${m.listClient}px tall`);

  /* The self-check, and it has already earned its place: it was first aimed at
     `min-height:0`, which I had asserted was load-bearing, and it passed -
     removing that declaration changes nothing measurable. The cap is what does
     the work. Take it away and the sheet grows to 1591px and starts 1222px
     above the screen, carrying the field with it, which is the reported bug
     exactly. */
  await page.addStyleTag({
    content: "#findSheet{max-height:none !important}" });
  await page.waitForTimeout(150);
  const broken = await measure(page);
  check("removing the height cap is detected, because that is the real fix",
        broken.sheetTop < 0 || broken.fieldTop < 0,
        `sheet ${broken.sheetTop}, field ${broken.fieldTop}, ` +
        `scrolls ${broken.listScrolls}`);

  await browser.close();
  console.log(fails ? `\n${fails} FAILED` : "\nall layout checks passed");
  process.exit(fails ? 1 : 0);
})().catch((err) => {
  console.log("FAIL  the layout run threw   " + String(err).slice(0, 200));
  process.exit(1);
});

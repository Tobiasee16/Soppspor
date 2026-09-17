/* Does the search panel keep the field on screen? Asked of a real browser.
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
    const tabs = document.querySelector(".tabs").getBoundingClientRect();
    /* Whatever is drawn at the middle of the tab bar. The bar sitting on top of
       the results is the other half of what the screenshots showed. */
    const over = document.elementFromPoint(
      Math.round(tabs.left + tabs.width / 2),
      Math.round(tabs.top + tabs.height / 2));
    return {
      vh: window.innerHeight,
      sheetTop: Math.round(sheet.top),
      fieldTop: Math.round(q.top),
      fieldBottom: Math.round(q.bottom),
      firstRowTop: first ? Math.round(first.getBoundingClientRect().top) : null,
      listScrolls: list.scrollHeight > list.clientHeight + 1,
      listClient: list.clientHeight,
      coversTabs: !!over && !!over.closest("#findSheet"),
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
  check("the panel covers the tab bar rather than being drawn under it",
        m.coversTabs, "the tab bar is on top of the results");

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

  /* The self-check, and it has earned its place twice: aimed first at
     `min-height:0`, which I had asserted was load-bearing and is not, and then
     at the height cap, which was the fix at the time and is not the fix now.
     So it is aimed at the anchor. Put the panel back on the bottom edge and let
     it size to its contents - which is what it was through six failed fixes -
     and it grows past the top of the screen with the field inside it. */
  await page.addStyleTag({
    content: "#findSheet{top:auto !important;bottom:0 !important;" +
             "height:auto !important}" });
  await page.waitForTimeout(150);
  const broken = await measure(page);
  check("re-anchoring the panel to the bottom is detected, which is the fix",
        broken.sheetTop < 0 || broken.fieldTop < 0,
        `sheet ${broken.sheetTop}, field ${broken.fieldTop}, ` +
        `scrolls ${broken.listScrolls}`);

  await ctx.close();

  /* ------------------------------------------------- with a keyboard up --
   * The case that was reported, four times, and could not be reproduced any
   * other way. Chromium shrinks the window for a keyboard; a phone does not.
   * It either shrinks the visual viewport where it stands, or - and this is the
   * one the screenshots showed - scrolls the visible part of the page upward so
   * the focused field stays in view, carrying everything above the field off
   * the top of the screen.
   *
   * Both are faked here, because both broke the old bottom-anchored sheet and
   * only one of them was ever simulated. The panel is anchored to the top now,
   * so the second case has nothing to scroll: the field was never covered. */
  const ios = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const p2 = await ios.newPage();
  await p2.addInitScript(() => {
    try { localStorage.setItem("soppkart.welcomed", "1"); } catch (e) {}
    const t = new EventTarget();
    t.height = window.innerHeight; t.offsetTop = 0; t.offsetLeft = 0;
    t.width = window.innerWidth; t.scale = 1;
    Object.defineProperty(window, "visualViewport", { get: () => t });
    /* `scroll` is the part that mattered. A browser that will not resize the
       window for a keyboard scrolls the visual viewport up instead, and then
       the gap at the bottom of the window is nothing while half the screen is
       still keyboard - which is why a guard written against that gap did
       nothing at all on the phone it was written for. */
    window.__keyboard = (px, scroll) => {
      t.height = window.innerHeight - px;
      t.offsetTop = scroll || 0;
      t.dispatchEvent(new Event("resize"));
    };
  });
  await p2.goto("file:///" + PAGE.replace(/\\/g, "/"));
  await p2.waitForSelector("#findBtn", { timeout: 20000 });
  await p2.click("#findBtn");
  await p2.waitForTimeout(450);
  await p2.evaluate(() => window.__keyboard(336, 0));
  await p2.waitForTimeout(250);
  await p2.evaluate((n) => {
    const l = document.getElementById("findList");
    l.innerHTML = "";
    for (let i = 0; i < n; i++) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "findRow";
      b.innerHTML = '<span class="nm">Storvatnet ' + (i + 1) +
                    '</span><span class="sub">Innsj\u00f8 \u00b7 Narvik \u00b7 12 km</span>';
      l.appendChild(b);
    }
  }, RESULTS);
  await p2.waitForTimeout(200);

  const read = () => p2.evaluate(() => {
    const el = (id) => document.getElementById(id);
    const b = (x) => x.getBoundingClientRect();
    const vv = window.visualViewport;
    const list = el("findList");
    return { sheetTop: Math.round(b(el("findSheet")).top),
             sheetBottom: Math.round(b(el("findSheet")).bottom),
             fieldTop: Math.round(b(el("findQ")).top),
             fieldBottom: Math.round(b(el("findQ")).bottom),
             noteTop: Math.round(b(el("findNote")).top),
             noteBottom: Math.round(b(el("findNote")).bottom),
             listBottom: Math.round(b(list).bottom),
             listScrolls: list.scrollHeight > list.clientHeight + 1,
             visTop: Math.round(vv.offsetTop),
             visBottom: Math.round(vv.offsetTop + vv.height) };
  });

  /* Both shapes of keyboard, and a half-scrolled one for good measure. The
     old sheet failed the scrolled cases and passed the overlay one, which is
     how it shipped twice. */
  for (const scroll of [0, 168, 336]) {
    await p2.evaluate((v) => window.__keyboard(336, v), scroll);
    await p2.waitForTimeout(250);
    const k = await read();
    const where = scroll ? `scrolled ${scroll}px` : "overlaid";
    check(`${where}: a full list does not carry the panel off the top`,
          k.sheetTop >= k.visTop, `panel at ${k.sheetTop}, visible from ${k.visTop}`);
    check(`${where}: the field you are typing into is visible`,
          k.fieldTop >= k.visTop && k.fieldBottom <= k.visBottom,
          `field ${k.fieldTop}-${k.fieldBottom} in ${k.visTop}-${k.visBottom}`);
    check(`${where}: the note under the field is whole, not half cut off`,
          k.noteTop >= k.visTop && k.noteBottom <= k.sheetBottom + 1,
          `note ${k.noteTop}-${k.noteBottom}, panel ends ${k.sheetBottom}`);
    check(`${where}: no result is stranded behind the keys`,
          k.listBottom <= k.visBottom + 1 && k.listScrolls,
          `list ends ${k.listBottom}, visible to ${k.visBottom}`);
  }

  /* An empty list must not eat the note. A negative bottom margin on the last
     flex item shortens the column, and with nothing in the list there is
     nothing to absorb it, so `overflow:hidden` took the bottom of the note -
     the "text under the search pill disappears halfway" half of the report. */
  await p2.evaluate(() => {
    document.getElementById("findList").innerHTML = "";
    window.__keyboard(336, 0);
  });
  await p2.waitForTimeout(220);
  const empty = await read();
  check("with no results at all, the note under the field is still whole",
        empty.noteBottom <= empty.sheetBottom + 1,
        `note ends ${empty.noteBottom}, panel ends ${empty.sheetBottom}`);

  /* The self-check for this half. Anchor the panel to the bottom again, which
     is what it was for six attempts, and let a scrolled viewport do to it what
     the phone did. */
  await p2.evaluate(() => {
    const l = document.getElementById("findList");
    for (let i = 0; i < 25; i++) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "findRow";
      b.innerHTML = '<span class="nm">Storvatnet</span>';
      l.appendChild(b);
    }
  });
  await p2.addStyleTag({
    content: "#findSheet{top:auto !important;bottom:0 !important;" +
             "height:auto !important}" });
  await p2.evaluate(() => window.__keyboard(336, 336));
  await p2.waitForTimeout(200);
  const wrong = await read();
  check("a bottom-anchored panel is detected, because that was the bug",
        wrong.sheetTop < wrong.visTop || wrong.fieldTop < wrong.visTop,
        `panel ${wrong.sheetTop}, field ${wrong.fieldTop}, ` +
        `visible from ${wrong.visTop}`);

  await ios.close();
  await browser.close();
  console.log(fails ? `\n${fails} FAILED` : "\nall layout checks passed");
  process.exit(fails ? 1 : 0);
})().catch((err) => {
  console.log("FAIL  the layout run threw   " + String(err).slice(0, 200));
  process.exit(1);
});

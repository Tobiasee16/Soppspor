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

  await ctx.close();

  /* ------------------------------------------------ the iOS keyboard case --
   * The one that was actually reported, twice, and could not be reproduced any
   * other way. Chromium shrinks the window for a keyboard; iOS does not - it
   * leaves innerHeight alone and shrinks the visual viewport underneath it. So
   * the real case has to be built by hand, and once it was, the fault appeared
   * on the first run.
   *
   * `bottom` is measured from the sheet's containing block, which stops above
   * the tab bar rather than at the window's edge. Lifting by a keyboard height
   * measured from innerHeight therefore raised the sheet by one tab bar too
   * many: with twenty suggestions up it ended 43px above the screen - the tab
   * bar to the pixel - taking the search field behind the status bar with it,
   * which is why the note under it read as cut in half. */
  const ios = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const p2 = await ios.newPage();
  await p2.addInitScript(() => {
    try { localStorage.setItem("soppkart.welcomed", "1"); } catch (e) {}
    const t = new EventTarget();
    t.height = window.innerHeight; t.offsetTop = 0; t.offsetLeft = 0;
    t.width = window.innerWidth; t.scale = 1;
    Object.defineProperty(window, "visualViewport", { get: () => t });
    /* `scroll` is the part that mattered. A browser that cannot resize the
       window for a keyboard scrolls the visual viewport up instead, to keep the
       focused field in view, and then the gap at the bottom is nearly zero
       while half the screen is still keyboard. */
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
    return { sheetTop: Math.round(b(el("findSheet")).top),
             fieldTop: Math.round(b(el("findQ")).top),
             noteTop: Math.round(b(el("findNote")).top),
             noteBottom: Math.round(b(el("findNote")).bottom),
             sheetBottom: Math.round(b(el("findSheet")).bottom),
             visTop: Math.round(vv.offsetTop),
             visBottom: Math.round(vv.offsetTop + vv.height),
             safeTop: (el("satProbe") || {}).offsetHeight || 0 };
  });

  let k = await read();
  check("with an iOS keyboard and a full list, the sheet stays on screen",
        k.sheetTop >= 0, `sheet top at ${k.sheetTop}px`);
  check("the search field is not pushed behind the status bar",
        k.fieldTop >= 0, `field top at ${k.fieldTop}px`);
  check("and the note under it is whole, not half cut off",
        k.noteTop >= 0 && k.noteBottom <= k.sheetBottom + 1,
        `note ${k.noteTop}-${k.noteBottom}, sheet ends ${k.sheetBottom}`);

  /* A notched phone. Chromium reports no inset, so the probe is given one -
     the sheet has to leave room for it rather than filling the viewport. */
  await p2.addStyleTag({ content: "#satProbe{height:47px !important}" });
  await p2.evaluate(() => window.__keyboard(336));
  await p2.waitForTimeout(250);
  k = await read();
  check("a status bar is reserved rather than covered",
        k.sheetTop >= k.safeTop, `sheet top ${k.sheetTop}, inset ${k.safeTop}`);

  /* ------------------------------------------------ the reported phone --
   * This is the one the screenshots showed, and none of the simulations above
   * would have caught it. The browser does not resize the window; it scrolls
   * the visual viewport up so the focused field stays visible. The gap at the
   * bottom is then nearly zero while half the screen is still keyboard, so a
   * guard written against that gap never fires - the sheet was never lifted,
   * never capped, and rode off the top with the rest of the page.
   *
   * Capping by `vv.height` is wrong here too: correct only when the visible
   * area starts at zero, and it leaves the sheet exactly `offsetTop` too tall
   * otherwise. The cap is measured from the visible top instead. */
  await p2.addStyleTag({ content: "#satProbe{height:0 !important}" });
  for (const scroll of [336, 168]) {
    await p2.evaluate((s) => window.__keyboard(336, s), scroll);
    await p2.waitForTimeout(250);
    const v = await read();
    check(`a viewport scrolled ${scroll}px keeps the sheet inside what is visible`,
          v.sheetTop >= v.visTop,
          `sheet top ${v.sheetTop}, visible from ${v.visTop}`);
    check(`and the field with it at ${scroll}px`,
          v.fieldTop >= v.visTop && v.fieldTop <= v.visBottom,
          `field ${v.fieldTop} in ${v.visTop}-${v.visBottom}`);
  }

  /* An empty list must not eat the note. A negative bottom margin on the last
     flex item shortens the column, and with nothing in the list there is
     nothing to absorb it, so `overflow:hidden` took the bottom of the note -
     the "cut in half" half of the report. */
  await p2.evaluate(() => {
    document.getElementById("findList").innerHTML = "";
    window.__keyboard(336, 336);
  });
  await p2.waitForTimeout(220);
  const empty = await read();
  check("with no results at all, the note under the field is still whole",
        empty.noteBottom <= empty.sheetBottom + 1,
        `note ends ${empty.noteBottom}, sheet ends ${empty.sheetBottom}`);

  await p2.evaluate(() => window.__keyboard(336, 0));
  await p2.waitForTimeout(200);

  /* The self-check for this half: compute the lift the old way, from the
     window rather than from the element's own containing block. */
  const baseline = await read();
  await p2.evaluate(() => {
    const s = document.getElementById("findSheet");
    const vv = window.visualViewport;
    s.style.bottom = Math.round(window.innerHeight - vv.height - vv.offsetTop) + "px";
  });
  await p2.waitForTimeout(150);
  const wrong = await read();
  check("measuring the lift from the window instead is detected",
        wrong.sheetTop < baseline.sheetTop,
        `${wrong.sheetTop} vs ${baseline.sheetTop}`);

  await ios.close();
  await browser.close();
  console.log(fails ? `\n${fails} FAILED` : "\nall layout checks passed");
  process.exit(fails ? 1 : 0);
})().catch((err) => {
  console.log("FAIL  the layout run threw   " + String(err).slice(0, 200));
  process.exit(1);
});

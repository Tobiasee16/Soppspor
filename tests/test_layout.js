/* Do the map's top controls stay where they belong? Asked of a real browser.
 *
 * Every other suite here reads the shell as text or drives it against a stub
 * DOM. Neither can answer a layout question, and that gap cost six fixes for
 * one bug: the reported fault was the same sentence four times, "the search
 * pill shoots beyond the roof of my phone when I get suggested places."
 *
 * So this one renders the built page in Chromium at phone size, fills the
 * suggestion list with more results than fit, fakes a keyboard over the bottom
 * of the screen in both of the shapes a phone uses, and measures where things
 * actually are.
 *
 * It ends each half by breaking the layout on purpose and checking that the
 * measurements notice. A layout test that cannot fail is the thing it is
 * replacing.
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

/* Enough to overflow any phone. The bug only appeared once the list was longer
   than the space for it, which is exactly when a search is going well. */
const RESULTS = 25;

/* Put results in the list without going near the network: this is a question
   about layout, and the ranking is tested against the real registers
   elsewhere. Unhiding the dropdown and announcing a viewport change is what
   the app itself does once an answer arrives. */
const fill = (page, n) => page.evaluate((count) => {
  const l = document.getElementById("findList");
  l.innerHTML = "";
  for (let i = 0; i < count; i++) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "findRow";
    b.innerHTML = '<span class="nm">Storvatnet ' + (i + 1) +
                  '</span><span class="sub">Innsjø · Narvik · 12 km</span>';
    l.appendChild(b);
  }
  document.getElementById("findDrop").hidden = false;
  window.dispatchEvent(new Event("resize"));
  if (window.visualViewport)
    window.visualViewport.dispatchEvent(new Event("resize"));
}, n);

const read = (page) => page.evaluate(() => {
  const el = (id) => document.getElementById(id);
  const b = (x) => x.getBoundingClientRect();
  const vv = window.visualViewport;
  const list = el("findList");
  const drop = el("findDrop");
  const field = b(el("findQ"));
  const sp = el("spPill");
  const tabs = document.querySelector(".tabs").getBoundingClientRect();
  return {
    vh: window.innerHeight,
    visTop: vv ? Math.round(vv.offsetTop) : 0,
    visBottom: vv ? Math.round(vv.offsetTop + vv.height) : window.innerHeight,
    fieldTop: Math.round(field.top),
    fieldBottom: Math.round(field.bottom),
    fieldLeft: Math.round(field.left),
    dropTop: Math.round(b(drop).top),
    dropBottom: Math.round(b(drop).bottom),
    dropLeft: Math.round(b(drop).left),
    dropRight: Math.round(b(drop).right),
    listScrolls: list.scrollHeight > list.clientHeight + 1,
    spLeft: sp.offsetParent ? Math.round(b(sp).left) : null,
    spVisible: !!sp.offsetParent,
    tabsTop: Math.round(tabs.top),
    width: window.innerWidth,
  };
});

(async () => {
  const browser = await chromium.launch();

  /* ------------------------------------------------- the controls at rest --
   * Search on the left, species filter on the right, and the map still
   * draggable in the gap between them. */
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
  await page.waitForSelector("#findQ", { timeout: 20000 });
  await page.waitForTimeout(400);

  let m = await read(page);
  check("the search bar is on the left of the top row",
        m.fieldLeft < m.width / 2, `field starts at ${m.fieldLeft}`);
  check("the species filter is on the right of it",
        m.spVisible && m.spLeft > m.fieldLeft,
        `filter at ${m.spLeft}, field at ${m.fieldLeft}`);

  /* The map has to stay draggable in the gaps: the column passes taps through
     and each control takes its own back. */
  const midGap = await page.evaluate(() => {
    const f = document.getElementById("findQ").getBoundingClientRect();
    const s = document.getElementById("spPill").getBoundingClientRect();
    const x = Math.round((f.right + s.left) / 2);
    const y = Math.round(f.top + f.height / 2);
    const hit = document.elementFromPoint(x, y);
    return hit ? hit.id || hit.tagName : "nothing";
  });
  check("the map is still draggable in the gap between them",
        midGap === "map" || midGap === "CANVAS", midGap);

  await page.focus("#findQ");
  await page.waitForTimeout(150);
  await fill(page, RESULTS);
  await page.waitForTimeout(150);
  m = await read(page);

  check("typing gives the field the row to itself",
        !m.spVisible, "the species filter is still showing");
  check("the suggestions hang under the bar, not over it",
        m.dropTop >= m.fieldBottom - 1,
        `list starts ${m.dropTop}, field ends ${m.fieldBottom}`);
  check("the list is the thing that scrolls", m.listScrolls);
  check("the map still shows down both sides of the list",
        m.dropLeft >= 8 && m.width - m.dropRight >= 8,
        `${m.dropLeft}px left, ${m.width - m.dropRight}px right`);
  check("and a long way below it",
        m.tabsTop - m.dropBottom > 100,
        `${m.tabsTop - m.dropBottom}px of map under the list`);

  /* The self-check for this half. The cap is what stops the list growing to
     fit its contents; take it away and it runs down over the whole map. */
  await page.addStyleTag({ content: "#findDrop{max-height:none !important}" });
  await page.waitForTimeout(150);
  const uncapped = await read(page);
  check("removing the cap is detected, because the cap is the fix",
        uncapped.dropBottom > m.dropBottom + 100,
        `${uncapped.dropBottom} vs ${m.dropBottom}`);
  await ctx.close();

  /* ------------------------------------------------- with a keyboard up --
   * The case that was reported, four times, and could not be reproduced any
   * other way. Chromium shrinks the window for a keyboard; a phone does not.
   * It either shrinks the visual viewport where it stands, or - and this is
   * the one the screenshots showed - scrolls the visible part of the page
   * upward so the focused field stays in view, carrying everything above the
   * field off the top of the screen.
   *
   * Both are faked here, because both broke the old bottom-anchored sheet and
   * only one of them was ever simulated. The bar is at the top now, so the
   * second has nothing to reveal: the field was never covered. */
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
  await p2.waitForSelector("#findQ", { timeout: 20000 });
  await p2.focus("#findQ");
  await p2.waitForTimeout(200);
  await fill(p2, RESULTS);

  const KB = 336;
  for (const scroll of [0, 168, 336]) {
    await p2.evaluate((v) => window.__keyboard(336, v), scroll);
    await p2.waitForTimeout(220);
    const k = await read(p2);
    const where = scroll ? `scrolled ${scroll}px` : "overlaid";
    check(`${where}: the field you are typing into is visible`,
          k.fieldTop >= k.visTop && k.fieldBottom <= k.visBottom,
          `field ${k.fieldTop}-${k.fieldBottom} in ${k.visTop}-${k.visBottom}`);
    check(`${where}: no suggestion is stranded behind the keys`,
          k.dropBottom <= k.visBottom + 1,
          `list ends ${k.dropBottom}, visible to ${k.visBottom}`);
    check(`${where}: the map is still visible between list and keyboard`,
          k.visBottom - k.dropBottom >= 60,
          `${k.visBottom - k.dropBottom}px of map showing`);
    check(`${where}: and the list still scrolls rather than overflowing`,
          k.listScrolls);
  }

  /* The self-check for this half: take the keyboard out of the arithmetic by
     making the cap a fixed share of the window, the way it would be if the
     script never ran, and the list reaches past the keys. */
  await p2.evaluate(() => window.__keyboard(336, 0));
  await p2.waitForTimeout(200);
  await p2.addStyleTag({ content: "#findDrop{max-height:700px !important}" });
  await p2.evaluate(() => {
    document.getElementById("findDrop").style.maxHeight = "700px";
  });
  await p2.waitForTimeout(150);
  const wrong = await read(p2);
  check("a list that ignores the keyboard is detected",
        wrong.dropBottom > wrong.visBottom,
        `list ends ${wrong.dropBottom}, visible to ${wrong.visBottom}`);

  await ios.close();
  await browser.close();
  console.log(fails ? `\n${fails} FAILED` : "\nall layout checks passed");
  process.exit(fails ? 1 : 0);
})().catch((err) => {
  console.log("FAIL  the layout run threw   " + String(err).slice(0, 200));
  process.exit(1);
});

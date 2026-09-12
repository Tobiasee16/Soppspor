/* The conditions card now names days rather than counting them, so it depends
   on two things the pipeline could break silently.
 *
 * The first is the daily series. `rainStrip` is scaled to the wettest day in
 * the window, so a series that is short, misaligned, or all-null still draws
 * a perfectly plausible chart - flat, or twenty-one identical bars - and says
 * nothing about it. So the alignment is checked against the file: the series
 * must have exactly one entry per day between first_day and last_day, and its
 * total must be the same rainfall the 21-day mean reports.
 *
 * The second is `dayWord`, which decides whether the reader sees "i går" or a
 * date. It works off Date.now(), so it is tested against a frozen clock; a
 * function whose answer depends on when the suite runs is not a test.
 */
const fs = require("fs");
const path = require("path");
const REPO = path.join(__dirname, "..");

const shell = fs.readFileSync(path.join(REPO, "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");
const COND = JSON.parse(
  fs.readFileSync(path.join(REPO, "data/processed/conditions.json"), "utf8"));

function grab(name) {
  const start = shell.indexOf("function " + name + "(");
  if (start < 0) throw new Error("no function " + name + " in the shell");
  let i = shell.indexOf("{", start), depth = 0;
  for (; i < shell.length; i++) {
    if (shell[i] === "{") depth++;
    else if (shell[i] === "}") { depth--; if (!depth) break; }
  }
  return shell.slice(start, i + 1);
}

const src = ["spanWord", "dayWord", "rainStrip", "lastDayRain"].map(grab).join("\n");

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

// A frozen clock, so "i går" means the same thing in June as in December.
function at(iso, condOverride) {
  const now = Date.parse(iso + "T09:00:00Z");
  return new Function("COND", "Date_", `
    const Date = new Proxy(Date_, { get(t, k) { return k === "now" ? () => ${now} : t[k]; } });
    ${src}
    return { spanWord, dayWord, rainStrip, lastDayRain };`)(
    condOverride || COND, Date);
}

// ---------------------------------------------------------------- windows --
{
  const f = at("2026-09-12");
  check("a 21 day window reads as weeks", f.spanWord(21) === "3 uker", f.spanWord(21));
  check("7 days is one week, singular", f.spanWord(7) === "1 uke", f.spanWord(7));
  check("10 days has no whole weeks, so stays days",
        f.spanWord(10) === "10 døgn", f.spanWord(10));
}

// ------------------------------------------------------------------ days --
{
  const f = at("2026-09-12");
  check("the day before today is 'i går'", f.dayWord("2026-09-11", false) === "i går",
        f.dayWord("2026-09-11", false));
  check("today is 'i dag'", f.dayWord("2026-09-12", false) === "i dag");
  check("two days back is 'i forgårs'", f.dayWord("2026-09-10", false) === "i forgårs");
  check("older than that gets a date, not a word",
        f.dayWord("2026-08-22", false) === "22.8.", f.dayWord("2026-08-22", false));
  check("the date form keeps the word", f.dayWord("2026-09-11", true) === "i går (11.9.)",
        f.dayWord("2026-09-11", true));
  // The same file read a day later must not still claim "i går".
  const later = at("2026-09-14");
  check("the word ages with the clock, it is not baked in",
        later.dayWord("2026-09-11", false) === "11.9.",
        later.dayWord("2026-09-11", false));
}

// --------------------------------------------------- the series itself --
{
  const d = COND.daily_rain_mm;
  check("conditions.json carries a daily series", Array.isArray(d) && d.length > 0,
        d ? d.length + " days" : "missing");
  const days = Math.round(
    (Date.parse(COND.last_day) - Date.parse(COND.first_day)) / 86400000) + 1;
  check("one entry per day of the stated window", d.length === days,
        d.length + " entries, " + days + " days " + COND.first_day + "–" + COND.last_day);
  check("the window is the one the summary totals", days === COND.window_days,
        days + " vs window_days " + COND.window_days);

  // The decisive check: the bars and the headline number are the same rain.
  // A series fetched for the wrong span would pass everything above.
  const sum = d.reduce((a, b) => a + (b || 0), 0);
  const err = Math.abs(sum - COND.summary.rain21_mean);
  check("the daily totals add up to the headline figure", err < 0.6,
        sum.toFixed(1) + " mm vs " + COND.summary.rain21_mean + " mm (± " + err.toFixed(2) + ")");

  const f = at("2026-09-12");
  check("the last day of the series is what the card calls yesterday",
        f.lastDayRain() === d[d.length - 1], String(f.lastDayRain()));
}

// -------------------------------------------------------------- the bars --
{
  const f = at("2026-09-12");
  const html = f.rainStrip();
  const bars = html.match(/<span class="[^"]*" style="height/g) || [];
  check("one bar per day", bars.length === COND.daily_rain_mm.length,
        bars.length + " bars");
  const heights = [...html.matchAll(/height:([\d.]+)px/g)].map(m => +m[1]);
  const wettest = Math.max(...COND.daily_rain_mm.map(v => v || 0));
  check("the wettest day is the tallest bar",
        heights.indexOf(Math.max(...heights)) ===
          COND.daily_rain_mm.findIndex(v => v === wettest),
        wettest + " mm");
  check("no bar escapes the 46px box", Math.max(...heights) <= 46.01,
        Math.max(...heights) + "px");
  check("a dry day still draws something", Math.min(...heights) >= 2,
        Math.min(...heights) + "px");

  // A flat, rainless window must not be magnified into a dramatic chart.
  const dry = JSON.parse(JSON.stringify(COND));
  dry.daily_rain_mm = dry.daily_rain_mm.map(() => 0.2);
  const flat = [...at("2026-09-12", dry).rainStrip().matchAll(/height:([\d.]+)px/g)]
    .map(m => +m[1]);
  check("a 0.2 mm drizzle does not fill the chart", Math.max(...flat) < 3,
        Math.max(...flat) + "px of 46");

  // No data at all is a blank, not a broken chart.
  const none = JSON.parse(JSON.stringify(COND));
  delete none.daily_rain_mm;
  check("an older conditions file with no series draws nothing",
        at("2026-09-12", none).rainStrip() === "");
}

console.log(fails ? `\n${fails} FAILED` : "\nall conditions checks passed");
process.exit(fails ? 1 : 0);

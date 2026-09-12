/* Spots are held in UTM33 on the device and stored as lon/lat on the server,
   so every sync crosses the projection twice. A sign error or a dropped term
   in the inverse would move spots by hundreds of metres and look plausible on
   the map, which is the kind of bug you only notice in a wood. */
const fs = require("fs");

const shell = fs.readFileSync(__dirname + "/../web/shell.html", "utf8")
  .split("\r\n").join("\n");

function grab(name) {
  const start = shell.indexOf("function " + name + "(");
  if (start < 0) throw new Error("missing " + name);
  // walk braces to the end of the function
  let i = shell.indexOf("{", start), depth = 0;
  for (; i < shell.length; i++) {
    if (shell[i] === "{") depth++;
    else if (shell[i] === "}") { depth--; if (!depth) break; }
  }
  return shell.slice(start, i + 1);
}

const ctx = new Function(
  grab("toUTM33") + grab("fromUTM33") + "return {toUTM33, fromUTM33};")();

let failures = 0;
function check(name, ok, detail) {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "   " + detail : ""));
  if (!ok) failures++;
}

// 1. Known point: Kristiansand city centre, 58.1467 N 7.9956 E.
//    UTM33 easting is far west of the 15E central meridian, so the value is
//    small - a sign or zone mistake shows up immediately here.
{
  const [e, n] = ctx.toUTM33(58.1467, 7.9956);
  check("Kristiansand forward-projects into zone 33 range",
    e > 40000 && e < 130000 && n > 6400000 && n < 6500000,
    `${e.toFixed(0)} E ${n.toFixed(0)} N`);
}

// 2. Round-trip across the whole modelled extent, corners and interior.
//
//    The bar is half a metre, not a millimetre. Both directions are truncated
//    Snyder series, and Norway uses zone 33 for the whole country, so
//    Kristiansand sits about 8 degrees west of the 15E central meridian -
//    far outside the +/-3 degrees the series is nominally good for, and
//    truncation error grows quickly with that distance.
//
//    Checked independently against pyproj (a rigorous PROJ transform) over a
//    135-point grid covering the extent: worst disagreement 22.4 cm, at the
//    north-west corner. That is the real accuracy figure. Against GPS at +/-5 m
//    and a 128 m display cell it is irrelevant, so half a metre is a bar that
//    catches a broken term while not failing on arithmetic that is fine.
{
  let worst = 0, worstAt = null;
  for (let e = 60000; e <= 110000; e += 2500) {
    for (let n = 6425000; n <= 6500000; n += 2500) {
      const [lat, lon] = ctx.fromUTM33(e, n);
      const [e2, n2] = ctx.toUTM33(lat, lon);
      const d = Math.hypot(e2 - e, n2 - n);
      if (d > worst) { worst = d; worstAt = [e, n, lat, lon]; }
    }
  }
  check("round-trip stays well inside GPS noise across the extent", worst < 0.5,
    `worst ${(worst * 100).toFixed(1)} cm at ${worstAt[0]} ${worstAt[1]}`);
}

// 3. Latitude and longitude land where they should for a known cell.
{
  const [lat, lon] = ctx.fromUTM33(86800, 6464300);
  check("inverse gives plausible Kristiansand lat/lon",
    lat > 58.0 && lat < 58.3 && lon > 7.8 && lon < 8.2,
    `${lat.toFixed(5)} N ${lon.toFixed(5)} E`);
}

// 4. A degree of latitude is about 111 km; check the inverse scales sanely.
{
  const [lat1] = ctx.fromUTM33(86800, 6464300);
  const [lat2] = ctx.fromUTM33(86800, 6464300 + 111320);
  check("111.32 km north is about one degree of latitude",
    Math.abs((lat2 - lat1) - 1.0) < 0.01,
    `${(lat2 - lat1).toFixed(5)} degrees`);
}

console.log(failures ? `\n${failures} FAILED` : "\nall projection tests passed");
process.exit(failures ? 1 : 0);

"""Client for the Geonorge Nedlasting API v2.

Geonorge publishes a common download-order standard that individual data
custodians host themselves. The catalogue record for a dataset names the host
in its `DistributionUrl`, and the same three calls then work everywhere:

    /api/v2/codelists/area/{uuid}   what can be ordered, and in which projections
    /api/v2/order                   POST an order, get download URLs back
    (the returned downloadUrl)      fetch the file

Verified 2026-09-09 against two hosts:

* NIBIO   `kart8.nibio.no`     SR16 raster, uuid 5de45872-...
* NGU     `nedlasting.ngu.no`  Losmasser,   uuid 3de4ddf6-...

TRAPS FOUND HERE:

* The paths advertised in the catalogue are `/api/capabilities/`, but the
  codelist and order endpoints live under **`/api/v2/`**. The unversioned
  paths return 404, and `capabilities` itself answers on both. Read the
  `_links` in the capabilities response rather than guessing.
* `codelists/projection` and `codelists/format` return an empty list. The real
  per-area projections and formats are nested inside each entry of
  `codelists/area`, which is the only one worth calling.
* An order can come back `ReadyForDownload` immediately or be queued. Both are
  normal; poll rather than assuming the first answer is final.

NO EMAIL IS SENT. The order schema accepts one for delivery notification, and
these datasets are all `deliveryNotificationByEmail: false`, so it is not
needed. A person's address is not ours to hand to a third party as a side
effect of a download.
"""
from __future__ import annotations

import json
import pathlib
import ssl
import time
import urllib.error
import urllib.request

TIMEOUT = 300
UA = {"User-Agent": "soppspor/0.1 (habitat suitability research; support@soppspor.no)"}
CTX = ssl.create_default_context()


class DownloadError(RuntimeError):
    """The service did not answer as verified. Never swallowed into a default."""


def _json(url: str, body: dict | None = None, retries: int = 2):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = dict(UA, Accept="application/json")
    if data is not None:
        headers["Content-Type"] = "application/json"
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers,
                                         method="POST" if data else "GET")
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise DownloadError(f"HTTP {exc.code} from {url}\n{detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise DownloadError(f"no answer from {url} after {retries + 1} tries: {last}")


def areas(base: str, uuid: str) -> list[dict]:
    """Orderable areas, each carrying its own projections and formats."""
    got = _json(f"{base.rstrip('/')}/api/v2/codelists/area/{uuid}")
    if not got:
        raise DownloadError(f"no orderable areas listed for {uuid} at {base}")
    return got


def find_area(base: str, uuid: str, code: str, kind: str = "fylke") -> dict:
    """One area by code, checked rather than assumed present.

    The catalogue lists areas per dataset, and coverage is not uniform: a
    dataset may be published per kommune, per fylke, or nationally only.
    Ordering a code the host does not list returns an order with no files
    rather than an error, so the check happens here.
    """
    for a in areas(base, uuid):
        if str(a.get("code")) == str(code) and a.get("type") == kind:
            return a
    listed = sorted({f"{a.get('type')}:{a.get('code')}" for a in areas(base, uuid)})
    raise DownloadError(
        f"{kind} {code} is not orderable for {uuid}. Listed: {', '.join(listed[:20])}")


def order(base: str, uuid: str, area: dict, projection: str = "25833",
          fmt: str | None = None) -> list[dict]:
    """Place an order and return its file entries.

    `projection` defaults to EPSG:25833 because CLAUDE.md fixes that CRS for
    the whole project and reprojecting on ingest is cheaper and safer than
    reprojecting a multi-gigabyte raster afterwards.
    """
    proj = next((p for p in area.get("projections", [])
                 if str(p.get("code")) == str(projection)), None)
    if proj is None:
        have = ", ".join(str(p.get("code")) for p in area.get("projections", []))
        raise DownloadError(
            f"{area.get('name')} is not published in EPSG:{projection}. Available: {have}. "
            f"Reprojecting on ingest is the project rule, so pick one of those "
            f"and reproject rather than changing the project CRS.")

    formats = area.get("formats") or []
    chosen = ([f for f in formats if f.get("name") == fmt] if fmt else formats[:1])
    if not chosen:
        have = ", ".join(str(f.get("name")) for f in formats)
        raise DownloadError(f"format {fmt!r} not offered for {area.get('name')}. Have: {have}")

    body = {"orderLines": [{
        "metadataUuid": uuid,
        "areas": [{"code": area["code"], "type": area["type"], "name": area["name"]}],
        "projections": [proj],
        "formats": chosen,
    }]}
    got = _json(f"{base.rstrip('/')}/api/v2/order", body=body)
    files = got.get("files") or []
    if not files:
        raise DownloadError(
            f"the order was accepted but returned no files: {json.dumps(got)[:400]}")
    return files


def wait_ready(base: str, reference: str, files: list[dict], poll_s: float = 20.0,
               timeout_s: float = 1800.0, progress=None) -> list[dict]:
    """Poll an order until every file is ready, or give up loudly."""
    if all(f.get("status") == "ReadyForDownload" for f in files):
        return files
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        got = _json(f"{base.rstrip('/')}/api/v2/order/{reference}")
        files = got.get("files") or files
        if progress:
            progress([f.get("status") for f in files])
        if all(f.get("status") == "ReadyForDownload" for f in files):
            return files
        time.sleep(poll_s)
    raise DownloadError(
        f"order {reference} was not ready within {timeout_s:.0f}s. "
        f"Statuses: {[f.get('status') for f in files]}")


def download(url: str, dest: pathlib.Path, progress=None, chunk: int = 1 << 20) -> dict:
    """Stream a file to disk, refusing to leave a truncated one behind.

    A partial download that stays on disk under the real filename is exactly
    the failure CLAUDE.md forbids: it would be read later as though it were
    complete. The file is written beside its destination and moved into place
    only after the byte count matches what the server promised.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    req = urllib.request.Request(url, headers=UA)
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            total = int(r.headers.get("Content-Length") or 0)
            with open(tmp, "wb") as fh:
                while True:
                    buf = r.read(chunk)
                    if not buf:
                        break
                    fh.write(buf)
                    got += len(buf)
                    if progress:
                        progress(got, total)
        if total and got != total:
            raise DownloadError(
                f"short read: {got:,} of {total:,} bytes from {url}. "
                f"A truncated archive must not be left where it could be used.")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return {"url": url, "path": str(dest), "bytes": got,
            "downloaded": time.strftime("%Y-%m-%dT%H:%M:%S")}

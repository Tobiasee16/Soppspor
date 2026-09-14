"""Serve the built interface locally, so the browser will hand it a position.

    python scripts/serve_interface.py            # http://localhost:8765
    python scripts/serve_interface.py --https    # also https://<lan-ip>:8765

**Why this exists.** Geolocation is gated on a *secure context*. A published
artifact is a secure context, but it renders inside a sandboxed iframe that was
never granted the geolocation permission, so the browser refuses immediately -
and it refuses with `PERMISSION_DENIED`, the same code it uses when a person
declines, which is why the app could not tell the difference until it started
checking whether it was framed. Nothing inside the page can lift that.

Two ways out, and which one you need depends on the device:

* **Desktop, same machine.** Plain HTTP is fine: `http://localhost` counts as a
  secure context by definition, no certificate needed. The position comes from
  wifi triangulation, so it is accurate to a few hundred metres at best - good
  enough to confirm the dot lands in the right part of town, useless for
  standing under a spruce.
* **Phone, over your wifi.** `http://192.168.x.x` is *not* a secure context, so
  geolocation stays blocked no matter what. It needs TLS, which is what
  `--https` is for: a self-signed certificate is generated on first use and the
  server binds every interface. Your phone will show a certificate warning -
  that is expected, it is a certificate signed by nobody. Tap through it. Once
  the page loads over `https://` it is a secure context and real GPS works.

Nothing here is a deployment. It binds a local port, serves one file, and is
meant to be stopped with Ctrl-C.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import mimetypes
import pathlib
import socket
import ssl
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PAGE = ROOT / "data" / "processed" / "web_preview" / "soppspor.html"
CERT_DIR = ROOT / "data" / "interim" / "devcert"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def lan_ip() -> str:
    """The address this machine has on the local network.

    Opening a UDP socket to an outside address picks the interface the routing
    table would actually use, without sending anything. `gethostbyname` on a
    machine with several adapters tends to return the wrong one.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_cert(host: str) -> tuple[pathlib.Path, pathlib.Path]:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert, key = CERT_DIR / "dev.crt", CERT_DIR / "dev.key"
    if cert.exists() and key.exists():
        return cert, key

    san = f"DNS:localhost,IP:127.0.0.1,IP:{host}"
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key), "-out", str(cert), "-days", "365",
             "-subj", "/CN=soppspor-dev", "-addext", f"subjectAltName={san}"],
            check=True, capture_output=True)
        print(f"generated a self-signed certificate in {CERT_DIR.relative_to(ROOT)}")
        return cert, key
    except (OSError, subprocess.CalledProcessError):
        pass

    # openssl is not on PATH. `cryptography` is already in the environment as a
    # transitive dependency, so this is not a new package.
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "soppspor-dev")])
    alts = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    try:
        alts.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        pass
    now = datetime.datetime.now(datetime.timezone.utc)
    crt = (x509.CertificateBuilder()
           .subject_name(name).issuer_name(name).public_key(k.public_key())
           .serial_number(x509.random_serial_number())
           .not_valid_before(now - datetime.timedelta(days=1))
           .not_valid_after(now + datetime.timedelta(days=365))
           .add_extension(x509.SubjectAlternativeName(alts), critical=False)
           .sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    cert.write_bytes(crt.public_bytes(serialization.Encoding.PEM))
    print(f"generated a self-signed certificate in {CERT_DIR.relative_to(ROOT)}")
    return cert, key


# `build_interface.py` emits a *fragment*: no doctype, no <head>. That is what
# the artifact host wants, because it wraps the file in its own skeleton at
# publish time. Served raw, a phone browser has no viewport meta to read, falls
# back to a 980 px desktop viewport, and renders the whole app as a small box
# in the middle of the screen. This is the same skeleton the host applies, so
# what you see locally is what you see published.
HEAD = """<!doctype html>
<html lang="no"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#f7f7fa">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Soppspor">
<link rel="apple-touch-icon" href="/icons/icon-180.png">
<link rel="icon" href="/icons/icon-192.png">
<style>
:root{color-scheme:light dark}
body{margin:0;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:#fafafa}
img{max-width:100%}
[hidden]{display:none!important}
</style>
</head><body>
"""
TAIL = """
<script>
/* Registered from the served page rather than baked into the shell, because a
   published artifact has no origin of its own to register a worker against.
   Failure here is not an error: it means the page is running somewhere that
   cannot install one, and everything still works online. */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  });
}
</script>
</body></html>
"""

# Served alongside the page so the browser will treat it as an installable app.
WEB = ROOT / "web"
STATIC = {
    "/manifest.webmanifest": ("application/manifest+json", WEB / "manifest.webmanifest"),
    "/sw.js": ("text/javascript", WEB / "sw.js"),
    "/icons/icon-180.png": ("image/png", WEB / "icons" / "icon-180.png"),
    "/icons/icon-192.png": ("image/png", WEB / "icons" / "icon-192.png"),
    "/icons/icon-512.png": ("image/png", WEB / "icons" / "icon-512.png"),
    "/icons/icon-maskable-512.png": ("image/png", WEB / "icons" / "icon-maskable-512.png"),
    # Served separately from the page so the weather can move without a 14 MB
    # rebuild. The page still carries a copy for offline and for the published
    # artifact, and prefers this one when it is newer.
    "/conditions.json": ("application/json",
                         ROOT / "data" / "processed" / "conditions.json"),
}


class Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the one page at every path, so any URL you type lands on it.

    With `--dist` it serves a built directory instead. The hosted build is
    already a complete static site, and serving it as files rather than as one
    page is the only way to exercise what that build is *for*: the service
    worker, the manifest, and a `conditions.json` that is fetched rather than
    inlined. Testing it through the single-page path would test a configuration
    nobody will ever deploy.
    """

    page: pathlib.Path
    dist = None

    def _serve_from_dist(self, path: str) -> bool:
        if self.dist is None:
            return False
        rel = path.lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        root = self.dist.resolve()
        target = (self.dist / rel).resolve()
        # Never serve outside the built directory, however the path is spelled.
        if target != root and root not in target.parents:
            self.send_error(403)
            return True
        if not target.is_file():
            # The single-page fallback is for paths a person could arrive at -
            # a bookmark, a shared link, a magic-link hash. It is emphatically
            # not for a missing asset: a zone tile that failed to upload would
            # come back as the page itself, with a 200, and the map would show
            # no zones with nothing anywhere reporting a problem. Anything with
            # a file extension 404s, which is also what the deployed
            # `_redirects` does.
            if pathlib.PurePosixPath(rel).suffix and rel != "index.html":
                self.send_error(404)
                return True
            target = self.dist / "index.html"
            if not target.is_file():
                return False
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        self.send_response(200)
        self.send_header("Content-Type", ctype
                         + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        # The page, the worker and the weather must never come back stale, or a
        # fix to any of them cannot reach a device that already has the old one.
        fresh = target.name in ("index.html", "sw.js", "conditions.json")
        self.send_header("Cache-Control", "no-cache" if fresh
                         else "public, max-age=3600")
        if target.name == "sw.js":
            self.send_header("Service-Worker-Allowed", "/")
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_GET(self):  # noqa: N802 - stdlib naming
        path = self.path.split("?", 1)[0]
        if self._serve_from_dist(path):
            return
        if path in STATIC:
            ctype, src = STATIC[path]
            if not src.exists():
                self.send_error(404, f"{src.name} missing - run scripts/make_icons.py")
                return
            body = src.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # The worker must never be served stale, or a fix to it can never
            # reach a device that already has the old one.
            self.send_header("Cache-Control", "no-cache")
            if path == "/sw.js":
                self.send_header("Service-Worker-Allowed", "/")
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/favicon.ico",):
            self.send_error(404)
            return
        raw = self.page.read_text(encoding="utf-8")
        head = raw.lstrip()[:200].lower()
        if head.startswith("<!doctype") or head.startswith("<html"):
            body = raw.encode("utf-8")
        else:
            body = (HEAD + raw + TAIL).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("page", nargs="?", type=pathlib.Path, default=DEFAULT_PAGE)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--https", action="store_true",
                    help="serve over TLS on all interfaces, for testing GPS on a phone")
    ap.add_argument("--dist", nargs="?", const=str(ROOT / "dist"), default=None,
                    help="serve a built static directory (default: dist/) "
                         "instead of a single page")
    args = ap.parse_args()

    if args.dist:
        dist = pathlib.Path(args.dist)
        if not (dist / "index.html").is_file():
            raise SystemExit(
                str(dist) + " has no index.html." + chr(10)
                + "Build it first:" + chr(10)
                + "  python scripts/build_hosted.py")
        Handler.dist = dist
        print("serving the hosted build from " + str(dist))

    if not args.dist and not args.page.exists():
        raise SystemExit(
            f"{args.page} does not exist.\n"
            "Build it first:\n"
            "  python scripts/build_interface.py <shell.html> "
            f"{DEFAULT_PAGE.relative_to(ROOT)}")

    ip = lan_ip()
    bind = "0.0.0.0" if args.https else "127.0.0.1"
    handler = functools.partial(Handler)
    Handler.page = args.page

    httpd = http.server.ThreadingHTTPServer((bind, args.port), handler)
    scheme = "http"
    if args.https:
        cert, key = ensure_cert(ip)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"

    size = args.page.stat().st_size / 1e6
    print(f"serving {args.page.name} ({size:.2f} MB)\n")
    print(f"  {scheme}://localhost:{args.port}/")
    if args.https:
        print(f"  {scheme}://{ip}:{args.port}/        <- open this on your phone")
        print("\nYour phone will warn that the certificate is not trusted. It is "
              "signed by\nnobody, which is expected; tap through it. The page is "
              "then a secure\ncontext and GPS will work.")
    else:
        print("\nlocalhost is a secure context, so geolocation works here without a\n"
              "certificate - but desktop position comes from wifi and is coarse.\n"
              "For real GPS on a phone, stop this and rerun with --https.")
    print("\nCtrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

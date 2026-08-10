"""Minimal static file server for Next.js static export (output: 'export').

Handles clean URLs: /strava → strava.html, /map → map.html, etc.
Falls back to index.html for unknown paths (SPA catch-all).

Features:
- HTTP Range requests (required by PMTiles byte-range slices)
- Gzip compression for text assets (JS/CSS/HTML/JSON/SVG — 3-4x smaller)
- Threaded request handling (HTTP/1.1)
"""

import gzip as _gzip
import http.server
import io
import os
import re
import socketserver
import sys
from email.utils import formatdate

ROOT = os.path.join(os.path.dirname(__file__), "out") if len(sys.argv) < 2 else sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) >= 3 else 3787

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

# Content types eligible for gzip (text-based assets that compress well)
_GZIP_TYPES = {
    "text/html", "text/css", "text/javascript", "text/plain", "text/xml",
    "application/javascript", "application/json", "application/xml",
    "application/manifest+json", "image/svg+xml",
}
# Minimum size to bother compressing (below this, overhead > savings)
_GZIP_MIN_SIZE = 256


class SPAHandler(http.server.SimpleHTTPRequestHandler):
    # Use HTTP/1.1 so Range/206 responses are well-formed and connections can be reused
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        path = self.path.split("?")[0]
        if "/_next/static/" in path:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        elif path.endswith(".html") or path == "/" or "." not in path.split("/")[-1]:
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        elif path.endswith((".pmtiles", ".fgraph", ".wasm", ".pb")):
            # Heavy static assets that only change between rebuilds.
            #
            # **This file (serve.py) only runs in DEV** — prod serves
            # PMTiles/.fgraph from GCS + Cloud CDN with their own cache
            # headers (24h+). So lowering the cache here is dev-only.
            #
            # Dev rebuild loop is event-driven (auto-PMTiles after each
            # ingest, debounced 5 min — see ingest._schedule_pmtiles_
            # rebuild). With max-age=60 the browser revalidates fast
            # enough to show new bytes without a hard reload, while
            # ``stale-while-revalidate=300`` lets it serve the cached
            # copy during a pan and refresh in the background. The
            # extra revalidation traffic (one If-Modified-Since per
            # tile-burst per minute) is fine on a single laptop —
            # 200 KB/min, negligible.
            #
            # If you ever vendor serve.py for staging/prod (don't),
            # bump these back to ``max-age=86400, stale-while-
            # revalidate=86400`` to avoid hammering the origin under
            # real user load.
            self.send_header(
                "Cache-Control",
                "public, max-age=60, stale-while-revalidate=300",
            )
        else:
            self.send_header("Cache-Control", "public, max-age=60")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?")[0].split("#")[0]
        full = os.path.join(ROOT, path.lstrip("/"))

        if os.path.isfile(full):
            return self._serve_file(full)

        html_path = full.rstrip("/") + ".html"
        if os.path.isfile(html_path):
            return self._serve_file(html_path)

        index_path = os.path.join(full, "index.html")
        if os.path.isdir(full) and os.path.isfile(index_path):
            return self._serve_file(index_path)

        # SPA fallback → index.html
        return self._serve_file(os.path.join(ROOT, "index.html"))

    def do_HEAD(self):
        # Same resolution as GET but write no body
        return self.do_GET()

    def _serve_file(self, full_path):
        """Serve a file with Range support and proper content-type."""
        try:
            st = os.stat(full_path)
        except OSError:
            self.send_error(404, "File not found")
            return

        ctype = self.guess_type(full_path)
        size = st.st_size

        try:
            f = open(full_path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return

        try:
            range_header = self.headers.get("Range")
            start, end = 0, size - 1
            status = 200

            if range_header:
                m = _RANGE_RE.match(range_header.strip())
                if m:
                    s, e = m.group(1), m.group(2)
                    if s == "" and e == "":
                        m = None  # malformed
                    elif s == "":
                        # suffix: last N bytes
                        n = int(e)
                        if n == 0:
                            m = None
                        else:
                            start = max(0, size - n)
                            end = size - 1
                            status = 206
                    else:
                        start = int(s)
                        end = int(e) if e != "" else size - 1
                        if start >= size:
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{size}")
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
                        end = min(end, size - 1)
                        status = 206

            length = end - start + 1

            # Gzip: only for full (non-Range) responses of compressible types
            # when the client accepts gzip and the file is large enough.
            accept_enc = self.headers.get("Accept-Encoding", "")
            use_gzip = (
                status == 200
                and "gzip" in accept_enc
                and length >= _GZIP_MIN_SIZE
                and ctype.split(";")[0].strip() in _GZIP_TYPES
            )

            if use_gzip:
                # Read full file content and compress
                f.seek(start)
                raw = f.read(length)
                buf = io.BytesIO()
                with _gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
                    gz.write(raw)
                compressed = buf.getvalue()

                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(compressed)))
                self.send_header("Last-Modified", formatdate(st.st_mtime, usegmt=True))
                self.send_header("Vary", "Accept-Encoding")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(compressed)
            else:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Last-Modified", formatdate(st.st_mtime, usegmt=True))
                self.end_headers()

                if self.command == "HEAD":
                    return

                f.seek(start)
                remaining = length
                chunk = 64 * 1024
                while remaining > 0:
                    buf = f.read(min(chunk, remaining))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    remaining -= len(buf)
        finally:
            f.close()


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    with ThreadingHTTPServer(("", PORT), SPAHandler) as httpd:
        print(f"Serving {ROOT} on port {PORT} (HTTP/1.1, Range supported)")
        httpd.serve_forever()

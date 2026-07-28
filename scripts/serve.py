"""Static server for web/.

Adds the two things the stock handler lacks and PMTiles needs: the .pmtiles
mime type and HTTP range requests (the archive is read by byte range, never
downloaded whole). GitHub Pages supports ranges natively, so this only matters
for local development.
"""

import functools
import http.server
import os
import re
import socketserver
from pathlib import Path

# 8787 by default; $PORT lets a harness that assigns its own port drive this.
PORT = int(os.environ.get("PORT") or 8787)
ROOT = Path(__file__).resolve().parents[1] / "web"
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".pmtiles": "application/octet-stream",
        ".mjs": "text/javascript",
        ".geojson": "application/geo+json",
    }

    def send_head(self):
        rng = self.headers.get("Range")
        if not rng:
            return super().send_head()

        path = self.translate_path(self.path)
        if os.path.isdir(path) or not os.path.exists(path):
            return super().send_head()

        size = os.path.getsize(path)
        m = RANGE_RE.fullmatch(rng.strip())
        if not m:
            self.send_error(400, "Malformed Range")
            return None
        start, end = m.group(1), m.group(2)
        if start == "":  # suffix range: last N bytes
            start, end = max(0, size - int(end)), size - 1
        else:
            start = int(start)
            end = int(end) if end else size - 1
        if start >= size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        end = min(end, size - 1)

        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return _Slice(f, end - start + 1)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # keep the console readable
        if "404" in (args[1] if len(args) > 1 else ""):
            super().log_message(fmt, *args)


class _Slice:
    """File wrapper that stops after n bytes, for copyfile()."""

    def __init__(self, f, n):
        self.f, self.left = f, n

    def read(self, size=-1):
        if self.left <= 0:
            return b""
        if size is None or size < 0:
            size = self.left
        chunk = self.f.read(min(size, self.left))
        self.left -= len(chunk)
        return chunk

    def close(self):
        self.f.close()


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(
        ("127.0.0.1", PORT), functools.partial(Handler, directory=str(ROOT))
    ) as httpd:
        print(f"http://127.0.0.1:{PORT}/")
        httpd.serve_forever()

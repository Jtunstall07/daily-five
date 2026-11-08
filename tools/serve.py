#!/usr/bin/env python3
"""Local test server that mimics GitHub Pages: serves the repo at /daily-five/ and supports Range requests.
Usage: python3 tools/serve.py [port]   then open http://127.0.0.1:<port>/daily-five/
"""
import http.server, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIX = "/daily-five/"
TYPES = {".webmanifest": "application/manifest+json", ".mp4": "audio/mp4", ".json": "application/json",
         ".svg": "image/svg+xml", ".js": "text/javascript", ".html": "text/html; charset=utf-8", ".png": "image/png"}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.handle_get(head=True)

    def do_GET(self):
        self.handle_get()

    def handle_get(self, head=False):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/daily-five":
            self.send_response(301); self.send_header("Location", PREFIX); self.end_headers(); return
        if not path.startswith(PREFIX):
            self.send_error(404); return
        rel = path[len(PREFIX):] or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        fp = os.path.normpath(os.path.join(ROOT, rel))
        if not fp.startswith(ROOT) or "/.git" in fp or not os.path.isfile(fp):
            self.send_error(404); return
        size = os.path.getsize(fp)
        ctype = TYPES.get(os.path.splitext(fp)[1], "application/octet-stream")
        rng = self.headers.get("Range")
        start, end, status = 0, size - 1, 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
                status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "max-age=600")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        with open(fp, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    s = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"http://127.0.0.1:{port}{PREFIX}")
    s.serve_forever()

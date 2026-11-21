"""Tiny local receiver used for the first import, before the backend existed.

Accepts capture envelopes (docs/CAPTURE_CONTRACT.md) by POST and writes one file
per envelope under data/captures/<source>/. Standard library only.
Run: uv run python scripts/capture_receiver.py
"""
import json
import pathlib
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8765
ROOT = pathlib.Path(__file__).resolve().parent.parent / "data" / "captures"
ALLOWED_SOURCES = {"x", "linkedin"}


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        # Chrome asks this before a public page may call a local address.
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # Relay page: x.com's content policy blocks direct calls to localhost, so the
        # capture script opens this page and hands envelopes over with postMessage.
        page = b"""<!doctype html><title>Kosha capture relay</title>
<body style="font:14px system-ui;padding:2em">Kosha capture relay. Keep this tab open.
<div id=n>0</div><script>
let n=0;
addEventListener('message', async (e) => {
  if (e.origin !== 'https://x.com' && e.origin !== 'https://www.linkedin.com') return;
  let ok=false;
  try { const r = await fetch('/', {method:'POST', headers:{'content-type':'application/json'}, body: e.data.body}); ok = r.ok; } catch (err) {}
  if (ok) document.getElementById('n').textContent = ++n;
  e.source.postMessage({koshaAck: e.data.id, ok}, e.origin);
});
</script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            env = json.loads(body)
            source = env["source"]
            if source not in ALLOWED_SOURCES:
                raise ValueError("unknown source")
            kind = "".join(c for c in str(env["kind"]) if c.isalnum() or c == "_")
            folder = ROOT / source
            folder.mkdir(parents=True, exist_ok=True)
            n = len(list(folder.glob("*.json")))
            (folder / f"{n:06d}-{kind}.json").write_bytes(body)
            code, out = 200, {"ok": True, "n": n}
        except Exception as exc:  # bad envelope: report, keep serving
            code, out = 400, {"ok": False, "error": str(exc)}
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())

    def log_message(self, *args):  # quiet
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

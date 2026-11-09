"""Local fake of the Confluence Cloud REST API (content by id and CQL search with pagination)."""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HOME = {"id": "1", "title": "GR8 Docs"}


def make_pages(base: str) -> dict[str, dict]:
    def page(pid, title, ancestors, html="<p>text</p>"):
        return {"id": pid, "type": "page", "title": title,
                "ancestors": [{"id": a["id"], "title": a["title"]} for a in ancestors],
                "version": {"number": 3, "when": "2026-01-02T03:04:05.000Z"},
                "body": {"export_view": {"value": html}},
                "_links": {"webui": f"/spaces/GD/pages/{pid}/x"}}
    root = {"id": "100", "title": "User Guides"}
    casino = {"id": "101", "title": "Casino Guide"}
    other = {"id": "104", "title": "Other Guide"}
    pages = [
        page("100", "User Guides", [HOME], "<p>Root page</p>"),
        page("101", "Casino Guide", [HOME, root],
             f"<h2>Intro</h2><p>Hello <a href=\"{base}/wiki/spaces/GD/pages/102/Setup\">setup</a> and "
             "<a href=\"https://example.org/x\">outside</a></p><ul><li>a</li><li>b</li></ul>"
             "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"),
        page("102", "Setup", [HOME, root, casino], "<p>Setup steps</p>"),
        page("104", "Other Guide", [HOME, root], "<p>Other</p>"),
        page("103", "Setup", [HOME, root, other], "<p>Other setup</p>"),
        page("105", "Орієнтація", [HOME, root], "<p>Привіт</p>"),
        page("106", "Dup", [HOME, root], "<p>first</p>"),
        page("107", "Dup", [HOME, root], "<p>second</p>"),
        page("200", "Outside", [HOME], "<p>not in branch</p>"),
    ]
    return {p["id"]: p for p in pages}


class FakeConfluence(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, email="o@example.com", token="TOKEN-123", page_size_cap=None):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.email, self.token = email, token
        self.pages = make_pages(self.url)
        self.requests: list[str] = []
        self.fail_429_once = False
        self.foreign_next = False
        self._429_done = False
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload, status=200, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        srv: FakeConfluence = self.server  # type: ignore[assignment]
        srv.requests.append(self.path)
        expected = "Basic " + base64.b64encode(f"{srv.email}:{srv.token}".encode()).decode()
        if self.headers.get("Authorization") != expected:
            return self._json({"message": "Unauthorized"}, 401)
        if srv.fail_429_once and not srv._429_done:
            srv._429_done = True
            return self._json({"message": "slow down"}, 429, {"Retry-After": "0"})
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        if parts.path == "/wiki/rest/api/content/search":
            cql = query.get("cql", [""])[0]
            ancestor = cql.split("ancestor")[1].split("=")[1].split()[0].strip()
            matches = sorted((p for p in srv.pages.values() if any(a["id"] == ancestor for a in p["ancestors"])),
                             key=lambda p: int(p["id"]))
            limit = int(query.get("limit", ["25"])[0])
            start = int(query.get("cursor", ["0"])[0])
            chunk = matches[start:start + limit]
            links = {"base": f"{srv.url}/wiki"}
            if start + limit < len(matches):
                host = "http://evil.example.com" if srv.foreign_next else ""
                links["next"] = f"{host}/rest/api/content/search?cql={query['cql'][0]}&limit={limit}&cursor={start + limit}"
            return self._json({"results": chunk, "start": start, "limit": limit, "size": len(chunk), "_links": links})
        if parts.path.startswith("/wiki/rest/api/content/"):
            page = srv.pages.get(parts.path.rsplit("/", 1)[1])
            return self._json(page, 200) if page else self._json({"message": "not found"}, 404)
        self._json({"message": "not found"}, 404)

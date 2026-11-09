"""Export one branch of a Confluence Cloud space to Markdown files (REST API, read-only).

Only GET requests are made. Credentials are used for the Authorization header only and are never
printed or written to disk. See docs/confluence-export-spec.md.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from markdownify import markdownify

EXPAND = "body.export_view,ancestors,version"
_RETRY_STATUSES = (429, 500, 502, 503, 504)
_PAGE_LINK_RE = re.compile(r"\]\((?:https?://[^/)\s]+)?/wiki/spaces/[^/)\s]+/pages/(\d+)[^)]*\)")


class ExportError(Exception):
    """The export stopped. The text never contains credentials."""


def slugify(title: str) -> str:
    """Lowercase file-system-safe name that keeps Unicode letters and digits."""
    slug = re.sub(r"[^\w]+", "-", title.lower(), flags=re.UNICODE).replace("_", "-").strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:80].strip("-")
    return slug or "page"


@dataclass
class Page:
    id: str
    title: str
    parent_id: str | None
    html: str
    url: str
    modified: str


class ConfluenceClient:
    """Small GET-only client with Basic auth, retries and a pause between requests."""

    def __init__(self, base_url: str, email: str, token: str, delay_s: float = 0.3,
                 sleep: Callable[[float], None] = time.sleep, max_retries: int = 5) -> None:
        self.base = base_url.rstrip("/")
        self.host = (urlsplit(self.base).hostname or "").lower()
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
        self._delay, self._sleep, self._retries = delay_s, sleep, max_retries
        local = self.host in ("127.0.0.1", "localhost")
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local \
            else urllib.request.build_opener()

    def get_json(self, path_or_url: str) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else self.base + path_or_url
        if (urlsplit(url).hostname or "").lower() != self.host:
            raise ExportError("refusing to follow a link to another host")
        url = quote(url, safe=":/?&=%,+@")
        for attempt in range(self._retries + 1):
            request = urllib.request.Request(url, headers=self._headers, method="GET")
            try:
                with self._opener.open(request, timeout=60) as response:
                    data = json.load(response)
                if self._delay:
                    self._sleep(self._delay)
                return data
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise ExportError(f"authorization failed (HTTP {exc.code}): check the account email, "
                                      "the API token and the access rights") from None
                if exc.code == 404:
                    raise ExportError("not found (HTTP 404): check the page id and the site") from None
                if exc.code in _RETRY_STATUSES and attempt < self._retries:
                    try:
                        wait = float(exc.headers.get("Retry-After", "") or 2 ** attempt)
                    except ValueError:
                        wait = float(2 ** attempt)
                    self._sleep(min(wait, 60))
                    continue
                raise ExportError(f"Confluence answered HTTP {exc.code}") from None
            except (urllib.error.URLError, OSError, ValueError) as exc:
                if attempt < self._retries:
                    self._sleep(min(2 ** attempt, 30))
                    continue
                raise ExportError(f"Confluence is not reachable ({type(exc).__name__})") from None
        raise ExportError("too many retries")  # pragma: no cover


def _to_page(item: dict[str, Any], base: str) -> Page:
    ancestors = item.get("ancestors") or []
    webui = (item.get("_links") or {}).get("webui", "")
    return Page(id=str(item["id"]), title=item.get("title") or "Untitled",
                parent_id=str(ancestors[-1]["id"]) if ancestors else None,
                html=(((item.get("body") or {}).get("export_view") or {}).get("value")) or "",
                url=f"{base}/wiki{webui}" if webui else base,
                modified=((item.get("version") or {}).get("when")) or "")


def fetch_branch(client: ConfluenceClient, root_id: str, page_size: int, max_pages: int,
                 log: Callable[[str], None]) -> dict[str, Page]:
    root = client.get_json(f"/wiki/rest/api/content/{root_id}?expand={EXPAND}")
    pages = {str(root["id"]): _to_page(root, client.base)}
    cql = quote(f"ancestor = {root_id} AND type = page", safe="")
    url: str | None = f"/wiki/rest/api/content/search?cql={cql}&expand={EXPAND}&limit={page_size}"
    while url:
        data = client.get_json(url)
        for item in data.get("results", []):
            pages[str(item["id"])] = _to_page(item, client.base)
            if len(pages) > max_pages:
                raise ExportError(f"the branch has more than {max_pages} pages; "
                                  "raise --max-pages if this is intended")
        if len(pages) % 100 < page_size and len(pages) >= 100:
            log(f"fetched {len(pages)} pages")
        links = data.get("_links") or {}
        nxt = links.get("next")
        if not nxt:
            break
        url = nxt if nxt.startswith("http") else (links.get("base") or client.base + "/wiki") + nxt
    return pages


def plan_paths(pages: dict[str, Page], root_id: str) -> tuple[dict[str, PurePosixPath], list[tuple[int, Page]], list[str]]:
    """Returns file path per page id, the pages in tree order with depth, and notes about oddities."""
    children: dict[str, list[Page]] = {}
    notes: list[str] = []
    for page in pages.values():
        if page.id == root_id:
            continue
        parent = page.parent_id if page.parent_id in pages else root_id
        if page.parent_id not in pages:
            notes.append(f"page {page.id} was attached to the root: its parent is not accessible")
        children.setdefault(parent, []).append(page)
    base = PurePosixPath(slugify(pages[root_id].title))
    paths: dict[str, PurePosixPath] = {root_id: base / "index.md"}
    order: list[tuple[int, Page]] = [(0, pages[root_id])]

    def place(parent_id: str, directory: PurePosixPath, depth: int) -> None:
        used: set[str] = set()
        for child in sorted(children.get(parent_id, []), key=lambda p: (p.title.lower(), int(p.id))):
            name = slugify(child.title)
            if name in used:
                name = f"{name}-{child.id}"
            used.add(name)
            if children.get(child.id):
                paths[child.id] = directory / name / "index.md"
                order.append((depth, child))
                place(child.id, directory / name, depth + 1)
            else:
                paths[child.id] = directory / f"{name}.md"
                order.append((depth, child))

    place(root_id, base, 1)
    return paths, order, notes


def _front_matter(page: Page, trail: str) -> str:
    return ("---\n"
            f"title: {json.dumps(page.title, ensure_ascii=False)}\n"
            f"confluence_id: {page.id}\n"
            f"url: {page.url}\n"
            f"last_modified: {page.modified}\n"
            f"path: {json.dumps(trail, ensure_ascii=False)}\n"
            "---\n\n")


def _convert(page: Page, paths: dict[str, PurePosixPath]) -> str:
    body = markdownify(page.html, heading_style="ATX", bullets="-", strip=["script", "style"])
    here = paths[page.id].parent

    def relink(match: re.Match[str]) -> str:
        target = paths.get(match.group(1))
        if target is None:
            return match.group(0)
        # shortest relative path between the two files
        common = 0
        for a, b in zip(here.parts, target.parts[:-1]):
            if a != b:
                break
            common += 1
        rel = PurePosixPath(*([".."] * (len(here.parts) - common)), *target.parts[common:])
        return f"]({str(rel).replace(' ', '%20')})"

    body = _PAGE_LINK_RE.sub(relink, body)
    return re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"


def export_branch(client: ConfluenceClient, root_id: str, out_dir: Path, page_size: int = 25,
                  max_pages: int = 3000, make_zip: bool = False,
                  log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    pages = fetch_branch(client, str(root_id), page_size, max_pages, log)
    root_id = str(root_id)
    paths, order, notes = plan_paths(pages, root_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = list(notes)
    titles = {pid: p.title for pid, p in pages.items()}
    for _depth, page in order:
        chain: list[str] = []
        cursor: str | None = page.id
        while cursor and cursor in pages and len(chain) < 50:
            chain.append(titles[cursor])
            cursor = pages[cursor].parent_id if cursor != root_id else None
        trail = " > ".join(reversed(chain))
        try:
            text = _front_matter(page, trail) + f"# {page.title}\n\n" + _convert(page, paths)
            target = out_dir / paths[page.id]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except Exception as exc:  # one bad page must not stop the export
            errors.append(f"page {page.id} was not converted ({type(exc).__name__})")
    index_lines = [f"# {pages[root_id].title}", ""]
    for depth, page in order:
        index_lines.append(f"{'  ' * depth}- [{page.title}]({paths[page.id]})")
    (out_dir / "INDEX.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    summary: dict[str, Any] = {"root_id": root_id, "root_title": pages[root_id].title, "pages": len(pages),
                               "errors": errors, "site": client.host,
                               "exported_at": datetime.now(timezone.utc).isoformat()}
    (out_dir / "_export.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if make_zip:
        archive_path = out_dir / "export.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(out_dir.rglob("*")):
                if file.is_file() and file != archive_path:
                    archive.write(file, file.relative_to(out_dir).as_posix())
        summary["zip"] = str(archive_path)
    return summary

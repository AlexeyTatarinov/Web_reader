"""Read-only look at a logged-in page: structure and data requests, without page text or secrets.

Used to learn how a portal is built (links, expandable tree, data calls) before writing a crawler.
Only paths of requests are reported (query strings are dropped).
"""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlsplit

_PROBE_JS = """() => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const anchors = [...document.querySelectorAll('a[href]')];
  const sample = anchors.filter(vis).slice(0, 40).map(a => {
    let path = ''; try { path = new URL(a.href, location.href).pathname; } catch (e) {}
    return ((a.innerText || '').trim().slice(0, 50) || '(no text)') + ' -> ' + path.slice(0, 90); });
  const count = sel => document.querySelectorAll(sel).length;
  return {title: document.title, url: location.origin + location.pathname,
          heading: (document.querySelector('h1') || {innerText: ''}).innerText.trim().slice(0, 80),
          textLength: (document.body ? document.body.innerText : '').length,
          anchors: anchors.length, sample: sample,
          expanders: {ariaExpanded: count('[aria-expanded]'), details: count('details'),
                      treeitems: count('[role=treeitem]'), tree: count('[role=tree]')},
          iframes: count('iframe'), buttons: count('button'), headings: count('h1,h2,h3')};
}"""


async def probe_page(page: Any, url: str, log: Callable[[str], None] = print, settle_ms: int = 3000) -> None:
    seen: list[str] = []

    def on_response(response: Any) -> None:
        request = response.request
        if request.resource_type in ("xhr", "fetch", "document"):
            parts = urlsplit(response.url)
            seen.append(f"{request.method} {response.status} {request.resource_type} {parts.hostname}{parts.path[:100]}")

    page.on("response", on_response)
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    await page.wait_for_timeout(settle_ms)
    info = await page.evaluate(_PROBE_JS)
    log(f"probe: page {info['url']} title={info['title']!r} heading={info['heading']!r} text_length={info['textLength']}")
    log(f"probe: anchors={info['anchors']} buttons={info['buttons']} headings={info['headings']} iframes={info['iframes']}")
    log(f"probe: expanders={info['expanders']}")
    for line in info["sample"]:
        log(f"probe: link {line}")
    unique = list(dict.fromkeys(seen))
    log(f"probe: requests {len(unique)} distinct")
    for line in unique[:40]:
        log(f"probe: request {line}")

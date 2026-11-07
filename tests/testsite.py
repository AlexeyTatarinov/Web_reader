"""Локальный тестовый сайт с типичными «ловушками» для чтения страниц."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

FILLER = "<p>" + "This is a long explanatory paragraph about our company, culture and the way we work together. " * 4 + "</p>"


def make_pdf(text: str) -> bytes:
    """Минимальный корректный PDF с одной строкой текста."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    ]
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def page(body: str, title: str = "Careers", head: str = "") -> bytes:
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>{head}</head><body>{body}</body></html>".encode()


def jobs_json(first: int, count: int) -> dict:
    return {"jobs": [{"title": f"Role {n} QA Tester", "url": f"/jobs/role-{n}", "location": "Valencia, Spain"}
                     for n in range(first, first + count)]}


PAGES: dict[str, bytes] = {}

PAGES["/dynamic"] = page(
    "<h1>Careers</h1>" + FILLER + "<div id='list'></div>"
    "<script>setTimeout(()=>{document.getElementById('list').innerHTML="
    "'<ul><li><a href=\"/jobs/qa-engineer-1\">Senior QA Engineer</a> Valencia</li>"
    "<li><a href=\"/jobs/lqa-ru-2\">Localization QA (Russian)</a> Remote</li></ul>'},800)</script>")

PAGES["/loadmore"] = page(
    "<h1>Open roles</h1><p>15 jobs</p>" + FILLER + "<ul id='jobs'></ul><button id='more'>Load more</button>"
    "<script>let page=0;"
    "async function more(){page++;const r=await fetch('/api/jobs?page='+page);const d=await r.json();"
    "d.jobs.forEach(j=>{const li=document.createElement('li');li.innerHTML='<a href=\"'+j.url+'\">'+j.title+'</a> '+j.location;"
    "document.getElementById('jobs').appendChild(li);});"
    "if(page>=3)document.getElementById('more').remove();}"
    "document.getElementById('more').addEventListener('click',more);more();</script>")

PAGES["/infinite"] = page(
    "<h1>Infinite list</h1>" + FILLER + "<div id='list'></div>"
    "<script>let n=0;function add(){for(let i=0;i<10&&n<60;i++){n++;const d=document.createElement('div');"
    "d.style.minHeight='120px';d.innerHTML='<a href=\"/jobs/inf-'+n+'\">Infinite role '+n+'</a>';"
    "document.getElementById('list').appendChild(d);}}add();"
    "let last=0;window.addEventListener('scroll',()=>{if(Date.now()-last>600&&window.innerHeight+window.scrollY>=document.documentElement.scrollHeight-250){last=Date.now();add();}});</script>")

PAGES["/iframe"] = page(
    "<h1>Company careers</h1>" + FILLER + "<iframe src='/embed' width='700' height='300'></iframe>")
PAGES["/embed"] = page(
    "<h2>Embedded board</h2>" + FILLER + "<div id='x'></div>"
    "<script>setTimeout(()=>{document.getElementById('x').innerHTML='<a href=\"/jobs/embedded-9\">Embedded Compliance Manager</a>'},300)</script>",
    title="Embedded")

PAGES["/shadow"] = page(
    "<h1>Shadow cards</h1>" + FILLER +
    "<script>customElements.define('job-card',class extends HTMLElement{constructor(){super();"
    "const r=this.attachShadow({mode:'open'});r.innerHTML='<div><h3>Shadow Tester Role</h3><a href=\"/jobs/shadow-1\">Apply in shadow</a></div>';}});</script>"
    "<job-card></job-card>")

PAGES["/cookie"] = page(
    "<h1>Cookie site</h1>" + FILLER + "<p id='state'>not accepted</p>"
    "<div id='cookie-banner' class='cookie-consent' role='dialog' style='position:fixed;bottom:0;left:0;right:0;background:#eee;padding:20px;z-index:1000'>"
    "We use cookies. <button onclick=\"document.getElementById('state').textContent='ACCEPTED';this.parentNode.remove()\">Accept all</button> "
    "<button onclick=\"this.parentNode.remove()\">Reject all</button></div>")

PAGES["/cookie_settings"] = page(
    "<h1>Cookie settings site</h1>" + FILLER + "<p id='state'>not accepted</p>"
    "<div id='cookie-banner' class='cookie-consent' role='dialog' style='position:fixed;bottom:0;left:0;right:0;background:#eee;padding:20px;z-index:1000'>"
    "<p>This website uses cookies</p>"
    "<div id='main'><button onclick=\"document.getElementById('state').textContent='ACCEPTED';this.closest('#cookie-banner').remove()\">Allow all</button> "
    "<button onclick=\"document.getElementById('main').hidden=true;document.getElementById('panel').hidden=false\">Cookie settings</button></div>"
    "<div id='panel' hidden><label><input type='checkbox' checked disabled> Necessary</label> "
    "<label><input id='an' type='checkbox' checked> Analytics</label> <label><input id='mk' type='checkbox' checked> Marketing</label> "
    "<button onclick=\"document.getElementById('state').textContent='SAVED an='+document.getElementById('an').checked+' mk='+document.getElementById('mk').checked;this.closest('#cookie-banner').remove()\">Save</button></div></div>")

PAGES["/cookie_no_way_out"] = page(
    "<h1>Cookie trap site</h1>" + FILLER + "<p id='state'>not accepted</p>"
    "<div class='cookie-consent' style='position:fixed;bottom:0;left:0;right:0;background:#eee;padding:20px;z-index:1000'>"
    "<p>This website uses cookies to personalise content and ads</p>"
    "<button onclick=\"document.getElementById('state').textContent='ACCEPTED'\">Allow all</button></div>")

PAGES["/popup"] = page(
    "<h1>Popup site</h1>" + FILLER +
    "<div class='modal' role='dialog' style='position:fixed;top:10%;left:15%;width:70%;height:70%;z-index:2000;background:white;border:1px solid #000'>"
    "<p>Subscribe to our newsletter now!</p><button aria-label='Close' class='close' onclick=\"this.parentNode.remove()\">×</button></div>")

PAGES["/tabs"] = page(
    "<h1>Teams</h1>" + FILLER +
    "<div role='tablist'><button role='tab' aria-selected='true' id='t1'>Engineering</button>"
    "<button role='tab' aria-selected='false' id='t2'>Design</button></div>"
    "<div id='p1'>Engineering roles: Backend Developer, QA Automation Engineer</div>"
    "<div id='p2' hidden>Design roles: Product Designer, UX Researcher</div>"
    "<button aria-expanded='false' aria-controls='acc1' id='b1'>Benefits</button>"
    "<div id='acc1' hidden>Benefits: health insurance and relocation package</div>"
    "<details><summary>Relocation policy</summary><p>We support relocation within the EU</p></details>"
    "<script>"
    "t1.onclick=()=>{p1.hidden=false;p2.hidden=true;t1.setAttribute('aria-selected','true');t2.setAttribute('aria-selected','false')};"
    "t2.onclick=()=>{p1.hidden=true;p2.hidden=false;t2.setAttribute('aria-selected','true');t1.setAttribute('aria-selected','false')};"
    "b1.onclick=()=>{acc1.hidden=false;b1.setAttribute('aria-expanded','true')};</script>")

PAGES["/spam"] = page(
    "<h1>Endless page</h1>" + FILLER + "<div id='log'></div>"
    "<script>let i=0;setInterval(async()=>{i++;try{const r=await fetch('/ping?i='+i);const t=await r.text();"
    "const d=document.createElement('div');d.textContent='tick '+i+' '+t;document.getElementById('log').appendChild(d);}catch(e){}},20);</script>")

PAGES["/jsononly"] = page(
    "<h1>Positions</h1><p>Showing 1-3 of 3 jobs</p>" + FILLER +
    "<canvas id='c'></canvas><script>fetch('/api/all').then(r=>r.json()).then(d=>{window.__jobs=d;})</script>")
PAGES["/jsononly_short"] = page(
    "<h1>Positions</h1><p>Showing 1-3 of 40 jobs</p>" + FILLER +
    "<canvas id='c'></canvas><script>fetch('/api/all').then(r=>r.json()).then(d=>{window.__jobs=d;})</script>")

PAGES["/blocked"] = page(
    "<h1>Checking your browser before accessing the site.</h1><p>Please wait.</p>", title="Just a moment...")

PAGES["/closed"] = page(
    "<h1>Senior QA Engineer</h1>" + FILLER + "<p>This job is no longer available.</p>", title="Senior QA Engineer - Acme")

PAGES["/about"] = page("<h1>About us</h1>" + FILLER, title="About us")

PAGES["/listing"] = page(
    "<h1>Careers at Acme</h1><p>3 jobs</p>" + FILLER + "<ul id='top'>"
    "<li><a href='/jobs/qa-a-1'>QA Engineer A</a></li>"
    "<li><a href='/jobs/lqa-b-2'>LQA Russian</a></li></ul>"
    "<a href='/listing?page=2' rel='next'>Next</a> "
    "<a href='/about'>About us</a> <a href='/files/jd.pdf'>Download job description</a> "
    "<a href='https://example.org/partner'>Partner</a> <a href='https://www.linkedin.com/company/acme'>LinkedIn</a> "
    "<a href='mailto:jobs@acme.test'>Email us</a> <a href='#top'>Back to top</a> <a href='javascript:void(0)'>Nothing</a>",
    title="Careers at Acme")
PAGES["/cards"] = page(
    "<h1>Join us</h1>" + FILLER +
    "<div class='cards'>"
    "<div class='card'><h3>Game QA Tester</h3><p>Hiring testers for console games</p><a href='/jobs/card-qa-1'>More Details</a></div>"
    "<div class='card'><h3>LQA Ukrainian</h3><p>Linguistic testing</p><a href='/jobs/card-lqa-2'>Apply Now</a></div>"
    "<div class='card'><h3>Unity Developer</h3><p>Programmer</p><a href='/jobs/card-dev-3'>Details</a></div></div>",
    title="Join us")
PAGES["/cards2"] = page(
    "<h1>Careers</h1>" + FILLER + "<div class='board'>"
    "<div class='opening'><div class='t'>QA Tester - Consoles (Compliance &amp; DevKits)</div><div>Mexico</div>"
    "<a href='/hub/?module=interview&jobOpeningId=334'>Apply Now</a><a href='/hub/?module=interview&jobDescriptionId=17'>View Job Description</a></div>"
    "<div class='opening'><div class='t'>Technical Artist</div><div>Remote</div><a href='/r/TA-OCT-26'>Apply!</a></div></div>"
    "<p><a href='/about'>Apply</a></p>", title="Careers")
PAGES["/flakylist"] = page(
    "<h1>Careers at Flaky Co</h1>" + FILLER + "<ul><li><a href='/jobs/ok-qa-1'>QA Fine</a></li>"
    "<li><a href='/jobs/flaky-qa-2'>QA Flaky</a></li></ul>", title="Careers at Flaky Co")
PAGES["/listing?page=2"] = page(
    "<h1>Careers at Acme, page 2</h1>" + FILLER + "<ul><li><a href='/jobs/compliance-c-3'>Compliance Manager</a></li></ul>",
    title="Careers at Acme 2")


def job_page(slug: str) -> bytes:
    title = slug.replace("-", " ").title()
    ld = json.dumps({"@context": "https://schema.org", "@type": "JobPosting", "title": title,
                     "hiringOrganization": {"@type": "Organization", "name": "Acme"},
                     "jobLocation": {"@type": "Place", "address": {"addressLocality": "Valencia", "addressCountry": "ES"}},
                     "datePosted": "2026-11-01", "validThrough": "2026-12-31", "description": "<p>Test games.</p>"})
    return page(f"<h1>{title}</h1>" + FILLER + "<p>Responsibilities: test games. Requirements: attention. Apply now.</p>",
                title=f"{title} - Acme", head=f"<script type='application/ld+json'>{ld}</script>")


FLAKY_COUNTS: dict[str, int] = {}
FLAKY_DENIALS: dict[str, int] = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # тишина в тестах
        pass

    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8", extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query)
        try:
            if path == "/api/jobs":
                n = int(query.get("page", ["1"])[0])
                self._send(200, json.dumps(jobs_json((n - 1) * 5 + 1, 5)).encode(), "application/json")
            elif path == "/api/all":
                self._send(200, json.dumps(jobs_json(101, 3)).encode(), "application/json")
            elif path == "/ping":
                self._send(200, b"pong", "text/plain")
            elif path == "/files/jd.pdf":
                self._send(200, make_pdf("Job description QA Tester for Acme"), "application/pdf")
            elif path == "/hang":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Hanging page</h1>")
                self.wfile.flush()
                end = time.time() + 40
                while time.time() < end:
                    time.sleep(0.5)
                    self.wfile.write(b" ")
                    self.wfile.flush()
            elif path.startswith("/jobs/flaky-"):
                # первые FLAKY_DENIALS запросов к странице получают «Access denied», потом страница открывается
                seen = FLAKY_COUNTS.get(path, 0)
                FLAKY_COUNTS[path] = seen + 1
                if seen < FLAKY_DENIALS.get(path, 1):
                    self._send(403, page("<h1>Access denied</h1>", title="Access Denied"))
                else:
                    self._send(200, job_page(path.split("/", 2)[2]))
            elif path == "/forbidden":
                self._send(403, page("<h1>Access denied</h1>", title="Error"))
            elif path == "/missing":
                self._send(404, page("<h1>Page not found</h1>" + FILLER, title="404"))
            elif path.startswith("/jobs/"):
                self._send(200, job_page(path.split("/", 2)[2]))
            elif self.path in PAGES:
                self._send(200, PAGES[self.path])
            elif path in PAGES:
                self._send(200, PAGES[path])
            else:
                self._send(404, page("<h1>Not found</h1>" + FILLER, title="Not found"))
        except (BrokenPipeError, ConnectionResetError):
            pass


def start_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"

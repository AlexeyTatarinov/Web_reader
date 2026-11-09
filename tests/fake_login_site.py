"""Local fake of a multi-step login (email -> password -> emailed code) and a fake Telegram Bot API."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def _html(body: str) -> bytes:
    return f"<!doctype html><html><head><meta charset='utf-8'><title>Sign in</title></head><body>{body}</body></html>".encode()


EMAIL_FORM = (
    "<h1>Log in</h1><form id='f' method='post' action='/login/email'>"
    "<input type='email' name='username' id='username' autocomplete='username'>"
    "<button type='submit' id='login-submit'>Continue</button></form>"
    "<script>document.getElementById('f').addEventListener('submit',async e=>{e.preventDefault();"
    "await fetch('/login/email',{method:'POST',body:document.getElementById('username').value});"
    "document.body.innerHTML=\"<h1>Welcome back</h1><form method='post' action='/login/password'>"
    "<input type='password' name='password' id='password'><button type='submit' id='login-submit'>Log in</button></form>\";});"
    "</script>"
)


def password_form(error: str = "") -> bytes:
    note = f"<p class='error'>{error}</p>" if error else ""
    return _html("<h1>Welcome back</h1>" + note + "<form method='post' action='/login/password'>"
                 "<input type='password' name='password' id='password'><button type='submit'>Log in</button></form>")


ODD = {"on": False}


def code_form(error: str = "") -> bytes:
    note = f"<p class='error'>{error}</p>" if error else ""
    if ODD["on"]:  # an input that matches none of the usual code selectors
        return _html("<h1>Check your email</h1><p>We sent a 6-character code to us***@example.com</p>" + note +
                     "<form method='post' action='/login/code'><input type='text' name='field1' id='f1'>"
                     "<button type='submit'>Verify</button></form>")
    return _html("<h1>Check your email</h1><p>We sent a code to your email.</p>" + note +
                 "<form method='post' action='/login/code'><input name='code' autocomplete='one-time-code' inputmode='numeric'>"
                 "<button type='submit'>Verify</button></form>")


class LoginSite(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, password="s3cret-Pass", code="482915"):
        super().__init__(("127.0.0.1", 0), _LoginHandler)
        self.password, self.code = password, code
        self.events: list[str] = []
        self.captured: list[str] = []  # everything the pages received (to prove where secrets went)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _LoginHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, body: bytes, status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, headers=None):
        self._send(b"", 302, {"Location": location, **(headers or {})})

    def _body(self) -> str:
        return self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)).decode()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/app":
            if "sid=ok" in (self.headers.get("Cookie") or ""):
                return self._send(_html("<h1>Knowledge base</h1><p>Welcome, you are logged in.</p>"
                                        "<a href='/app/guide-1'>Guide one</a><button aria-expanded='false'>More</button>"
                                        "<script>fetch('/api/tree?secret=1')</script>"))
            return self._redirect("/login")
        if path == "/api/tree":
            return self._send(b"{}", 200, {"Content-Type": "application/json"})
        if path == "/login":
            return self._send(_html(EMAIL_FORM))
        if path == "/to_other_host":
            return self._redirect(f"http://localhost:{self.server.server_address[1]}/login")
        self._send(_html("not found"), 404)

    def do_POST(self):
        path = urlsplit(self.path).path
        body = self._body()
        self.server.captured.append(body)
        if path == "/login/email":
            self.server.events.append("email")
            return self._send(b"ok")
        if path == "/login/password":
            self.server.events.append("password")
            if parse_qs(body).get("password", [""])[0] != self.server.password:
                return self._send(password_form("Incorrect email address and / or password"))
            return self._send(code_form())
        if path == "/login/code":
            self.server.events.append("code")
            fields = parse_qs(body)
            if (fields.get("code") or fields.get("field1") or [""])[0].replace(" ", "") != self.server.code:
                return self._send(code_form("That code is not valid"))
            return self._redirect("/app", {"Set-Cookie": "sid=ok; Path=/"})
        self._send(_html("not found"), 404)


class FakeTelegram(ThreadingHTTPServer):
    """Implements sendMessage, getUpdates (short poll) and deleteMessage for chat tests."""
    daemon_threads = True

    def __init__(self, token="TESTTOKEN"):
        super().__init__(("127.0.0.1", 0), _TelegramHandler)
        self.token = token
        self.updates: list[dict] = []
        self.sent: list[dict] = []
        self.deleted: list[int] = []
        self._lock = threading.Lock()
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def push(self, chat_id: int, text: str) -> int:
        with self._lock:
            update_id = (self.updates[-1]["update_id"] + 1) if self.updates else 100
            self.updates.append({"update_id": update_id,
                                 "message": {"message_id": update_id, "chat": {"id": chat_id}, "text": text}})
            return update_id

    def push_later(self, delay: float, chat_id: int, text: str) -> None:
        threading.Timer(delay, self.push, args=(chat_id, text)).start()


class _TelegramHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            data = {}
        parts = urlsplit(self.path).path.strip("/").split("/")
        server: FakeTelegram = self.server  # type: ignore[assignment]
        if len(parts) != 2 or parts[0] != f"bot{server.token}":
            return self._json({"ok": False, "description": "Unauthorized"}, 401)
        method = parts[1]
        if method == "getMe":
            return self._json({"ok": True, "result": {"id": 1, "is_bot": True, "username": "test_login_bot"}})
        if method == "sendMessage":
            server.sent.append(data)
            return self._json({"ok": True, "result": {"message_id": len(server.sent)}})
        if method == "deleteMessage":
            server.deleted.append(data.get("message_id"))
            return self._json({"ok": True, "result": True})
        if method == "getUpdates":
            offset = data.get("offset")
            with server._lock:
                if offset is not None and offset < 0:
                    result = server.updates[-1:]
                else:
                    result = [u for u in server.updates if offset is None or u["update_id"] >= offset]
            if not result:
                time.sleep(min(float(data.get("timeout", 0) or 0), 0.2))
            return self._json({"ok": True, "result": result})
        self._json({"ok": False, "description": "unknown method"}, 404)

    def _json(self, payload: dict, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

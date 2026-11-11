"""Login to Atlassian (email -> password -> one-time code from email) with the code requested via Telegram.

The program types the credentials only on trusted hosts, never retries a rejected password (to avoid
locking the account), never logs secrets, and never tries to get around bot protection: captchas and
similar checks are reported and the run stops.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from . import antibot
from .i18n import t

TRUSTED_SUFFIXES = (".atlassian.com", ".atlassian.net")
LOGIN_BUTTON_RE = re.compile(r"^\s*(log ?in|sign ?in|войти|увійти)", re.I)

_GENERIC_INPUT = ("input:not([type=hidden]):not([type=password]):not([type=email]):not([type=checkbox])"
                  ":not([type=radio]):not([type=submit]):not([type=button])")
_STAGE_JS = """() => {
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !el.disabled; };
  const q = sel => [...document.querySelectorAll(sel)].filter(visible);
  const mark = sel => q(sel).some(el => !el.hasAttribute('data-wr-seen'));
  const GENERIC_INPUT = "__GENERIC__";
  const pw = 'input[type=password]';
  const code = 'input[autocomplete=one-time-code], input[name*=code i], input[id*=code i], input[inputmode=numeric], input[name*=otp i], input[id*=otp i]';
  const email = 'input[type=email], input[name=username], input#username, input[autocomplete=username]';
  const stage = q(pw).length ? 'password' : q(code).length ? 'code' : q(email).length ? 'email' : null;
  const sel = {password: pw, code: code, email: email}[stage];
  const error = q('[role=alert], .error, [class*=error i], [id*=error i], [data-testid*=error i]')
    .some(el => (el.innerText || '').trim().length > 0);
  const generic = q(GENERIC_INPUT);
  return {stage: stage, fresh: stage ? mark(sel) : false, error: error,
          generic: generic.length > 0, genericFresh: generic.some(el => !el.hasAttribute('data-wr-seen'))};
}"""
_MARK_JS = "() => document.querySelectorAll('input').forEach(el => el.setAttribute('data-wr-seen', '1'))"
_SELECTORS = {
    "email": "input[type=email], input[name=username], input#username, input[autocomplete=username]",
    "password": "input[type=password]",
    "code": ("input[autocomplete=one-time-code], input[name*=code i], input[id*=code i], "
             "input[inputmode=numeric], input[name*=otp i], input[id*=otp i]"),
}


_STAGE_JS = _STAGE_JS.replace("__GENERIC__", _GENERIC_INPUT)


class LoginError(Exception):
    """Login stopped. `reason` is a short code, `stage` where it happened; the text never holds secrets."""

    def __init__(self, reason: str, stage: str = "", message: str = "") -> None:
        self.reason, self.stage, self.message = reason, stage, message
        super().__init__(f"{reason}" + (f" ({stage})" if stage else "") + (f": {message}" if message else ""))


@dataclass(frozen=True)
class Credentials:
    email: str = field(repr=False)
    password: str = field(repr=False)


@dataclass
class LoginResult:
    ok: bool
    stages: list[str]


def extract_code(text: str) -> str | None:
    """Extracts a one-time code from a chat reply.

    Codes may be digits only (spaces or dashes inside are ignored) or letters and digits (e.g. 6E5IUQ).
    A 4-8 character token counts as a code when it contains a digit, or when the whole reply is exactly
    six capital letters (so ordinary words such as "hello" are never taken for a code).
    """
    text = (text or "").strip()
    for match in re.finditer(r"\d(?:[ \-]?\d)+", text):
        digits = re.sub(r"\D", "", match.group())
        if 4 <= len(digits) <= 8:
            return digits
    tokens = re.findall(r"[A-Za-z0-9]{4,8}", text)
    for token in tokens:
        if re.search(r"\d", token) or re.fullmatch(r"[A-Z]{6}", text):
            return token.upper()
    return None


class TelegramPrompter:
    """Asks the owner for the emailed code in Telegram and waits for the reply."""

    def __init__(self, token: str, chat_id: int | str, lang: str = "en",
                 api_base: str = "https://api.telegram.org", poll_timeout_s: int = 20) -> None:
        self._token, self._chat_id, self._lang = token, str(chat_id), lang
        self._api_base = api_base.rstrip("/")
        self._poll = max(1, int(poll_timeout_s))
        local = urlsplit(self._api_base).hostname in ("127.0.0.1", "localhost")
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local \
            else urllib.request.build_opener()

    def _call(self, method: str, payload: dict[str, Any], http_timeout: float = 30) -> Any:
        request = urllib.request.Request(f"{self._api_base}/bot{self._token}/{method}",
                                         data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=http_timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = str(json.load(exc).get("description", ""))[:120]
            except Exception:
                pass
            raise LoginError("telegram", "", f"Telegram API refused the request (HTTP {exc.code}) {detail}".strip()) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise LoginError("telegram", "", f"Telegram API is not reachable ({type(exc).__name__})") from None
        if not data.get("ok"):
            raise LoginError("telegram", "", "Telegram API returned an error")
        return data.get("result")

    def check(self, text: str) -> str:
        """Sends a test message and returns the bot's @username, so the owner knows which bot to open.

        Raises LoginError('telegram') when the token is wrong or the bot cannot write to the chat
        (for example the owner has not pressed Start in the bot yet).
        """
        info = self._call("getMe", {}) or {}
        self._call("sendMessage", {"chat_id": self._chat_id, "text": text})
        return str(info.get("username", ""))

    def notify(self, text: str) -> None:
        """Best-effort message to the owner; never raises."""
        try:
            self._call("sendMessage", {"chat_id": self._chat_id, "text": text})
        except LoginError:
            pass

    def request_code(self, timeout_s: float, hint: str = "first") -> str | None:
        """Sends the request, waits up to `timeout_s` for a reply from the configured chat with a code."""
        last = self._call("getUpdates", {"offset": -1, "timeout": 0, "allowed_updates": ["message"]})
        offset = last[-1]["update_id"] + 1 if last else None
        key = "login_code_retry" if hint == "retry" else "login_code_request"
        self._call("sendMessage", {"chat_id": self._chat_id,
                                   "text": t(self._lang, key, minutes=max(1, round(timeout_s / 60)))})
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            wait = max(1, min(self._poll, int(deadline - time.monotonic())))
            payload: dict[str, Any] = {"timeout": wait, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            for update in self._call("getUpdates", payload, http_timeout=wait + 15) or []:
                offset = update["update_id"] + 1
                message = update.get("message") or {}
                if str((message.get("chat") or {}).get("id")) != self._chat_id:
                    continue  # only the owner's chat is trusted
                code = extract_code(message.get("text", ""))
                if code:
                    try:  # remove the code from the chat history, best effort
                        self._call("deleteMessage", {"chat_id": self._chat_id, "message_id": message.get("message_id")})
                    except LoginError:
                        pass
                    return code
        return None


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _trusted(host: str, trusted: set[str]) -> bool:
    return host in trusted or any(host.endswith(suffix) for suffix in TRUSTED_SUFFIXES)


async def _state(page: Any) -> dict[str, Any]:
    try:
        return await page.evaluate(_STAGE_JS)
    except Exception:  # the page is navigating: the old context is gone
        return {"stage": "busy", "fresh": False, "error": False}


async def _page_text(page: Any) -> tuple[str, str]:
    try:
        return await page.title(), (await page.evaluate("() => document.body ? document.body.innerText : ''"))[:4000]
    except Exception:
        return "", ""


_DIAG_JS = """() => {
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const inputs = [...document.querySelectorAll('input')].filter(visible)
    .map(el => [el.type, el.name, el.id, el.autocomplete, el.inputMode].join('/')).slice(0, 8);
  const buttons = [...document.querySelectorAll('button, [role=button]')].filter(visible)
    .map(el => (el.innerText || '').trim().slice(0, 30)).filter(Boolean).slice(0, 6);
  return {inputs: inputs, buttons: buttons};
}"""


async def _diagnostics(page: Any) -> str:
    """Safe description of the current page for the error message: no input values, emails masked."""
    title, text = await _page_text(page)
    try:
        info = await page.evaluate(_DIAG_JS)
    except Exception:
        info = {"inputs": [], "buttons": []}
    snippet = re.sub(r"\s+", " ", text)[:200]
    snippet = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "***@***", snippet)
    return (f"host={_host(page.url)} path={urlsplit(page.url).path[:40]} title={title[:60]!r} "
            f"inputs={info['inputs']} buttons={info['buttons']} text={snippet!r}")


async def _click_login_button(page: Any) -> bool:
    for role in ("link", "button"):
        locator = page.get_by_role(role, name=LOGIN_BUTTON_RE)
        try:
            if await locator.count() and await locator.first.is_visible():
                await locator.first.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


async def _submitted_by_itself(page: Any, had_error: bool, wait_s: float = 2.0) -> bool:
    """True when the page reacted to the typed code on its own (re-rendered, navigated or showed a new error).

    An error that was already on the page before the code was typed (from a previous attempt) proves nothing.
    """
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        await asyncio.sleep(0.25)
        state = await _state(page)
        new_error = bool(state["error"]) and not had_error
        if state["stage"] == "busy":
            return True
        if state["stage"] is None:
            if not state.get("generic"):
                return True  # no input left: the page moved on
            if state.get("genericFresh") or new_error:  # the unrecognised code page
                return True
            continue
        if state["stage"] != "code" or state["fresh"] or new_error:
            return True
    return False


async def _submit(page: Any, stage: str, value: str) -> None:
    await page.evaluate(_MARK_JS)
    had_error = bool((await _state(page)).get("error"))
    field_locator = page.locator(_SELECTORS[stage]).first
    if stage == "code" and not await field_locator.count():
        field_locator = page.locator(_GENERIC_INPUT).first
        if not await field_locator.count():
            raise LoginError("unknown_page", "code", await _diagnostics(page))
    if stage == "code":
        await field_locator.click(timeout=5000)
        await page.keyboard.type(value, delay=40)  # also works when the code is split into one box per digit
        if await _submitted_by_itself(page, had_error):
            return  # many code forms submit on the last character: a second Enter would send the code twice
    else:
        await field_locator.fill(value, timeout=5000)
    await page.keyboard.press("Enter")


async def _after_submit(page: Any, stage: str, wait_s: float = 25.0, fresh_grace_s: float = 6.0) -> str:
    """Waits until the page reacts. Returns 'progressed' or 'rejected'; raises on silence.

    A code page that re-renders its boxes while it verifies the code is not a rejection: new empty fields only
    count after `fresh_grace_s` seconds without any progress; a visible error text counts at once.
    """
    deadline = time.monotonic() + wait_s
    fresh_since: float | None = None
    while time.monotonic() < deadline:
        await asyncio.sleep(0.4)
        state = await _state(page)
        if state["stage"] == "busy":
            continue
        if state["stage"] is None and stage == "code" and state.get("generic"):
            fresh = bool(state.get("genericFresh"))  # the unrecognised code page
            if not fresh and not state["error"]:
                return "progressed"
        elif state["stage"] != stage:
            return "progressed"
        else:
            fresh = bool(state["fresh"])
        if state["error"]:
            return "rejected"
        if fresh:
            if stage != "code":
                return "rejected"
            fresh_since = fresh_since or time.monotonic()
            if time.monotonic() - fresh_since > fresh_grace_s:
                return "rejected"
        else:
            fresh_since = None
    raise LoginError("timeout", stage, "the page did not react to the submitted value")


async def login_atlassian(page: Any, creds: Credentials,
                          ask_code: Callable[[str], Awaitable[str | None]], *, start_url: str,
                          trusted_hosts: set[str] | None = None, overall_timeout_s: float = 600.0,
                          expect_text: str | None = None, settle_polls: int = 3,
                          log: Callable[[str], None] = lambda _msg: None) -> LoginResult:
    trusted = {_host(start_url)} | {h.lower() for h in (trusted_hosts or set())}
    deadline = time.monotonic() + overall_timeout_s
    try:
        await page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
    except Exception as exc:
        raise LoginError("navigation", "", f"could not open the start page ({type(exc).__name__})") from None

    stages: list[str] = []
    submits = {"email": 0, "password": 0, "code": 0}
    quiet, clicked_login = 0, False
    unknown_polls, unknown_since = 0, None

    def note(stage: str) -> None:
        if not stages or stages[-1] != stage:
            stages.append(stage)
            log(f"login stage: {stage}")

    while time.monotonic() < deadline:
        state = await _state(page)
        stage = state["stage"]
        if stage == "busy":
            await asyncio.sleep(0.4)
            continue
        if stage is None:
            title, text = await _page_text(page)
            blocked = antibot.detect(title, text, None, None)
            if blocked:
                raise LoginError("blocked", stages[-1] if stages else "", f"bot protection detected ({blocked})")
            if not stages and not clicked_login and quiet >= 1 and await _click_login_button(page):
                clicked_login, quiet = True, 0
                continue
            path = urlsplit(page.url).path.lower()
            on_app = _host(page.url) == _host(start_url) and "/login" not in path
            quiet = quiet + 1 if on_app else 0
            if quiet >= settle_polls and (not expect_text or expect_text in text):
                note("done")
                return LoginResult(True, stages)
            if not on_app:
                unknown_since = unknown_since or time.monotonic()
                unknown_polls += 1
                if stages and stages[-1] in ("password", "code") and unknown_polls >= 3 and \
                        _trusted(_host(page.url), trusted):
                    stage = "code"  # the page after the password is not recognised: ask the owner for the code
                    unknown_polls, unknown_since = 0, None
                elif time.monotonic() - unknown_since > 45:
                    raise LoginError("unknown_page", stages[-1] if stages else "", await _diagnostics(page))
            else:
                unknown_polls, unknown_since = 0, None
            if stage is None:
                await asyncio.sleep(0.4)
                continue
        else:
            unknown_polls, unknown_since = 0, None

        quiet = 0
        host = _host(page.url)
        if not _trusted(host, trusted):
            raise LoginError("untrusted_host", stage, "the login page is on a host that is not trusted")
        note(stage)
        if stage == "email":
            if submits["email"] >= 2:
                raise LoginError("rejected", "email", "the email was not accepted")
            submits["email"] += 1
            await _submit(page, "email", creds.email)
        elif stage == "password":
            if submits["password"] >= 1:
                raise LoginError("rejected", "password", "the password was not accepted")
            submits["password"] += 1
            await _submit(page, "password", creds.password)
        else:  # code
            if submits["code"] >= 2:
                raise LoginError("rejected", "code", "the code was not accepted twice")
            code = await ask_code("retry" if submits["code"] else "first")
            if not code:
                raise LoginError("code_timeout", "code", "no code was received in time")
            submits["code"] += 1
            log(f"login: submitting a code of {len(code)} characters (attempt {submits['code']})")
            await _submit(page, "code", code)
        outcome = await _after_submit(page, stage)
        if outcome == "rejected":
            if stage == "code":
                log("login: the code page after submission: " + await _diagnostics(page))
            if stage == "code" and submits["code"] < 2:
                continue  # one more try: the loop asks the owner again
            raise LoginError("rejected", stage, f"the {stage} was not accepted; page: " + await _diagnostics(page))
    raise LoginError("timeout", stages[-1] if stages else "", "login did not finish in time")

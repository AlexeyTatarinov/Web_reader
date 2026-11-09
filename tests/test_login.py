"""Tests for the Atlassian-style login: written before the implementation (see docs/login-spec.md)."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_login_site import FakeTelegram, LoginSite  # noqa: E402
from conftest import make_settings  # noqa: E402
from web_reader.browser import context_options, launch_browser  # noqa: E402
from web_reader.login import (Credentials, LoginError, TelegramPrompter, extract_code,  # noqa: E402
                              login_atlassian)

EMAIL, PASSWORD, CODE, CHAT = "owner@example.com", "s3cret-Pass", "482915", 777


# ---------- pure functions and the Telegram prompter ----------

def test_extract_code_takes_first_4_to_8_digit_number():
    assert extract_code("482915") == "482915"
    assert extract_code("code is 482 915 thanks") == "482915"
    assert extract_code("here: 1234") == "1234"
    assert extract_code("no digits here") is None
    assert extract_code("123") is None
    assert extract_code("") is None


@pytest.fixture()
def telegram():
    server = FakeTelegram()
    yield server
    server.shutdown()


def make_prompter(telegram, **kwargs):
    return TelegramPrompter("TESTTOKEN", CHAT, lang="en", api_base=telegram.url, poll_timeout_s=1, **kwargs)


def test_prompter_returns_code_from_right_chat_and_ignores_old_and_foreign_messages(telegram):
    telegram.push(CHAT, "111111")                       # old message from before the request
    prompter = make_prompter(telegram)
    telegram.push_later(0.3, 999, "222222")             # foreign chat
    telegram.push_later(0.6, CHAT, "hello")             # no digits
    telegram.push_later(0.9, CHAT, "my code 333444")
    assert prompter.request_code(10, "first") == "333444"
    assert len(telegram.sent) == 1
    assert str(telegram.sent[0]["chat_id"]) == str(CHAT)


def test_prompter_times_out_with_none(telegram):
    assert make_prompter(telegram).request_code(1.0, "first") is None


def test_prompter_wrong_token_raises_clear_error(telegram):
    prompter = TelegramPrompter("BADTOKEN", CHAT, lang="en", api_base=telegram.url, poll_timeout_s=1)
    with pytest.raises(LoginError) as info:
        prompter.request_code(2, "first")
    assert info.value.reason == "telegram"
    assert "BADTOKEN" not in str(info.value)


def test_prompter_message_is_localised_and_has_no_secrets(telegram):
    for lang in ("ru", "en", "uk"):
        TelegramPrompter("TESTTOKEN", CHAT, lang=lang, api_base=telegram.url, poll_timeout_s=1).request_code(0.2, "retry")
    texts = [m["text"] for m in telegram.sent]
    assert len(set(texts)) == 3
    assert all(PASSWORD not in text for text in texts)


# ---------- login flow in a real browser against the fake site ----------

@pytest.fixture()
def site():
    server = LoginSite(PASSWORD, CODE)
    yield server
    server.shutdown()


def run_login(site, codes, password=PASSWORD, start_path="/app", **kwargs):
    """codes: list of values the fake owner 'sends' (None = no reply). Returns (result_or_exc, asked_hints)."""
    asked: list[str] = []

    async def ask_code(hint):
        asked.append(hint)
        return codes.pop(0) if codes else None

    async def main():
        settings = make_settings()
        async with async_playwright() as playwright:
            browser, _ = await launch_browser(playwright, settings)
            try:
                context = await browser.new_context(**context_options(settings, browser))
                page = await context.new_page()
                result = await login_atlassian(page, Credentials(EMAIL, password), ask_code,
                                               start_url=site.url + start_path, trusted_hosts={"127.0.0.1"},
                                               overall_timeout_s=60, **kwargs)
                state = await context.storage_state()
                return result, state
            finally:
                await browser.close()

    try:
        return asyncio.run(main()), asked
    except LoginError as exc:
        return exc, asked


@pytest.mark.browser
def test_full_login_succeeds_and_returns_session(site):
    (result, state), asked = run_login(site, [CODE])
    assert result.ok is True
    assert result.stages == ["email", "password", "code", "done"]
    assert asked == ["first"]
    assert any(c["name"] == "sid" for c in state["cookies"])


@pytest.mark.browser
def test_wrong_password_stops_without_asking_for_code(site):
    exc, asked = run_login(site, [CODE], password="wrong-password")
    assert isinstance(exc, LoginError) and exc.reason == "rejected" and exc.stage == "password"
    assert asked == []
    assert site.events.count("password") == 1          # exactly one attempt: no account lockout


@pytest.mark.browser
def test_wrong_code_once_asks_again_then_succeeds(site):
    (result, _), asked = run_login(site, ["000000", CODE])
    assert result.ok is True
    assert asked == ["first", "retry"]


@pytest.mark.browser
def test_wrong_code_twice_stops(site):
    exc, asked = run_login(site, ["000000", "111111"])
    assert isinstance(exc, LoginError) and exc.reason == "rejected" and exc.stage == "code"
    assert asked == ["first", "retry"]


@pytest.mark.browser
def test_no_code_in_time_stops_with_code_timeout(site):
    exc, asked = run_login(site, [])
    assert isinstance(exc, LoginError) and exc.reason == "code_timeout"


@pytest.mark.browser
def test_secrets_never_leak_to_output_or_errors(site, capsys):
    exc, _ = run_login(site, [], password=PASSWORD)
    out = capsys.readouterr()
    for secret in (PASSWORD, CODE, EMAIL):
        assert secret not in out.out and secret not in out.err
        assert secret not in str(exc)


@pytest.mark.browser
def test_credentials_are_not_typed_on_an_untrusted_host(site):
    exc, asked = run_login(site, [CODE], start_path="/to_other_host")
    assert isinstance(exc, LoginError) and exc.reason == "untrusted_host"
    assert site.events == []                            # nothing was ever submitted
    assert asked == []


# ---------- command line ----------

def test_cli_login_test_without_secrets_exits_2(monkeypatch, capsys):
    from web_reader.cli import main
    for name in ("ATLASSIAN_EMAIL", "ATLASSIAN_PASSWORD", "LOGIN_TELEGRAM_BOT_TOKEN", "LOGIN_TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    assert main(["login-test", "--start-url", "https://example.com/app"]) == 2
    err = capsys.readouterr().err
    assert "ATLASSIAN_EMAIL" in err


def test_cli_login_test_refuses_plain_http_without_allow_local(monkeypatch, capsys):
    from web_reader.cli import main
    for name, value in (("ATLASSIAN_EMAIL", EMAIL), ("ATLASSIAN_PASSWORD", PASSWORD),
                        ("LOGIN_TELEGRAM_BOT_TOKEN", "t"), ("LOGIN_TELEGRAM_CHAT_ID", "1")):
        monkeypatch.setenv(name, value)
    assert main(["login-test", "--start-url", "http://example.com/app"]) == 2
    assert "https" in capsys.readouterr().err


@pytest.mark.browser
def test_cli_login_test_end_to_end(site, telegram, monkeypatch, tmp_path, capsys):
    from web_reader.cli import main
    for name, value in (("ATLASSIAN_EMAIL", EMAIL), ("ATLASSIAN_PASSWORD", PASSWORD),
                        ("LOGIN_TELEGRAM_BOT_TOKEN", "TESTTOKEN"), ("LOGIN_TELEGRAM_CHAT_ID", str(CHAT)),
                        ("LOGIN_TELEGRAM_API_BASE", telegram.url)):
        monkeypatch.setenv(name, value)
    telegram.push_later(2.0, CHAT, f"code {CODE}")
    code = main(["login-test", "--start-url", site.url + "/app", "--allow-local", "--out", str(tmp_path),
                 "--code-timeout", "30"])
    out = capsys.readouterr()
    assert code == 0
    assert "OK" in out.out
    for secret in (PASSWORD, CODE, EMAIL):
        assert secret not in out.out and secret not in out.err

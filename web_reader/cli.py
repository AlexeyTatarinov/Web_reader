"""Командная строка: python -m web_reader check | read | version."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from . import __version__
from .check import run_checks
from .i18n import t
from .runner import parse_input_lines, run_urls
from .settings import SUPPORTED_LANGUAGES, load_settings


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="config/settings.json", help="файл настроек")
    parser.add_argument("--lang", choices=SUPPORTED_LANGUAGES, help="язык сообщений и отчётов (ru, en, uk)")
    parser.add_argument("--out", default="results", help="папка для результатов")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="web_reader", description="Полное чтение динамических веб-страниц")
    sub = parser.add_subparsers(dest="cmd")
    check = sub.add_parser("check", help="проверка браузера и соединений")
    _common(check)
    check.add_argument("--no-internet", action="store_true", help="не проверять выход в интернет")
    read = sub.add_parser("read", help="прочитать страницы")
    _common(read)
    read.add_argument("urls", nargs="*", help="адреса страниц")
    read.add_argument("--file", help="файл со списком адресов (по одному в строке)")
    read.add_argument("--skip-check", action="store_true", help="не запускать предварительную проверку")
    login = sub.add_parser("login-test", help="test the Atlassian login (email, password, emailed code via Telegram)")
    _common(login)
    login.add_argument("--start-url", required=True, help="page that requires login (https)")
    login.add_argument("--allow-local", action="store_true", help="allow http and local hosts (tests only)")
    login.add_argument("--code-timeout", type=int, default=300, help="seconds to wait for the code in Telegram")
    login.add_argument("--expect-text", help="text that must be on the page after login")
    login.add_argument("--save-session", help="write the logged-in session (cookies) to this file")
    tg = sub.add_parser("telegram-test", help="send a test message from the login bot to the owner's chat")
    _common(tg)
    cx = sub.add_parser("confluence-export", help="export a Confluence branch to Markdown files (read-only, REST API)")
    _common(cx)
    cx.add_argument("--root-id", required=True, help="id of the root page of the branch")
    cx.add_argument("--site", default=os.environ.get("CONFLUENCE_SITE", "gr8-tech.atlassian.net"),
                    help="Confluence Cloud site, e.g. example.atlassian.net")
    cx.add_argument("--max-pages", type=int, default=3000, help="safety cap on the number of pages")
    cx.add_argument("--delay", type=float, default=0.3, help="pause between requests, seconds")
    cx.add_argument("--zip", action="store_true", help="also write export.zip")
    cx.add_argument("--cloud-id", help="Atlassian cloud id (needed only for tokens created with scopes; found automatically)")
    cx.add_argument("--api-gateway", default="https://api.atlassian.com", help=argparse.SUPPRESS)
    cx.add_argument("--allow-local", action="store_true", help="allow http and local hosts (tests only)")
    sub.add_parser("version", help="версия программы")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd in (None, "version"):
        print(f"web_reader {__version__}")
        return 0
    try:
        settings = load_settings(args.config)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    lang = args.lang or settings.language
    out_dir = Path(args.out)

    if args.cmd == "confluence-export":
        return _confluence_export(args, lang, out_dir)

    if args.cmd == "telegram-test":
        return _telegram_test(lang)

    if args.cmd == "login-test":
        return _login_test(args, settings, lang, out_dir)

    if args.cmd == "check":
        ok, _ = asyncio.run(run_checks(settings, lang, out_dir, check_internet=not args.no_internet))
        return 0 if ok else 1

    # read
    lines: list[str] = list(args.urls)
    if args.file:
        try:
            lines += Path(args.file).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            print(f"{args.file}: {exc}", file=sys.stderr)
            return 2
    items, problems = parse_input_lines(lines, lang)
    for problem in problems:
        print(problem, file=sys.stderr)
    if not items:
        print(t(lang, "no_urls"), file=sys.stderr)
        return 2
    if not args.skip_check:
        ok, _ = asyncio.run(run_checks(settings, lang, out_dir))
        if not ok:
            return 1
    summary = asyncio.run(run_urls(items, settings, out_dir, lang, input_problems=problems))
    return 0 if summary.results else 1


_LOGIN_ENV = ("ATLASSIAN_EMAIL", "ATLASSIAN_PASSWORD", "LOGIN_TELEGRAM_BOT_TOKEN", "LOGIN_TELEGRAM_CHAT_ID")


def _login_test(args: argparse.Namespace, settings, lang: str, out_dir: Path) -> int:
    """Logs in once and reports the result (also to Telegram). Nothing from the site is saved."""
    from playwright.async_api import async_playwright

    from .browser import context_options, launch_browser
    from .login import Credentials, LoginError, TelegramPrompter, login_atlassian

    missing = [name for name in _LOGIN_ENV if not os.environ.get(name)]
    if missing:
        print(t(lang, "login_missing_env", names=", ".join(missing)), file=sys.stderr)
        return 2
    if not args.start_url.lower().startswith("https://") and not args.allow_local:
        print(t(lang, "login_need_https"), file=sys.stderr)
        return 2
    prompter = TelegramPrompter(os.environ["LOGIN_TELEGRAM_BOT_TOKEN"], os.environ["LOGIN_TELEGRAM_CHAT_ID"], lang=lang,
                                api_base=os.environ.get("LOGIN_TELEGRAM_API_BASE", "https://api.telegram.org"))
    creds = Credentials(os.environ["ATLASSIAN_EMAIL"], os.environ["ATLASSIAN_PASSWORD"])
    try:  # fail fast: do not touch Atlassian when the owner cannot be reached in Telegram
        prompter.check(t(lang, "telegram_test_message"))
    except LoginError as exc:
        print(t(lang, "telegram_test_failed", reason=str(exc)), file=sys.stderr)
        return 1

    async def ask_code(hint: str) -> str | None:
        return await asyncio.to_thread(prompter.request_code, args.code_timeout, hint)

    async def run() -> str:
        async with async_playwright() as playwright:
            browser, _ = await launch_browser(playwright, settings)
            try:
                context = await browser.new_context(**context_options(settings, browser))
                page = await context.new_page()
                try:
                    result = await login_atlassian(page, creds, ask_code, start_url=args.start_url,
                                                   expect_text=args.expect_text, log=print)
                except LoginError:
                    target = out_dir / "login"
                    target.mkdir(parents=True, exist_ok=True)
                    try:
                        await page.screenshot(path=str(target / "failure.png"))
                    except Exception:
                        pass
                    raise
                if args.save_session:
                    await context.storage_state(path=args.save_session)
                return " -> ".join(result.stages)
            finally:
                await browser.close()

    try:
        stages = asyncio.run(run())
    except LoginError as exc:
        message = t(lang, "login_failed", reason=str(exc))
        print(message, file=sys.stderr)
        prompter.notify(message)
        return 1
    message = t(lang, "login_ok", stages=stages)
    print(message)
    prompter.notify(message)
    return 0


def _telegram_test(lang: str) -> int:
    from .login import LoginError, TelegramPrompter

    missing = [n for n in ("LOGIN_TELEGRAM_BOT_TOKEN", "LOGIN_TELEGRAM_CHAT_ID") if not os.environ.get(n)]
    if missing:
        print(t(lang, "login_missing_env", names=", ".join(missing)), file=sys.stderr)
        return 2
    prompter = TelegramPrompter(os.environ["LOGIN_TELEGRAM_BOT_TOKEN"], os.environ["LOGIN_TELEGRAM_CHAT_ID"], lang=lang,
                                api_base=os.environ.get("LOGIN_TELEGRAM_API_BASE", "https://api.telegram.org"))
    try:
        username = prompter.check(t(lang, "telegram_test_message"))
    except LoginError as exc:
        print(t(lang, "telegram_test_failed", reason=str(exc)), file=sys.stderr)
        return 1
    print(t(lang, "telegram_test_ok", username=username))
    return 0


def _confluence_export(args: argparse.Namespace, lang: str, out_dir: Path) -> int:
    from .confluence_export import ConfluenceClient, ExportError, export_branch

    missing = [n for n in ("CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN") if not os.environ.get(n)]
    if missing:
        print(t(lang, "login_missing_env", names=", ".join(missing)), file=sys.stderr)
        return 2
    site = args.site.strip()
    if site.startswith("http"):
        base = site
        host = site.split("//", 1)[1].split("/", 1)[0].split(":")[0]
    else:
        base, host = f"https://{site}", site.split("/", 1)[0].split(":")[0]
    if not args.allow_local and not host.lower().endswith(".atlassian.net"):
        print(t(lang, "cx_need_atlassian"), file=sys.stderr)
        return 2
    from .confluence_export import discover_cloud_id
    cloud_id = args.cloud_id or (None if args.allow_local else discover_cloud_id(host))
    gateway = f"{args.api_gateway.rstrip('/')}/ex/confluence/{cloud_id}" if cloud_id else None
    client = ConfluenceClient(base, os.environ["CONFLUENCE_EMAIL"], os.environ["CONFLUENCE_API_TOKEN"],
                              delay_s=args.delay, gateway_base=gateway)
    try:
        summary = export_branch(client, args.root_id, out_dir, max_pages=args.max_pages, make_zip=args.zip, log=print)
    except ExportError as exc:
        print(t(lang, "cx_failed", reason=str(exc)), file=sys.stderr)
        return 1
    print(t(lang, "cx_done", pages=summary["pages"], path=str(out_dir)))
    for problem in summary["errors"][:20]:
        print(f"note: {problem}")
    return 0

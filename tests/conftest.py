from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.async_api import async_playwright  # noqa: E402

from testsite import start_server  # noqa: E402,F401
from web_reader.browser import context_options, launch_browser  # noqa: E402
from web_reader.budget import RunBudget  # noqa: E402
from web_reader.reader import RunContext, read_page  # noqa: E402
from web_reader.settings import load_settings  # noqa: E402


@pytest.fixture(scope="session")
def base_url():
    server, url = start_server()
    yield url
    server.shutdown()


def make_settings(**sections):
    """Настройки для тестов: локальные адреса разрешены, пауз между страницами нет."""
    overrides = {"safety": {"allow_private_hosts": True}, "run": {"delay_between_pages_s": 0},
                 "output": {"screenshots": "never"}}
    for name, values in sections.items():
        overrides.setdefault(name, {}).update(values)
    return load_settings(None, overrides)


async def _with_context(settings, run_dir, fn):
    async with async_playwright() as playwright:
        browser, _ = await launch_browser(playwright, settings)
        try:
            rc = RunContext(settings=settings, browser=browser,
                            budget=RunBudget(settings.run.max_pages_per_run, settings.run.max_run_seconds,
                                             settings.run.max_probes_per_run),
                            context_options=context_options(settings, browser), run_dir=run_dir)
            return await fn(rc)
        finally:
            await browser.close()


def read_one(url, settings=None, mode="full", run_dir=None):
    settings = settings or make_settings()
    return asyncio.run(_with_context(settings, run_dir, lambda rc: read_page(rc, url, mode=mode)))

"""Тесты в настоящем браузере на локальном тестовом сайте (tests/testsite.py)."""
from __future__ import annotations

import asyncio
import json

import pytest
from playwright.async_api import async_playwright

from conftest import make_settings, read_one
from web_reader.browser import launch_browser
from web_reader.runner import InputItem, run_urls

pytestmark = pytest.mark.browser


def text_of(res):
    return res.full_text()


def link_urls(res):
    return {l["url"] for l in res.links}


# ---------------- JavaScript, динамика

def test_javascript_rendered_content_is_read(base_url):
    res = read_one(base_url + "/dynamic")
    assert "Senior QA Engineer" in text_of(res)
    assert any(l["url"].endswith("/jobs/qa-engineer-1") and l["class"] == "job_internal" for l in res.links)
    assert res.status == "ok"
    assert res.jobs["counts"]["links"] == 2


def test_load_more_button_loads_everything(base_url):
    res = read_one(base_url + "/loadmore")
    assert res.jobs["counts"]["links"] == 15
    assert res.jobs["counts"]["network"] == 15          # те же вакансии из перехваченного JSON
    assert res.expansion["expansions"] >= 2
    assert res.status == "ok", (res.limits_hit, res.warnings)


def test_infinite_scroll_reads_all_when_limits_allow(base_url):
    res = read_one(base_url + "/infinite")
    assert res.jobs["counts"]["links"] == 60, res.expansion
    assert res.status == "ok", (res.limits_hit, res.warnings)


def test_infinite_scroll_cut_by_scroll_limit_is_reported(base_url):
    res = read_one(base_url + "/infinite", make_settings(limits={"max_scrolls": 2}))
    assert res.jobs["counts"]["links"] < 60
    assert any(h["limit"] == "max_scrolls" for h in res.limits_hit)
    assert res.status == "partial"


def test_job_limit_stops_reading_and_is_reported(base_url):
    res = read_one(base_url + "/infinite", make_settings(limits={"max_jobs_per_page": 20}))
    assert any(h["limit"] == "max_jobs_per_page" for h in res.limits_hit)
    assert len(res.jobs["links"]) <= 20
    assert res.status == "partial"


# ---------------- виджеты, iframe, shadow DOM

def test_iframe_content_is_read_with_source_label(base_url):
    res = read_one(base_url + "/iframe")
    assert "Embedded Compliance Manager" in text_of(res)
    frame = next(f for f in res.frames if f["url"].endswith("/embed"))
    assert frame["read"] and frame["kind"] == "other"
    assert any(s["source"].startswith("frame:") for s in res.sections)
    assert any(l["url"].endswith("/jobs/embedded-9") for l in res.links)


def test_shadow_dom_text_and_links(base_url):
    res = read_one(base_url + "/shadow")
    assert "Shadow Tester Role" in text_of(res)
    assert any(l["url"].endswith("/jobs/shadow-1") for l in res.links)


def test_tabs_accordions_and_details_are_all_collected(base_url):
    res = read_one(base_url + "/tabs")
    text = text_of(res)
    assert "Engineering roles" in text and "Design roles" in text      # вкладка меняет содержимое, оба текста сохранены
    assert "Benefits: health insurance" in text                        # аккордеон
    assert "We support relocation within the EU" in text               # закрытый <details>
    assert res.expansion["expansions"] >= 3


# ---------------- попапы и баннеры

def test_cookie_banner_is_rejected_never_accepted(base_url):
    res = read_one(base_url + "/cookie")
    assert any("Reject all" in d for d in res.popups["dismissed"])
    assert "not accepted" in text_of(res)
    assert "ACCEPTED" not in text_of(res)
    assert "We use cookies" not in text_of(res)


def test_modal_popup_is_closed(base_url):
    res = read_one(base_url + "/popup")
    assert any(d.startswith("popup") for d in res.popups["dismissed"])
    assert "Subscribe to our newsletter" not in text_of(res)
    assert not res.popups["obstructions"]


# ---------------- данные, которых нет в видимом тексте

def test_jobs_found_in_network_json_even_if_page_renders_nothing(base_url):
    res = read_one(base_url + "/jsononly")
    assert res.jobs["counts"]["links"] == 0
    assert res.jobs["counts"]["network"] == 3
    assert not any(w["code"] == "w_declared_gt_collected" for w in res.warnings)
    assert res.status == "ok"


def test_declared_count_mismatch_makes_page_partial(base_url):
    res = read_one(base_url + "/jsononly_short")
    assert any(w["code"] == "w_declared_gt_collected" and w["declared"] == 40 for w in res.warnings)
    assert res.status == "partial"


# ---------------- защита от бесконечности и «плохих» страниц

def test_endless_requests_are_capped(base_url):
    settings = make_settings(limits={"max_requests_per_page": 60, "page_timeout_s": 10})
    res = read_one(base_url + "/spam", settings)
    assert res.seconds < 25
    assert any(h["limit"] in ("max_requests_per_page", "timeout", "max_scrolls") for h in res.limits_hit), res.limits_hit
    assert res.status == "partial"
    assert "tick 1 " in text_of(res)       # то, что успели прочитать, сохранено


def test_hanging_page_is_cut_by_timeout_and_keeps_partial_text(base_url):
    settings = make_settings(limits={"page_timeout_s": 4, "hard_timeout_grace_s": 6})
    res = read_one(base_url + "/hang", settings)
    assert res.seconds < 14
    assert res.limits_hit, "ожидалась пометка о сработавшем лимите"
    assert res.status in ("partial", "empty", "error")


def test_bot_challenge_is_reported_not_bypassed(base_url):
    res = read_one(base_url + "/blocked")
    assert res.status == "blocked" and res.antibot
    assert res.kind == "blocked"


def test_403_is_reported(base_url):
    res = read_one(base_url + "/forbidden")
    assert res.status == "blocked" and res.http_status == 403


def test_404_page_kind(base_url):
    res = read_one(base_url + "/missing")
    assert res.http_status == 404 and res.kind == "not_found"


def test_closed_posting_is_recognised(base_url):
    res = read_one(base_url + "/closed")
    assert res.kind == "closed_posting"
    assert any(w["code"] == "w_closed_posting" for w in res.warnings)


def test_private_start_address_is_refused_without_permission(base_url):
    settings = make_settings(safety={"allow_private_hosts": False})
    res = read_one(base_url + "/dynamic", settings)
    assert res.status == "skipped" and "forbidden" in res.skip_reason


def test_light_mode_reads_title_and_jsonld_without_expansion(base_url):
    res = read_one(base_url + "/jobs/qa-tester-7", mode="light")
    assert res.kind == "job_posting"
    assert res.expansion["expansions"] == 0 and res.expansion["scrolls"] == 0
    assert res.jobs["jsonld"][0]["valid_through"] == "2026-12-31"


def test_screenshot_is_taken_on_problem(base_url, tmp_path):
    settings = make_settings(output={"screenshots": "on_problem"})
    res = read_one(base_url + "/blocked", settings, run_dir=tmp_path)
    assert res.screenshot and (tmp_path / res.screenshot).exists()


def test_unreachable_host_is_error_not_crash():
    res = read_one("http://127.0.0.1:9/nothing", make_settings(limits={"page_timeout_s": 8}))
    assert res.status == "error" and res.error


# ---------------- полный запуск: ссылки, глубина, PDF, пагинация, отчёты

def test_full_run_follows_links_one_level_and_writes_reports(base_url, tmp_path):
    settings = make_settings()
    summary = asyncio.run(run_urls([InputItem(base_url + "/listing")], settings, tmp_path, "ru", log=lambda m: None))
    top = summary.results[0]
    assert top.status == "ok", (top.limits_hit, top.warnings)
    assert top.jobs["chain_total"] == 3                  # заявлено «3 jobs»: 2 на первой странице + 1 на второй

    decisions = {l["url"]: l.get("decision") for l in top.links}
    by_class = {}
    for l in top.links:
        by_class.setdefault(l["class"], []).append(l)

    # вакансии: прочитаны с переходом
    job_children = [c for c in top.children if c.relation == "job_internal"]
    assert {c.final_url.rsplit("/", 1)[-1] for c in job_children} >= {"qa-a-1", "lqa-b-2"}
    assert all(c.kind == "job_posting" and c.mode == "light" for c in job_children)

    # PDF: текст извлечён
    pdf = next(c for c in top.children if c.relation == "pdf")
    assert "Job description QA Tester" in pdf.pdf["text"]

    # пагинация: вторая страница прочитана полностью и добавила вакансию
    page2 = next(c for c in top.children if c.relation == "pagination")
    assert page2.mode == "full" and page2.jobs["counts"]["links"] == 1
    assert any(c.final_url.endswith("/jobs/compliance-c-3") for c in page2.children)

    # решения по остальным ссылкам записаны, ничего не потеряно
    assert by_class["external"][0]["decision"] == "d_skip_external"
    assert by_class["social"][0]["decision"] == "d_skip_social"
    assert by_class["mailto"][0]["decision"] == "d_mail"
    assert by_class["anchor"][0]["decision"] == "d_anchor"
    assert by_class["javascript"][0]["decision"] == "d_skip_type"
    about = by_class["internal"][0]
    assert about["decision"] == "d_probe" and "About us" in about["where"]
    assert all(l.get("decision") for l in top.links), [l for l in top.links if not l.get("decision")]

    # отчёты
    index = (summary.run_dir / "index.md").read_text(encoding="utf-8")
    assert "/listing" in index
    md = (summary.run_dir / f"{summary.stems[0]}.md").read_text(encoding="utf-8")
    assert "QA Engineer A" in md and "Job description QA Tester" in md and "d_" not in md
    data = json.loads((summary.run_dir / f"{summary.stems[0]}.json").read_text(encoding="utf-8"))
    assert data["children"] and data["links"]
    assert (tmp_path / "LATEST.txt").read_text().strip() == summary.run_id


def test_run_budget_marks_unprocessed_addresses(base_url, tmp_path):
    settings = make_settings(run={"max_pages_per_run": 1, "max_depth": 0})
    summary = asyncio.run(run_urls([InputItem(base_url + "/dynamic"), InputItem(base_url + "/about")],
                                   settings, tmp_path, "en", log=lambda m: None))
    assert summary.results[0].status == "ok"
    assert summary.results[1].status == "skipped" and summary.results[1].skip_reason == "run_page_budget"
    index = (summary.run_dir / "index.md").read_text(encoding="utf-8")
    assert "run_page_budget" in index


def test_depth_zero_does_not_follow(base_url, tmp_path):
    settings = make_settings(run={"max_depth": 0, "max_pagination_pages": 0})
    summary = asyncio.run(run_urls([InputItem(base_url + "/listing")], settings, tmp_path, "uk", log=lambda m: None))
    assert summary.results[0].children == []


def test_browser_launches_with_default_settings():
    async def go():
        async with async_playwright() as p:
            browser, label = await launch_browser(p, make_settings())
            await browser.close()
            return label
    assert asyncio.run(go())


def test_pagination_limit_is_reported_and_site_hint_is_in_index(base_url, tmp_path):
    settings = make_settings(run={"max_pagination_pages": 0})
    summary = asyncio.run(run_urls([InputItem(base_url + "/listing")], settings, tmp_path, "ru", log=lambda m: None))
    top = summary.results[0]
    assert any(h["limit"] == "max_pagination_pages" for h in top.limits_hit) and top.status == "partial"
    index = (summary.run_dir / "index.md").read_text(encoding="utf-8")
    assert "site_overrides" in index and "max_pagination_pages" in index


def test_site_override_raises_limit_for_that_site_only(base_url, tmp_path):
    settings = make_settings(run={"max_pagination_pages": 0},
                             site_overrides={"127.0.0.1": {"run": {"max_pagination_pages": 3}}})
    summary = asyncio.run(run_urls([InputItem(base_url + "/listing")], settings, tmp_path, "ru", log=lambda m: None))
    top = summary.results[0]
    assert any(c.relation == "pagination" for c in top.children)
    assert not any(h["limit"] == "max_pagination_pages" for h in top.limits_hit)

"""Быстрые тесты без браузера."""
from __future__ import annotations

import pytest

from web_reader import antibot, i18n
from web_reader.classify import classify_frame, classify_link, is_pagination_link, looks_like_job_url, same_site
from web_reader.fetch import read_pdf
from web_reader.jobs import declared_counts, extract_job_items, parse_jsonld
from web_reader.runner import parse_input_lines
from web_reader.safety import check_url, normalize_url, private_reason, url_key
from web_reader.settings import load_settings
from testsite import make_pdf


def settings(**over):
    return load_settings(None, over or None)


# ---------------- настройки

def test_defaults_match_agreed_limits():
    s = settings()
    assert s.limits.page_timeout_s == 30
    assert s.limits.max_scrolls == 15
    assert s.limits.max_expansions == 25
    assert s.limits.max_jobs_per_page == 100
    assert s.language == "ru"


def test_ceilings_and_bad_values_are_clamped_with_warnings():
    s = settings(limits={"page_timeout_s": 99999, "max_scrolls": "many", "max_expansions": -5}, typo_key=1)
    assert s.limits.page_timeout_s == 120
    assert s.limits.max_scrolls == 15          # нечисловое значение отброшено
    assert s.limits.max_expansions == 1        # минимум
    assert any("typo_key" in w for w in s.warnings)
    assert any("потолк" in w for w in s.warnings)


def test_unknown_language_falls_back():
    assert settings(language="xx").language == "ru"


# ---------------- безопасность адресов

@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254",
                                  "[::1]", "2130706433", "0x7f.0.0.1", "intranet.local"])
def test_private_hosts_are_detected(host):
    assert private_reason(host)


def test_public_hosts_pass():
    assert private_reason("example.com") is None
    assert private_reason("8.8.8.8") is None


def test_check_url_blocks_private_and_blocked_domains():
    import asyncio
    s = settings()
    assert not asyncio.run(check_url("http://127.0.0.1:8000/x", s.safety))[0]
    assert not asyncio.run(check_url("file:///etc/passwd", s.safety))[0]
    assert not asyncio.run(check_url("https://stats.doubleclick.net/x", s.safety))[0]
    assert asyncio.run(check_url("http://127.0.0.1:8000/x", settings(safety={"allow_private_hosts": True}).safety))[0]


def test_normalize_and_url_key():
    assert normalize_url("HTTPS://Example.com:443/a?utm_source=x&id=1#frag") == "https://example.com/a?id=1"
    assert url_key("https://example.com/a/") == url_key("https://example.com/a#x")
    assert normalize_url("ftp://example.com") is None
    assert normalize_url("/relative", "https://example.com/base") == "https://example.com/relative"


# ---------------- ссылки

def test_link_classes():
    s = settings()
    page = "https://acme.com/careers"
    assert classify_link("/careers/qa-engineer-12", page, s) == "job_internal"
    assert classify_link("/careers", page, s) == "internal"
    assert classify_link("https://boards.greenhouse.io/acme", page, s) == "ats_board"
    assert classify_link("https://boards.greenhouse.io/acme/jobs/123", page, s) == "ats_job"
    assert classify_link("https://jobs.lever.co/acme/8a1b-22", page, s) == "ats_job"
    assert classify_link("/files/offer.pdf", page, s) == "pdf"
    assert classify_link("/files/deck.pptx", page, s) == "document"
    assert classify_link("https://www.linkedin.com/company/acme", page, s) == "social"
    assert classify_link("mailto:a@b.c", page, s) == "mailto"
    assert classify_link("#top", page, s) == "anchor"
    assert classify_link("javascript:void(0)", page, s) == "javascript"
    assert classify_link("https://other.org/x", page, s) == "external"
    assert classify_link("/careers?page=2", page, s, text="Next") == "pagination"
    assert classify_link("/careers?gh_jid=44", page, s) == "job_internal"


def test_pagination_and_job_url_heuristics():
    assert is_pagination_link("Next", "", "", "/jobs?page=2", "https://a.com/jobs")
    assert is_pagination_link("2", "next", "", "/jobs?page=2", "https://a.com/jobs")
    assert not is_pagination_link("Next", "", "", "https://b.com/jobs?page=2", "https://a.com/jobs")
    assert not looks_like_job_url("https://a.com/careers")
    assert looks_like_job_url("https://a.com/careers/12345")
    assert same_site("https://jobs.acme.co.uk/x", "https://www.acme.co.uk/y")
    assert not same_site("https://acme.com", "https://acme.org")


def test_frame_classes():
    s = settings()
    assert classify_frame("https://boards.greenhouse.io/embed/job_board?for=acme", s) == "ats"
    assert classify_frame("https://www.youtube.com/embed/abc", s) == "video"
    assert classify_frame("https://docs.google.com/presentation/d/1/embed", s) == "presentation"
    assert classify_frame("https://www.google.com/maps/embed?pb=1", s) == "map"
    assert classify_frame("https://googleads.g.doubleclick.net/x", s) == "ads"
    assert classify_frame("about:blank", s) == "inline"
    assert classify_frame("https://unknown.example/widget", s) == "other"


# ---------------- вакансии в данных

def test_extract_jobs_from_nested_json():
    data = {"departments": [{"name": "Eng", "jobs": [
        {"title": "QA Engineer", "absolute_url": "https://x.io/1", "location": {"name": "Valencia"}},
        {"title": "LQA Tester (RU)", "absolute_url": "https://x.io/2", "location": {"name": "Remote"}},
        {"title": "Compliance Manager", "absolute_url": "https://x.io/3", "location": {"name": "Madrid"}}]}]}
    items, total = extract_job_items(data)
    assert total == 3 and items[0]["location"] == "Valencia"


def test_site_menu_is_not_a_job_list():
    items, total = extract_job_items({"menu": [{"name": "Home", "url": "/"}, {"name": "About", "url": "/about"}]})
    assert total == 0


def test_job_item_cap_keeps_total():
    data = [{"title": f"Job number {i}", "id": i, "location": "X"} for i in range(250)]
    items, total = extract_job_items(data, max_items=100)
    assert len(items) == 100 and total == 250


def test_jsonld_job_posting():
    block = ('{"@context":"https://schema.org","@graph":[{"@type":"JobPosting","title":"QA Tester",'
             '"validThrough":"2026-12-31","jobLocation":{"address":{"addressLocality":"Valencia"}},'
             '"hiringOrganization":{"name":"Acme"}}]}')
    jobs = parse_jsonld([block])
    assert jobs[0]["title"] == "QA Tester" and jobs[0]["location"] == "Valencia" and jobs[0]["valid_through"] == "2026-12-31"


def test_declared_counts():
    assert declared_counts("Showing 1-20 of 134 results")["value"] == 134
    assert declared_counts("Showing 1-20 of 134 results")["confidence"] == "high"
    assert declared_counts("We have 27 open positions")["value"] == 27
    assert declared_counts("Jobs (1,204)")["value"] == 1204
    assert declared_counts("Mostrando 1-10 de 56 ofertas")["value"] == 56
    assert declared_counts("Nothing about counts")["value"] is None


# ---------------- защита от ботов

def test_antibot_detection():
    assert antibot.detect("Just a moment...", "", 200, {}) == "challenge_title"
    assert antibot.detect("Site", "Please verify you are human", 200, {})
    assert antibot.detect("Site", "x" * 50, 403, {})
    assert antibot.detect("Site", "x", 200, {"CF-Mitigated": "challenge"}) == "cloudflare_challenge"
    long_normal = "Our jobs " * 400 + " we use reCAPTCHA on forms"
    assert antibot.detect("Careers", long_normal, 200, {}) is None


# ---------------- ввод и тексты

def test_parse_input_lines():
    items, problems = parse_input_lines(
        ["# comment", "", "https://a.com/jobs | my note", "https://a.com/jobs/", "not a url", "ftp://x.org"], "ru")
    assert [i.url for i in items] == ["https://a.com/jobs"]
    assert items[0].note == "my note"
    assert len(problems) == 2


def test_all_languages_have_all_texts():
    for key in i18n.all_keys():
        for lang in ("ru", "en", "uk"):
            assert i18n.t(lang, key), (key, lang)


def test_unknown_i18n_key_returns_key():
    assert i18n.t("ru", "no_such_key") == "no_such_key"


# ---------------- PDF

def test_pdf_blocked_for_private_hosts_by_default():
    s = settings()
    info = read_pdf("http://127.0.0.1:9/x.pdf", s.safety, max_bytes=1000, max_pages=2)
    assert not info["ok"] and "blocked" in info["error"]


def test_make_pdf_is_readable():
    import io
    from pypdf import PdfReader
    text = PdfReader(io.BytesIO(make_pdf("Hello QA"))).pages[0].extract_text()
    assert "Hello QA" in text

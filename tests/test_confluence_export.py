"""Tests for the Confluence branch export; written before the implementation (docs/confluence-export-spec.md)."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_confluence import FakeConfluence  # noqa: E402
from web_reader.confluence_export import ConfluenceClient, ExportError, export_branch, slugify  # noqa: E402

EMAIL, TOKEN = "o@example.com", "TOKEN-123"


@pytest.fixture()
def server():
    s = FakeConfluence(EMAIL, TOKEN)
    yield s
    s.shutdown()


def client(server, token=TOKEN, gateway=False):
    return ConfluenceClient(server.url, EMAIL, token, delay_s=0, sleep=lambda _s: None,
                            gateway_base=server.url + "/ex/confluence/abc" if gateway else None)


def run(server, tmp_path, **kwargs):
    return export_branch(client(server), "100", tmp_path, page_size=3, **kwargs)


def test_slugify():
    assert slugify("Casino Guide") == "casino-guide"
    assert slugify("  MMS (Mirror Management System) User Guide ") == "mms-mirror-management-system-user-guide"
    assert slugify("Орієнтація №1") == "орієнтація-1"
    assert slugify("///") == "page"
    assert len(slugify("a" * 300)) <= 80


def test_layout_follows_the_hierarchy_and_excludes_pages_outside(server, tmp_path):
    summary = run(server, tmp_path)
    base = tmp_path / "user-guides"
    assert (base / "index.md").exists()
    assert (base / "casino-guide" / "index.md").exists()          # has a child
    assert (base / "casino-guide" / "setup.md").exists()
    assert (base / "other-guide" / "setup.md").exists()
    assert (base / "орієнтація.md").exists()
    assert not list(tmp_path.rglob("*outside*"))
    assert summary["pages"] == 8                                   # root + 7 descendants, not the outside page


def test_markdown_keeps_headings_lists_tables_and_links(server, tmp_path):
    run(server, tmp_path)
    text = (tmp_path / "user-guides" / "casino-guide" / "index.md").read_text(encoding="utf-8")
    assert text.startswith("---\n") and 'title: "Casino Guide"' in text and "confluence_id: 101" in text
    assert "## Intro" in text and "- a" in text and "| A | B |" in text
    assert "[outside](https://example.org/x)" in text


def test_links_between_exported_pages_become_relative(server, tmp_path):
    run(server, tmp_path)
    text = (tmp_path / "user-guides" / "casino-guide" / "index.md").read_text(encoding="utf-8")
    assert "[setup](setup.md)" in text


def test_duplicate_titles_in_one_folder_do_not_overwrite(server, tmp_path):
    run(server, tmp_path)
    base = tmp_path / "user-guides"
    assert (base / "dup.md").read_text(encoding="utf-8").count("first") == 1
    assert (base / "dup-107.md").read_text(encoding="utf-8").count("second") == 1


def test_index_manifest_and_zip(server, tmp_path):
    summary = run(server, tmp_path, make_zip=True)
    assert "Casino Guide" in (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "_export.json").read_text(encoding="utf-8"))
    assert manifest["pages"] == 8 and manifest["root_id"] == "100" and manifest["errors"] == []
    with zipfile.ZipFile(summary["zip"]) as archive:
        assert "user-guides/index.md" in archive.namelist()


def test_wrong_token_gives_a_clear_error_without_leaking_it(server, tmp_path):
    with pytest.raises(ExportError) as info:
        export_branch(client(server, token="WRONG-TOKEN"), "100", tmp_path)
    assert "401" in str(info.value) and "WRONG-TOKEN" not in str(info.value)


def test_correct_auth_header_is_sent_and_secrets_stay_out_of_files(server, tmp_path):
    run(server, tmp_path)
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.suffix in (".md", ".json"):
            assert TOKEN not in path.read_text(encoding="utf-8")


def test_429_is_retried(server, tmp_path):
    server.fail_429_once = True
    assert run(server, tmp_path)["pages"] == 8


def test_foreign_pagination_host_is_refused(server, tmp_path):
    server.foreign_next = True
    with pytest.raises(ExportError) as info:
        run(server, tmp_path)
    assert "host" in str(info.value)


def test_branch_bigger_than_cap_stops_instead_of_partial_export(server, tmp_path):
    with pytest.raises(ExportError) as info:
        run(server, tmp_path, max_pages=4)
    assert "more than 4" in str(info.value)


def test_only_get_requests_are_made(server, tmp_path):
    run(server, tmp_path)
    assert server.requests and all(r.startswith("/wiki/rest/api/content") for r in server.requests)


# ---------- command line ----------

def test_cli_missing_secrets_exits_2(monkeypatch, capsys):
    from web_reader.cli import main
    monkeypatch.delenv("CONFLUENCE_EMAIL", raising=False)
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    assert main(["confluence-export", "--root-id", "100"]) == 2
    assert "CONFLUENCE_EMAIL" in capsys.readouterr().err


def test_cli_refuses_non_atlassian_host(monkeypatch, capsys):
    from web_reader.cli import main
    monkeypatch.setenv("CONFLUENCE_EMAIL", EMAIL)
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", TOKEN)
    assert main(["confluence-export", "--site", "evil.example.com", "--root-id", "100"]) == 2
    assert "atlassian.net" in capsys.readouterr().err


def test_cli_end_to_end(server, monkeypatch, tmp_path, capsys):
    from web_reader.cli import main
    monkeypatch.setenv("CONFLUENCE_EMAIL", EMAIL)
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", TOKEN)
    code = main(["confluence-export", "--site", server.url, "--allow-local", "--root-id", "100",
                 "--out", str(tmp_path / "out"), "--zip", "--delay", "0"])
    out = capsys.readouterr()
    assert code == 0 and (tmp_path / "out" / "user-guides" / "index.md").exists()
    assert TOKEN not in out.out + out.err
    assert (tmp_path / "out" / "export.zip").exists()


# ---------- scoped API tokens (the site URL refuses them, the api.atlassian.com gateway accepts them) ----------

def test_scoped_token_falls_back_to_the_gateway_and_keeps_site_urls(server, tmp_path):
    server.scoped_only = True
    summary = export_branch(client(server, gateway=True), "100", tmp_path, page_size=3)
    assert summary["pages"] == 8
    text = (tmp_path / "user-guides" / "casino-guide" / "index.md").read_text(encoding="utf-8")
    assert f"url: {server.url}/wiki/spaces/GD/pages/101/x" in text          # site URL, not the gateway one
    assert any(r.startswith("/ex/confluence/abc/wiki/rest/api/content/search") for r in server.requests)
    assert any(r.startswith("/ex/confluence/abc/wiki/rest/api/content/search") and "cursor=" in r for r in server.requests)


def test_scoped_token_without_gateway_gives_a_helpful_error(server, tmp_path):
    server.scoped_only = True
    with pytest.raises(ExportError) as info:
        export_branch(client(server), "100", tmp_path)
    message = str(info.value)
    assert "403" in message and "scope" in message and TOKEN not in message
    assert "FAILURE_CLIENT_AUTH_MISMATCH" in message
    assert "Forbidden" in message                              # the (non-secret) answer body is reported


def test_cloud_id_discovery_reads_tenant_info(server):
    from web_reader.confluence_export import discover_cloud_id
    assert discover_cloud_id(server.url) == "abc"
    assert discover_cloud_id("http://127.0.0.1:9") is None


def test_cli_uses_cloud_id_for_scoped_tokens(server, monkeypatch, tmp_path, capsys):
    from web_reader.cli import main
    server.scoped_only = True
    monkeypatch.setenv("CONFLUENCE_EMAIL", EMAIL)
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", TOKEN)
    code = main(["confluence-export", "--site", server.url, "--allow-local", "--root-id", "100",
                 "--out", str(tmp_path / "out"), "--delay", "0", "--cloud-id", "abc",
                 "--api-gateway", server.url])
    assert code == 0, capsys.readouterr().err


def test_requests_carry_a_descriptive_user_agent(server, tmp_path):
    run(server, tmp_path)
    assert server.user_agents and all(ua.startswith("web_reader/") for ua in server.user_agents)

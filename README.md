# web_reader

A program for fully reading dynamic web pages. It opens a page in a real headless browser (Playwright), expands hidden content, reads embedded frames, intercepts the data the page loads by itself, parses links, and saves a report with honest notes on what was read completely and what was not.

There is **no AI inside the program**: it only reads and describes. Conclusions are drawn by whoever reads the report (a person or an AI assistant). It works without Claude and without any paid service: the browser runs on your machine or in GitHub Actions.

## What it does

| Situation on the page | What the program does |
|---|---|
| Content is built by JavaScript | Waits for loading and reads what is actually visible |
| "Show more", tabs, accordions, collapsed blocks | Expands them one by one and saves what it sees after each click |
| Infinite scroll, lists inside scrollable blocks | Scrolls while new content appears (with hard limits) |
| Paginated lists ("Next") | Reads the following list pages (up to a set number) |
| Lists that change page in place by a "Next" button (no link) | Clicks it while new content appears; each click counts toward the expansion limit |
| Cookie banners, pop-ups | Clicks "Reject" or "Close". If there is no reject button: opens "Cookie settings", turns off the optional categories, and saves. If that is not possible either, the banner is hidden on the page without giving consent. "Accept" is never clicked |
| Embedded frames (iframe), e.g. Greenhouse, Workable | Reads them separately and marks where the text came from |
| Open shadow DOM | Reads it together with the rest |
| Data arrives as a separate JSON request | Intercepts the response and extracts the vacancy list from it |
| JSON-LD (`JobPosting`) and embedded JSON (`__NEXT_DATA__`) | Parses them and keeps the vacancy expiry date |
| Links | Records the text, address, type, and what was done with each (see below) |
| PDF | Downloads it (with a size limit) and extracts the text |
| Presentations and images | Records the address and a note; does not recognize text in images |
| Bot protection | Detects and reports it honestly; **does not bypass it** |

### Links: what the program does with them

Every link found gets a type and a decision, and all of it goes into the report; no link disappears silently.

* `job_internal`, `ats_job`: opened in the browser (light read), one level deep;
* `ats_board` (a vacancy page in a hiring system): opened and read fully, with scrolling;
* `pdf`: downloaded, text extracted;
* `internal` and vacancies beyond the open limit: quick check without a browser (where it leads, what the title is);
* `external`, `social`: recorded but not opened (external sites can only be opened by an explicit setting);
* `mailto`, `tel`, anchors, `javascript:`: recorded.

## Protection against endless reading

All limits are in `config/settings.json` and work independently of each other. If a page reaches a limit, the report contains the note "read partially, reason: ...".

| Limit | Default |
|---|---|
| time per page | 30 s (15 s for a link without expansion) plus a 10 s margin before forced stop |
| scrolls | 15 |
| expansions (clicks, tabs) | 25 |
| consecutive "nothing new" | 3 rounds, then stop |
| vacancies per page | 100 |
| network requests per page | 400 (extra ones are aborted) |
| text size | 2,000,000 characters |
| embedded frames | 10 |
| pages per run | 40 |
| run time | 900 s |
| link depth | 1 |
| links opened from a page | 15 |
| list pages (pagination) | 5 |

In addition: ads and trackers are blocked, media and fonts are not loaded, local and private addresses are forbidden, every link from a page passes a safety check, and the same page is never read twice. Values above the hard ceilings (`CEILINGS` in `web_reader/settings.py`) are cut down by the program, which reports it.

## Retry after a refusal ("Access Denied")

If a site refuses access under frequent requests (access denied, 403, 429, 503), the program does not wait in place. It remembers the page and returns to it after `run.retry_after_starts` (default 3) other addresses from the list. If no addresses remain, retries are made with pauses (`run.retry_pause_s`, 30 s, multiplied by the attempt number). At most `run.max_retry_attempts` (2) attempts. If the site still refuses, the report says so. CAPTCHAs and browser checks (Cloudflare and the like) are not retried and not bypassed.

## Role matching

The dictionary `config/roles.json` has four groups: QA, LQA (linguistic testing), Compliance, and Certification/submission. Each contains abbreviations (QA, QC, SDET, LQA, TRC, ...), full titles (Quality Assurance, Test Manager, ...), names in English, Spanish, French, German, Polish, Portuguese, Italian, and Ukrainian, as well as Ukrainian word stems that catch all forms. Words that are too generic (localization, release manager, compliance alone) count as weak and are shown separately.

`index.md` gets these sections: matches by role, mentions only in the vacancy text (weak match), and matches excluded by region (Russia, Belarus). The dictionary can be edited without changing code. Matching works only on what was read: for unread sites, suitable vacancies may be missed, and the report says so.

## Custom limits for individual sites

The general limits stay the same. If one site needs more (for example, PlayStation has hundreds of vacancies on dozens of list pages), there is no need to raise the limit for everyone. The procedure:

1. Do a normal run.
2. `index.md` gets the section "Sites where the limit was not enough" with a ready settings line for each such site.
3. You decide whether to raise the limit for that site. If so, paste the line into `config/settings.json`:

```
"site_overrides": {
  "careers.playstation.com": {"run": {"max_pagination_pages": 12, "max_pages_per_start": 30}}
}
```

The domain also matches subdomains. Only these can be changed per site: `run.max_pagination_pages`, `run.max_pages_per_start`, `run.max_followed_per_page`, `limits.max_scrolls`, `limits.max_expansions`, `limits.max_jobs_per_page`, `limits.page_timeout_s`. The hard ceilings (`CEILINGS`) apply here too. Whole-run limits (`max_pages_per_run`, `max_run_seconds`) cannot be changed per site.

## What the program does not do (and why)

* **It does not bypass CAPTCHAs or strong bot protection** (Cloudflare and the like). This is protection the site owner put there on purpose. A simple "wait a few seconds" check is passed by the browser itself, and the program waits a little. If that fails, the page is marked `blocked`. Legitimate alternatives: the hiring system API, RSS, the same vacancies on other platforms.
* While reading pages it does not fill in forms, log in to accounts, or click "Apply", "Accept", and similar (the list of dangerous buttons is `SKIP_RE` in `web_reader/dom.py`). The only exception is the separate, manually started `login-test` command described below.
* It does not read closed shadow DOM (the browser exposes it to no one).
* It does not recognize text in images and PDF scans (the report has a note).
* It does not save downloaded files, except PDFs in memory for reading.

## Installation

Python 3.10 or newer is required.

```
pip install -r requirements.txt
python -m playwright install chromium
```

If Chrome or Chromium is already installed, you can set its path in `browser.executable_path` in the settings or in the environment variable `WEB_READER_BROWSER`.

## Running

A separate connection check always runs first (browser, JavaScript reading, iframe, shadow DOM, internet access). The main run starts only if it succeeds.

```
python -m web_reader check
python -m web_reader read https://example.com/careers
python -m web_reader read --file input/urls.txt
python -m web_reader read --file input/urls.txt --lang en
```

`read` runs the check by itself unless `--skip-check` is given. The result appears in the folder `results/<date-time>/`.

### In GitHub Actions

The file `.github/workflows/read.yml`. To run: Actions tab → Read pages → Run workflow (you can enter addresses in the form) or edit the file `input/urls.txt`. The workflow runs the check, then the reading, and saves the results in `results/` in the same repository. No secrets are required.

## What the result contains

In the folder `results/<date-time>/`:

* `index.md`: run summary. First comes the section "Unread and partially read" with reasons; then a table of all addresses; then those not processed because of limits and input errors;
* `NN-<address>.md`: a detailed report per address (reading completeness, vacancies found, pop-ups, expansion, embedded frames, intercepted data, text, links with decisions, pages opened from links);
* `NN-<address>.json`: the same data in full, for programmatic processing;
* `summary.json`: a short summary;
* `screenshots/`: screenshots for pages with problems (`output.screenshots`: `never`, `on_problem`, `always`).

The file `results/LATEST.txt` holds the name of the latest run. The last `output.keep_runs` runs are kept (default 10).

### How to read the completeness assessment

* **Statuses**: `ok` read completely; `partial` a limit was hit or there is a warning; `blocked` bot protection; `error` did not open; `empty` opened without content; `skipped` not processed (reason given).
* **Vacancies are collected from several independent sources**: links on the page, intercepted JSON, JSON-LD, embedded JSON. The count is compared with the number the page declares ("134 jobs", "Showing 1–20 of 134"). If noticeably fewer were collected, the program says so directly. A matching count is a heuristic, not a guarantee: at low confidence it is only a note.
* **Page type**: vacancy, vacancy list, vacancy closed or unavailable, not found, bot protection, login required, PDF.

## Settings

The file `config/settings.json`. Only what changes needs to be specified; the rest comes from the defaults. Typos in parameter names are not silently ignored but reported as warnings. Main sections: `limits` (limits for reading one page), `run` (run budget, depth, link rules), `browser`, `safety` (forbidden domains, list of hiring systems), `output`, `preflight`.

## Languages

Program messages and reports: Russian, English, Ukrainian (`"language": "ru" | "en" | "uk"` or the `--lang` key). The texts are in `web_reader/i18n.py`.

## Code structure

```
web_reader/
  settings.py    settings loading, limits and ceilings
  safety.py      address checks (local addresses, blacklist, DNS)
  classify.py    link and embedded frame type
  dom.py         scripts executed inside the page (text, links, buttons)
  harvest.py     accumulating text and links in snapshots
  network.py     request filter, counter, JSON interception
  popups.py      closing banners and windows
  expand.py      scrolling and expanding content
  reader.py      reading one page, completeness assessment
  runner.py      running over a list, following links, pagination
  fetch.py       quick link and PDF checks without a browser
  jobs.py        vacancies in JSON and JSON-LD, declared counts
  antibot.py     bot protection detection
  report.py      Markdown and JSON reports
  check.py       preliminary connection check
  cli.py         command line
  api.py         programmatic interface: read one address from another program
  browser.py     browser launch and page environment options
  budget.py      time and page budgets
  roles.py       role matching against config/roles.json
  i18n.py        program messages (ru, en, uk)
  probe.py       page structure probe used by login-test
  login.py       Atlassian login with a one-time code via Telegram (login-test)
  confluence_export.py  Confluence branch export to Markdown (REST API, read-only)
tests/           tests on a local test site
```

## Tests

```
pip install -r requirements-dev.txt
python -m pytest
```

The fast tests do not need a browser (`tests/test_units.py`). The browser tests (`tests/test_integration.py`) start a local test site with typical traps: loading by button, infinite scroll, tabs, pop-ups, iframe, shadow DOM, endless requests, a hung page, bot protection.

## Known limitations

* The heuristics (the "vacancy" link type, page type, declared count) can be wrong, so they are always accompanied by the source data in the report.
* Sites that load the list only by complex gestures or after login may be read incompletely; in that case the comparison with the declared count or the "nothing new" rule applies.
* A window with an address field (personal interface) is planned after the core and is not implemented yet.
* Authorship mark: the `authorship_mark` field in the settings is printed in the report footer; the mark format is still to be defined.

## License

MIT, see LICENSE.

## Login test (Atlassian, code from email via Telegram)

For sites behind an Atlassian login the program can sign in on its own and ask you for the one-time code from the email in Telegram. This step only checks the login; it saves nothing from the site (see `docs/login-spec.md`).

1. Create the repository secrets `ATLASSIAN_EMAIL`, `ATLASSIAN_PASSWORD`, `LOGIN_TELEGRAM_BOT_TOKEN`, `LOGIN_TELEGRAM_CHAT_ID`. Use a bot that has no webhook set and is not polled by another program at the same moment.
2. Run the workflow **Login test** (Actions tab) and keep Telegram open.
3. When the bot writes, reply with the digits from the email. The program deletes your message with the code, signs in and reports the result in Telegram and in the log.

Safety rules: credentials are typed only on the start host and `*.atlassian.com` / `*.atlassian.net`; a rejected password is never retried; a wrong code gets one retry; captchas and bot checks stop the run (never bypassed); secrets and codes are never printed. Locally: `python -m web_reader login-test --start-url https://...` with the same four values as environment variables.

## Confluence export (REST API, read-only)

Exports one branch of a Confluence Cloud space to Markdown files that keep the page hierarchy (see `docs/confluence-export-spec.md`).

1. Create the repository secrets `CONFLUENCE_EMAIL` (the Atlassian account email) and `CONFLUENCE_API_TOKEN` (id.atlassian.com, Security, API tokens).
2. Run the workflow **Confluence export** (root page id, site) and download the artifact `confluence-export` from the finished run (kept 3 days, nothing is committed).
3. Locally: `python -m web_reader confluence-export --root-id 123456 --site example.atlassian.net --out export --zip`.

Only GET requests are made; the token is never printed or saved. The export stops with an error if the branch has more pages than `--max-pages` (default 3000).

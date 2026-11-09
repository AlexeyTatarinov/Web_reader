# Spec: authenticated reading (Atlassian login with emailed code)

Status: step 1 (login test only). Step 2 (crawl of the nested knowledge-base tree) comes after the login test passes in GitHub Actions.

## Goal
Let web_reader read a knowledge base that sits behind an Atlassian login (email -> password -> one-time code sent to the account's email). The code is requested from the owner through a Telegram bot while the workflow is running; the owner replies with the digits and the run continues.

## Decisions
- Credentials come only from environment variables (GitHub secrets): `ATLASSIAN_EMAIL`, `ATLASSIAN_PASSWORD`. The Telegram channel uses `LOGIN_TELEGRAM_BOT_TOKEN`, `LOGIN_TELEGRAM_CHAT_ID`.
- Login is a stage machine driven by what is visible on the page (email, password, code, done), not by fixed URLs, because the login page is a single-page app.
- The code is requested via Telegram: the program sends a message, then polls `getUpdates` and accepts only messages from the configured chat that arrived after the request.
- Never logged or written to disk: password, one-time code. The email is not printed either. Reports contain only stage names.
- Rejected input (wrong password, wrong code twice, no code within the timeout) stops the run with a clear reason. No retries with the password, to avoid locking the account.
- Bot protection (captcha, "access denied") is detected and reported; it is never bypassed.
- The session (cookies) is kept in memory and in a temp file inside one run only. Nothing is committed to the repository: the login workflow does not commit anything and uploads only failure screenshots as a short-lived artifact.
- Messages to the owner support ru, en, uk (`i18n.py`).

## Out of scope for step 1
Crawling the knowledge base, converting pages to Markdown, storing the session between runs.

## Acceptance criteria (tests first)
1. Code extraction takes the first 4-8 digit number from a reply.
2. Telegram prompter: ignores other chats and messages older than the request, returns the code, returns None on timeout.
3. Login against a local fake site (email step is single-page, password and code steps are server-rendered) ends logged in and saves the session.
4. Wrong password stops with reason `rejected`, without asking for a code.
5. Wrong code once: asks again; wrong twice: stops.
6. No code in time: stops with `code_timeout`.
7. Neither the password nor the code appears in stdout/stderr or in the error text.
8. CLI `login-test` exits 2 with a clear message when secrets are missing.

# Spec: Confluence branch export to Markdown (REST API, read-only)

Goal: export one branch of a Confluence Cloud space (first target: "User Guides", page id 7012399, about 1500 pages) into Markdown files that keep the page hierarchy, so the knowledge base can be searched and read as plain files.

## Decisions
- Auth: Basic auth with the account email and an Atlassian API token from environment variables `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN` (GitHub secrets). Never printed, never written to files.
- Read-only: only GET requests. Nothing in Confluence is created, edited or deleted.
- Fetch: the root page by id, then all descendants with one paginated CQL search (`ancestor = ID AND type = page`) that also returns each page's rendered HTML (`body.export_view`) and ancestors.
- Hosts: HTTPS and `*.atlassian.net` only (`--allow-local` for tests). Pagination links that point to another host are refused.
- Politeness: pause between requests, retry on 429/5xx honouring `Retry-After`, at most 5 retries.
- Safety cap: `--max-pages` (default 3000). If the branch is bigger, the run stops with an error instead of exporting a silent partial result.
- Layout: a page with children becomes `<slug>/index.md` with its children inside; a leaf becomes `<slug>.md`. Slugs are lowercase, Unicode letters are kept; a clash inside one folder adds `-<id>`.
- Each file starts with front matter (title, id, url, last_modified, path in the tree). Links to other exported pages are rewritten to relative file links; other links stay absolute.
- Output: the folder tree, `INDEX.md` (tree with links) and `_export.json` (counts, errors, no secrets), plus an optional zip. The workflow uploads the zip as a short-lived artifact and commits nothing.
- Attachments and images are not downloaded in this step (image links are kept as URLs).

## Acceptance criteria (tests first)
1. Hierarchy and file layout as above; pages outside the branch are not exported.
2. Markdown keeps headings, lists, tables and links.
3. Links between exported pages become relative.
4. Duplicate titles in one folder do not overwrite each other.
5. Request carries correct Basic auth; a wrong token ends with a clear error and the token appears nowhere.
6. 429 is retried; a foreign pagination host and a branch bigger than the cap are refused.
7. CLI: missing secrets and a non-atlassian host exit with code 2.

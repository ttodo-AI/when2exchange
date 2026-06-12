# Workflow: build-dashboard

## Purpose
Build a **self-contained HTML dashboard** from the final scored + drafted output.
One file, no external dependencies — open it directly in a browser. Shows, per
item: the score, the source link, the brief (hook/point/example/format), and the
draft. Sortable and filterable.

## Inputs
- The Writer's drafts JSON (`output/drafts-*.json`). Default: most recent.
- The Scorer's JSON (`output/scored-*.json`) for the relevance/content breakdown.
  Default: most recent. (Joined to drafts by source link; optional.)

## Scripts to run
```
python executions/dashboard.py
```
Optional flags:
- `--in <path>`      drafts JSON to render (default: latest `output/drafts-*.json`).
- `--scored <path>`  scored JSON for the score breakdown (default: latest `output/scored-*.json`).
- `--out <path>`     output HTML (default `output/dashboard-<timestamp>.html`).

## Behavior
1. Loads the drafts JSON; enriches each item with relevance/content_potential and
   reasons from the scored file (matched by link).
2. Inlines the data into a single HTML file with embedded CSS + vanilla JS
   (data is escaped so it is safe inside the `<script>` tag).
3. The page supports: text filter (title/brief/draft), min-total-score filter,
   sort by total/relevance/content/title (asc/desc), and a per-card
   "Copy draft" button.

## What a good result looks like
- A single `.html` file with NO external references (no CDN/script src/link href)
  that opens offline and shows all items as cards.
- Exit code 0. Missing drafts JSON exits non-zero with a clear message.

## Reporting
Report how many items were rendered and the output path, and give the
`file:///...` URL so the user can open it directly.

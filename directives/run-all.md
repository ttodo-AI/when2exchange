# Workflow: run-all

## Purpose
Run the **entire content pipeline** in one command and open the dashboard:
Scout → Scorer → Editor → Writer → Dashboard. Use this when the user wants
"the whole thing" / fresh drafts + dashboard from scratch.

## Inputs
- `FIRECRAWL_API_KEY` and `ANTHROPIC_API_KEY` in `.env`.
- `voice/voice-samples.md` filled in (the Writer stage needs it).

## Scripts to run
```
python executions/run_all.py
```
Optional flags:
- `--query "<terms>"`  override the Scout's search query.
- `--full`             pass `--full` to the Scout (scrape article bodies).
- `--no-open`          don't open the dashboard at the end.

## Behavior
Runs each stage in order as a subprocess, from the project root. Each stage
auto-picks the previous stage's most recent `output/` file, so no paths need to
be wired by hand. If any stage exits non-zero, the pipeline stops and reports
which stage failed. On success it opens the newest `output/dashboard-*.html`.

## What a good result looks like
- Fresh files in `output/`: `krw-exchange-rate-*.json`, `scored-*.json`,
  `briefs-*.{md,json}`, `drafts-*.{md,json}`, `dashboard-*.html`.
- The dashboard opens in the browser (unless `--no-open`).
- Exit code 0. A failing stage stops the run with a clear message.

## Reporting
Report that the pipeline completed, list the key output files (especially the
dashboard path / `file:///` URL), and surface the top draft(s).

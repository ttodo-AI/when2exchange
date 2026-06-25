# Agentic Workflow Framework

This repo is an agent-operated workflow runner. Work is described in **directives**
(what to do) and performed by **executions** (Python scripts that do the actual work).

## Layout

```
directives/   Markdown files. Each describes ONE workflow: its purpose,
              inputs, the script(s) to run, and what a good result looks like.
executions/   Standalone Python scripts. Each does one job and prints its
              results to stdout. No shared framework — every script runs on
              its own via `python executions/<name>.py`.
.env          API keys and secrets (NOT committed). See .env.example.
```

## Workflow routing (read this first)

CLAUDE.md is the orchestration layer. On **every** request:

1. Read this file first.
2. **Read `docs/WORKLOG.md`** — the living work log (할 일 / 진행 중 / 최근 완료).
   Pick up wherever the last chat left off, and **keep it updated** as you work
   (move items 진행 중 → 완료, add new todos). This is what makes work survive a
   lost chat.
3. Map the request to a workflow in the table below (match by name or by goal).
4. Open that workflow's directive, then run the execution script it names.

| Workflow | Directive | Execution script |
|----------|-----------|------------------|
| Summarize text | `directives/summarize-text.md` | `executions/summarize.py` |
| Exchange-rate (KRW) news watcher (the "Scout") | `directives/exchange-rate-watcher.md` | `executions/exchange_rate_watcher.py` |
| Score the Scout's results for audience fit (the "Scorer") | `directives/score-results.md` | `executions/scorer.py` |
| Write content briefs from top scored results (the "Editor") | `directives/write-briefs.md` | `executions/editor.py` |
| Draft LinkedIn posts from briefs, in the user's voice (the "Writer") | `directives/draft-posts.md` | `executions/writer.py` |
| Build a self-contained HTML dashboard of the final output (private/internal) | `directives/build-dashboard.md` | `executions/dashboard.py` |
| Find the day's Top 4 USD/KRW drivers with sourced bullets (the "Analyst") | `directives/factor-analysis.md` | `executions/factor_analysis.py` |
| Build this week's FX economic calendar (D-Day events) | `directives/econ-calendar.md` | `executions/econ_calendar.py` |
| Build a shareable consumer page for monthly exchangers (public-facing) | `directives/build-share-page.md` | `executions/share_page.py` |
| **Run the whole pipeline** (Scout→Scorer→Editor→Writer→Dashboard) and open it | `directives/run-all.md` | `executions/run_all.py` |

The content pipeline is a chain — each stage reads the previous stage's latest
`output/` file: **Scout → Scorer → Editor → Writer → Dashboard**. Run a single
stage from the table above, or the whole chain at once with `run-all`.

If no row matches, list `directives/` and pick the closest one. One directive
maps to one execution script; adding a workflow means adding a row above.

## How to run a workflow

When asked to run a workflow (by name or by describing a goal):

1. **Read the directive.** Open `directives/<workflow>.md` and read it fully.
   If no name was given, list `directives/` and pick the matching one.
2. **Run the scripts.** Execute the scripts the directive names, in order,
   with `python executions/<script>.py [args]`. Pass along any inputs the
   directive calls for.
3. **Report results.** Summarize what each script produced — surface the actual
   output, not just "done." If a script fails, report the error and stop;
   don't guess at a fix unless the directive says how to handle it.

## Conventions

- **Secrets** live only in `.env`. Every script loads them with
  `python-dotenv` (`load_dotenv()` then `os.environ[...]`). Never hardcode keys
  or echo their values.
- **Scripts are standalone.** A script imports only the standard library plus
  its own pip dependencies; it does not import from other scripts. Shared
  behavior is copied, not abstracted, so each script stays runnable in isolation.
- **Scripts print results.** Human-readable output to stdout, errors to stderr,
  non-zero exit code on failure — so the agent can tell success from failure.
- **Directives name their scripts.** If you add a workflow, add a directive that
  points to the exact script(s) and arguments to run.

## Setup

```
pip install -r requirements.txt
cp .env.example .env   # then fill in real keys
```

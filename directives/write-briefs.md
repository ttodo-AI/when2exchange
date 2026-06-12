# Workflow: write-briefs

## Purpose
Turn the **Scorer's** top results into ready-to-use **content briefs**. Takes the
top N (default 3) scored articles and writes a brief for each with four parts —
**hook, point, example, format** — aimed at the user's audience. Saves the briefs
to a Markdown file.

## Inputs
- A Scorer output JSON (`output/scored-*.json`). By default the Editor auto-picks
  the most recent one. The audience is read from that file (override with `--audience`).
- `ANTHROPIC_API_KEY` in `.env`.

## Scripts to run
```
python executions/editor.py
```
Optional flags:
- `--in <path>`        Scorer file to use (default: latest `output/scored-*.json`).
- `--top N`            how many top articles to brief (default 3).
- `--audience "<...>"` override the audience (default: read from the scored file).
- `--out <path>`       output file (default `output/briefs-<timestamp>.md`).
- `--model <id>`       Claude model (default claude-haiku-4-5-20251001).

## Behavior
1. Loads the Scorer file, re-sorts by `total`, takes the top N.
2. Sends them to Claude in one call; gets a brief per article:
   `hook` (scroll-stopping opener), `point` (core takeaway), `example`
   (concrete number/comparison), `format` (best format + why).
3. Renders a Markdown file (one section per article) and prints it to stdout.

## What a good result looks like
- A Markdown file with N briefs, each having hook / point / example / format,
  plus the article's score and source link.
- Exit code 0. Missing key / unreadable Scorer file / unparseable model reply
  exit non-zero with a clear message.

## Reporting
Report the source file used and where the briefs were saved, then show the
briefs (or the top one) so the user can act on them.

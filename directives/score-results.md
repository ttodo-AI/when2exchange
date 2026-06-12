# Workflow: score-results

## Purpose
Score the **Scout's** news results (from `exchange-rate-watcher`) for how well
each fits the user's audience. Each article gets two 1-10 scores —
**relevance** and **content potential** — with one line of reasoning per score,
then the results are ranked and saved to a new file.

## Inputs
- A Scout output JSON (the watcher's `output/krw-exchange-rate-*.json`).
  By default the Scorer auto-picks the most recent one.
- `ANTHROPIC_API_KEY` in `.env`.

## Scripts to run
```
python executions/scorer.py
```
Optional flags:
- `--in <path>`        Scout file to score (default: latest `output/krw-exchange-rate-*.json`).
- `--audience "<...>"` audience description used for scoring. Default targets
                       Korean readers interested in personal finance / smart
                       spending / currency exchange / money-saving tips.
- `--out <path>`       output file (default `output/scored-<timestamp>.json`).
- `--model <id>`       Claude model (default claude-haiku-4-5-20251001).

## Behavior
1. Loads the Scout file's `articles` (title / summary / link / date).
2. Sends them to Claude in one call; gets `relevance` and `content_potential`
   (1-10) plus a one-line reason for each.
3. Computes `total = relevance + content_potential`, sorts high→low.
4. Writes `output/scored-<timestamp>.json` and prints a ranked list to stdout.

## What a good result looks like
- A JSON file with each article scored (relevance, relevance_reason,
  content_potential, content_reason, total), ranked by total.
- Exit code 0. Missing key / unreadable Scout file / unparseable model reply
  exit non-zero with a clear message.

## Reporting
Report the source file scored, where the scored file was saved, and the top few
articles by total score with their one-line reasons and links.

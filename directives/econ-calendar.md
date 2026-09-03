# Workflow: econ-calendar

## Purpose
Build **this week's economic calendar** of events likely to move the USD/KRW
rate (FOMC, 미 CPI·고용, 한은 금통위 등), each with an expected-impact line and an
importance score. The share page renders it at the bottom; **D-Day is computed
client-side** so it stays current.

## Inputs
- `ANTHROPIC_API_KEY` in `.env`. (뉴스 검색은 Google News RSS 무료, 키 불필요.)

## Scripts to run
```
python executions/econ_calendar.py
```
Optional flags:
- `--per-query N`  news results per search query (default 12).
- `--out <path>`   output JSON (default `output/calendar-<timestamp>.json`).
- `--model <id>`   Claude model (default `claude-sonnet-5`).

## Behavior
1. Searches recent Korean news for upcoming macro events (a few queries).
2. Claude extracts events in the next ~8 days, each with `date` (YYYY-MM-DD,
   **grounded in the news — no guessing**), `name`, `impact` (one-line FX effect
   with direction), `importance` (1-3 = ★).
3. Saves `output/calendar-<timestamp>.json` (`{today_kst, events:[...]}`).

## Downstream
`executions/share_page.py` auto-picks the latest `calendar-*.json` and renders a
"이번주 환율 영향 일정" table at the bottom. D-Day (D-3 / D-DAY / 지남) is
computed in the browser from each event's date.

## What a good result looks like
- A handful of dated, FX-relevant events with clear impact lines and ★ importance.
- Dates traceable to the news. If the news has no firm dates, fewer events appear
  (that's expected — accuracy over completeness).

## Reporting
List the extracted events with date / ★ / impact, and the output path.

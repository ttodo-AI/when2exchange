# Workflow: factor-analysis

## Purpose
Find the day's **Top 4 drivers** of the USD/KRW rate and explain each with
specific, sourced bullets. Feeds the share page's "오늘 환율을 움직인 요인
Top 4" section and the "왜 그럴까요" summary.

## Inputs
- `FIRECRAWL_API_KEY` and `ANTHROPIC_API_KEY` in `.env`.
- (Optional) latest Scout `output/krw-exchange-rate-*.json` for rate context.

## Scripts to run
```
python executions/factor_analysis.py
```
Optional flags:
- `--per-factor N`  news results per factor (default 15).
- `--out <path>`    output JSON (default `output/factors-<timestamp>.json`).
- `--model <id>`    Claude model (default `claude-sonnet-4-6`).

## Behavior
1. Searches recent Korean news (Firecrawl, last week) for each of **10 fixed
   factors** (미 통화정책 / 한 통화정책 / 미 지표 / 한 수출 / 달러인덱스 /
   아시아통화 / 외국인수급 / 지정학 / 유가 / 무역·관세).
2. Asks Claude to pick the **4 that actually moved USD/KRW today**, write 4
   specific bullets per factor (numbers + proper nouns + cause→effect), choose
   3–5 source articles, and a concrete `overall_why` (1–2 sentences).
3. Saves `output/factors-<timestamp>.json`:
   `{overall_why, factors:[{name, emoji, headline, bullets[4], sources[]}]}`.

## Writing rule (hard)
Logic / accuracy / specificity first. **No vague filler** ("여러 요인이 겹쳐",
"복합적 요인", "대내외 불확실성"). Every bullet must cite a concrete fact from
the news.

## Downstream
`executions/share_page.py` auto-picks the latest `factors-*.json` and renders the
Top 4 blocks + uses `overall_why` for "왜 그럴까요". If no factors file exists,
the share page falls back to the plain news list.

## Reporting
Report the 4 selected factors with their headlines, and where the JSON was saved.

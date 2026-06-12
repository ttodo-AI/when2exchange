# Workflow: exchange-rate-watcher (KRW)

## Purpose
Monitor recent news about the **Korean won (KRW)** exchange rate — especially
articles useful for deciding *when to exchange currency* (원/달러 환율 전망,
환전 타이밍). Pull the last 24 hours, keep the top 10, and save them locally.

## Inputs
- `FIRECRAWL_API_KEY` in `.env`.
- Optional `--query` to override the default search terms.
  Default: `원/달러 환율 전망 환전` (USD/KRW rate outlook + currency exchange).
  Note: very long queries (e.g. adding `타이밍`) can return zero news hits;
  keep the query short for reliable coverage.

## Scripts to run
```
python executions/exchange_rate_watcher.py
```
Optional flags:
- `--query "<terms>"`   override the search query (Korean or English).
- `--hours N`           look-back window in hours (default 24).
- `--limit N`           how many articles to keep (default 10).
- `--out <path>`        output file (default `output/krw-exchange-rate-<timestamp>.json`).
- `--full`              scrape each kept article for a fuller summary (uses extra
                        Firecrawl credits — 1 per article). Without it, summaries
                        are the short search snippets.
- `--monthly-usd N`     USD converted from KRW each month (default 2000), used to
                        frame the timing verdict.
- `--no-verdict`        skip the Claude timing verdict.

## Timing verdict
By default (when `ANTHROPIC_API_KEY` is set), the script asks Claude to read the
collected news and judge whether now is relatively GOOD / NEUTRAL / BAD timing to
convert **KRW → USD** for the user's fixed monthly obligation (default $2,000).
A weak won (high KRW/USD) makes dollars more expensive, so it leans BAD. The
verdict (label + reasoning + an approximate rate) is printed and saved under
`timing_verdict` in the JSON. It is news-based context, **not** financial advice.

## Behavior
1. Searches Firecrawl news (`sources=["news"]`) for the query, restricted to the
   past day.
2. Filters results to those published within the look-back window.
3. Keeps the top `--limit` (default 10), each normalized to:
   `title`, `link`, `summary`, `date`.
4. Writes them to a JSON file under `output/` and prints a readable list to stdout.

## What a good result looks like
- A JSON file containing up to 10 entries, each with title / link / summary / date.
- stdout shows the same 10 as a numbered list with dates.
- Exit code 0. On a missing key or API error: clear message to stderr, non-zero exit.

## Reporting
Tell the user how many articles were saved and where (the file path), then list
the top headlines with their dates and links.

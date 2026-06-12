# Workflow: build-share-page

## Purpose
Build a **shareable, consumer-facing HTML page** for people who exchange KRW→USD
every month — friends like the user. Unlike the internal dashboard
(scores/briefs/drafts), this shows only what a reader cares about: a big
"is now a good time to exchange?" verdict, the current rate, a plain-Korean
"why", a practical tip, and the latest news. Single self-contained file,
mobile-first.

## Inputs
- The Scout JSON (`output/krw-exchange-rate-*.json`) — it carries the
  `timing_verdict` and the Korean `articles`. Default: most recent.
- `ANTHROPIC_API_KEY` in `.env` (used to rewrite the verdict as friendly Korean;
  optional — falls back to the raw verdict text with `--no-ai` or no key).

## Scripts to run
```
python executions/share_page.py
```
Optional flags:
- `--in <path>`   Scout JSON to use (default: latest `output/krw-exchange-rate-*.json`).
- `--out <path>`  output HTML (default `output/share-<timestamp>.html`).
- `--no-ai`       skip the Claude Korean rewrite; use the raw parsed verdict.

## Behavior
1. Loads the latest Scout JSON; reads `timing_verdict` (label + text) and articles.
2. Rewrites the verdict into friendly Korean consumer copy (headline / rate /
   why / tip) via Claude; falls back to parsing RATE/WHY/TIP from the raw text.
3. Renders a mobile-first, light-themed self-contained HTML page with a hero
   verdict, news cards (title/date/summary/link), a native "share" button, and a
   "not financial advice" footer.

## What a good result looks like
- A single `.html` file with NO external references, readable on a phone, in
  Korean, with the verdict and today's news.
- Exit code 0. Missing Scout JSON exits non-zero with a clear message.

## Reporting
Report the output path / `file:///` URL, and remind that to share it as a real
URL it must be hosted (e.g. GitHub Pages / Netlify Drop / Vercel) — the file
itself can also be sent directly (KakaoTalk/email) and opened in a browser.

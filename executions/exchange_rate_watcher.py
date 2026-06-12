#!/usr/bin/env python3
"""KRW exchange-rate news watcher.

Searches Firecrawl news for Korean-won exchange-rate articles, keeps those from
the last N hours, and saves the top results (title / link / summary / date) to a
local JSON file.

Standalone script. Run directly:
    python executions/exchange_rate_watcher.py
    python executions/exchange_rate_watcher.py --query "원/달러 환율" --hours 24 --limit 10
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

DEFAULT_QUERY = "원/달러 환율 전망 환전"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KRW exchange-rate news watcher (Firecrawl).")
    p.add_argument("--query", default=DEFAULT_QUERY, help="Search terms (KR or EN).")
    p.add_argument("--hours", type=int, default=24, help="Look-back window in hours (default 24).")
    p.add_argument("--limit", type=int, default=10, help="Max articles to keep (default 10).")
    p.add_argument("--out", default=None, help="Output JSON path (default output/krw-exchange-rate-<ts>.json).")
    p.add_argument(
        "--full",
        action="store_true",
        help="Scrape each kept article for a fuller summary (extra Firecrawl credits).",
    )
    p.add_argument(
        "--monthly-usd",
        type=float,
        default=2000,
        help="USD you convert from KRW each month, used for the timing verdict (default 2000).",
    )
    p.add_argument(
        "--no-verdict",
        action="store_true",
        help="Skip the Claude timing verdict (verdict runs by default if ANTHROPIC_API_KEY is set).",
    )
    return p.parse_args()


def get(item, *keys):
    """Read the first present key from a dict-or-object result item."""
    for key in keys:
        if isinstance(item, dict):
            if key in item and item[key] not in (None, ""):
                return item[key]
        else:
            val = getattr(item, key, None)
            if val not in (None, ""):
                return val
    return None


def parse_date(raw, now: datetime):
    """Best-effort parse of a result date into an aware datetime, or None.

    Handles ISO-8601 (with/without Z) and relative strings like "5 hours ago".
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):  # epoch seconds or ms
        ts = raw / 1000 if raw > 1e11 else raw
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    text = str(raw).strip()

    # Relative: "3 hours ago", "2 days ago", "방금", "1시간 전"
    rel = re.search(r"(\d+)\s*(minute|hour|day|분|시간|일)", text, re.IGNORECASE)
    if rel and ("ago" in text.lower() or "전" in text):
        n = int(rel.group(1))
        unit = rel.group(2).lower()
        if unit in ("minute", "분"):
            return now - timedelta(minutes=n)
        if unit in ("hour", "시간"):
            return now - timedelta(hours=n)
        if unit in ("day", "일"):
            return now - timedelta(days=n)
    if re.search(r"(방금|just now)", text, re.IGNORECASE):
        return now

    # ISO-8601
    iso = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    # Common explicit formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y.%m.%d", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def extract_news(resp):
    """Pull the news list out of whatever shape the SDK returns."""
    # Try common containers in order.
    for attr in ("news", "data", "results", "web"):
        val = resp.get(attr) if isinstance(resp, dict) else getattr(resp, attr, None)
        if isinstance(val, list) and val:
            return val
        if isinstance(val, dict):  # e.g. {"data": {"news": [...]}}
            inner = val.get("news") or val.get("results")
            if isinstance(inner, list) and inner:
                return inner
    if isinstance(resp, list):
        return resp
    return []


def scrape_summary(client, url: str, max_chars: int = 700):
    """Scrape an article and return a longer plain-text summary, or None on failure."""
    if not url:
        return None
    try:
        doc = client.scrape(url, formats=["markdown"])
    except Exception:
        return None
    md = doc.get("markdown") if isinstance(doc, dict) else getattr(doc, "markdown", None)
    if not md:
        return None
    # Collapse whitespace and drop markdown link/image noise for a clean extract.
    text = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", md)  # images/links
    text = re.sub(r"[#>*`_]+", "", text)              # markdown markers
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


def timing_verdict(articles, monthly_usd: float):
    """Use Claude to judge KRW->USD exchange timing from the collected news.

    The user must convert KRW into `monthly_usd` dollars every month, so a weak
    won (high KRW/USD) makes their dollars more expensive. Returns a dict with a
    label/headline_rate/reasoning, or None if no Anthropic key / call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    digest = "\n".join(
        f"- {a['title']} ({a.get('date') or '?'}): {(a.get('summary') or '')[:300]}"
        for a in articles
    )
    prompt = (
        "You are a currency-timing assistant. The user must convert KRW into "
        f"US${monthly_usd:,.0f} EVERY MONTH (won -> dollar), so a WEAK won "
        "(high KRW per USD) means their dollars are MORE expensive, and a STRONG "
        "won means cheaper. Based ONLY on the Korean exchange-rate news below, "
        "judge whether right now is relatively good or bad timing for this "
        "monthly conversion.\n\n"
        f"NEWS:\n{digest}\n\n"
        "Respond in this exact format, in English:\n"
        "VERDICT: <GOOD | NEUTRAL | BAD> timing to buy USD with KRW now\n"
        "RATE: <approximate current USD/KRW level from the news, or 'unknown'>\n"
        "WHY: <2-3 sentences citing what the news says about level and trend>\n"
        "TIP: <1 sentence of practical advice for a fixed monthly $-obligation>\n"
        "Note: this is news-based, not financial advice."
    )
    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        return {"text": f"(verdict unavailable: {exc})"}
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    label = None
    for cand in ("GOOD", "NEUTRAL", "BAD"):
        if f"VERDICT: {cand}" in text.upper():
            label = cand
            break
    return {"label": label, "text": text}


def main() -> None:
    args = parse_args()
    load_dotenv()

    # Print UTF-8 regardless of the console's locale (Windows cp949 etc.).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        sys.exit("error: FIRECRAWL_API_KEY is not set (add it to .env).")

    try:
        from firecrawl import Firecrawl  # firecrawl-py v2
    except ImportError:
        try:
            from firecrawl import FirecrawlApp as Firecrawl  # older SDK
        except ImportError:
            sys.exit("error: firecrawl-py not installed. Run: pip install -r requirements.txt")

    client = Firecrawl(api_key=api_key)

    # Ask for more than --limit so the 24h filter still leaves enough.
    fetch_n = max(args.limit * 3, 20)
    try:
        resp = client.search(
            query=args.query,
            limit=fetch_n,
            sources=["news"],
            tbs="qdr:d",  # Google "past day" filter; we re-filter precisely below.
        )
    except TypeError:
        # Older signatures may not accept sources/tbs.
        resp = client.search(query=args.query, limit=fetch_n)
    except Exception as exc:
        sys.exit(f"error: Firecrawl search failed: {exc}")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)
    raw_items = extract_news(resp)

    if not raw_items:
        sys.exit("error: Firecrawl returned no news results for this query.")

    articles = []
    for item in raw_items:
        date_raw = get(item, "date", "published", "publishedAt", "publishedDate", "age")
        dt = parse_date(date_raw, now)
        # Keep if within window, or if no date could be parsed (tbs already scoped it).
        if dt is not None and dt < cutoff:
            continue
        articles.append({
            "title": get(item, "title", "name") or "(untitled)",
            "link": get(item, "url", "link", "href"),
            "summary": get(item, "description", "snippet", "summary", "markdown") or "",
            "date": dt.isoformat() if dt else (str(date_raw) if date_raw else None),
        })
        if len(articles) >= args.limit:
            break

    if not articles:
        sys.exit(f"error: no articles within the last {args.hours}h for this query.")

    if args.full:
        for a in articles:
            full = scrape_summary(client, a["link"])
            if full:
                a["summary"] = full

    verdict = None
    if not args.no_verdict:
        verdict = timing_verdict(articles, args.monthly_usd)

    out_path = args.out or os.path.join(
        "output", f"krw-exchange-rate-{now.strftime('%Y-%m-%d_%H%M')}.json"
    )
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    payload = {
        "query": args.query,
        "generated_at": now.isoformat(),
        "window_hours": args.hours,
        "count": len(articles),
        "monthly_usd": args.monthly_usd,
        "timing_verdict": verdict,
        "articles": articles,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"Saved {len(articles)} article(s) to {out_path}\n")
    if verdict and verdict.get("text"):
        print("=" * 60)
        print(f"TIMING VERDICT  (converting KRW -> US${args.monthly_usd:,.0f}/month)")
        print("=" * 60)
        print(verdict["text"])
        print("=" * 60 + "\n")
    for i, a in enumerate(articles, 1):
        when = a["date"] or "date unknown"
        print(f"{i}. {a['title']}  ({when})")
        if a["link"]:
            print(f"   {a['link']}")
        if a["summary"]:
            snippet = a["summary"].replace("\n", " ")
            print(f"   {snippet[:160]}{'…' if len(snippet) > 160 else ''}")
        print()


if __name__ == "__main__":
    main()

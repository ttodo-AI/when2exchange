#!/usr/bin/env python3
"""KRW exchange-rate news watcher (free, no API key).

Searches Google News RSS for Korean-won exchange-rate articles, keeps those from
the last N hours, best-effort fetches each article body, and saves the top
results (title / link / summary / date) to a local JSON file.

Free by design: uses only the standard library (urllib + xml) to hit Google
News RSS — no Firecrawl, no API key, so a credit outage can't break the build.
The optional timing verdict still uses Claude if ANTHROPIC_API_KEY is set.

Standalone script. Run directly:
    python executions/exchange_rate_watcher.py
    python executions/exchange_rate_watcher.py --query "원/달러 환율" --hours 24 --limit 10
"""
import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from dotenv import load_dotenv

DEFAULT_QUERY = "원/달러 환율 전망 환전"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
RSS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KRW exchange-rate news watcher (Google News RSS, free).")
    p.add_argument("--query", default=DEFAULT_QUERY, help="Search terms (KR or EN).")
    p.add_argument("--hours", type=int, default=24, help="Look-back window in hours (default 24).")
    p.add_argument("--limit", type=int, default=10, help="Max articles to keep (default 10).")
    p.add_argument("--out", default=None, help="Output JSON path (default output/krw-exchange-rate-<ts>.json).")
    p.add_argument(
        "--no-body",
        action="store_true",
        help="Skip fetching article bodies (use RSS snippet only). Faster, thinner.",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="(kept for compatibility) Body fetch is on by default now; this is a no-op.",
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


def parse_date(raw, now: datetime):
    """Best-effort parse of a date string into an aware datetime, or None.

    Handles RFC-822 (RSS pubDate), ISO-8601, and relative strings.
    """
    if raw is None:
        return None
    text = str(raw).strip()

    # RFC-822 e.g. "Mon, 15 Jun 2026 04:00:00 GMT" (Google News RSS pubDate)
    try:
        dt = parsedate_to_datetime(text)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    # Relative: "3 hours ago", "1시간 전", "방금"
    rel = re.search(r"(\d+)\s*(minute|hour|day|분|시간|일)", text, re.IGNORECASE)
    if rel and ("ago" in text.lower() or "전" in text):
        n, unit = int(rel.group(1)), rel.group(2).lower()
        if unit in ("minute", "분"):
            return now - timedelta(minutes=n)
        if unit in ("hour", "시간"):
            return now - timedelta(hours=n)
        if unit in ("day", "일"):
            return now - timedelta(days=n)
    if re.search(r"(방금|just now)", text, re.IGNORECASE):
        return now

    # ISO-8601
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def fetch(url: str, timeout: int = 10) -> str | None:
    """GET a URL with a browser UA, following redirects. Returns text or None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                    "Accept-Language": "ko,en;q=0.8"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            charset = r.headers.get_content_charset() or "utf-8"
            return r.read().decode(charset, errors="replace")
    except Exception:
        return None


def search_news(query: str):
    """Google News RSS search -> list of {title, link, summary, date_raw, source}.

    `when:3d` keeps results recent (covers weekend gaps); main re-filters by --hours.
    """
    xml = fetch(RSS.format(q=urllib.parse.quote(f"{query} when:3d")))
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = _strip_html(it.findtext("description") or "")
        pub = it.findtext("pubDate")
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        # Google often appends " - <source>" to the title; trim it.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")].strip()
        items.append({"title": title, "link": link, "summary": desc,
                      "date_raw": pub, "source": source})
    return items


def fetch_body(url: str, max_chars: int = 700):
    """Best-effort article-body extract (follows Google redirect). None on failure."""
    page = fetch(url, timeout=12)
    if not page:
        return None
    # Prefer paragraph text; many Korean news pages put body in <p>.
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", page)
    chunks = [_strip_html(p) for p in paras]
    chunks = [c for c in chunks if len(c) >= 40]
    text = " ".join(chunks).strip()
    if len(text) < 80:  # too thin to trust — likely a redirect/consent shell
        return None
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


def timing_verdict(articles, monthly_usd: float):
    """Use Claude to judge KRW->USD exchange timing from the collected news."""
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

    raw_items = search_news(args.query)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)

    def _mk(item):
        dt = parse_date(item.get("date_raw"), now)
        return dt, {
            "title": item["title"] or "(untitled)",
            "link": item["link"],
            "summary": item["summary"] or "",
            "source": item.get("source") or "",
            "date": dt.isoformat() if dt else (str(item["date_raw"]) if item["date_raw"] else None),
        }

    articles = []
    for item in raw_items:
        dt, art = _mk(item)
        if dt is not None and dt < cutoff:
            continue
        articles.append(art)
        if len(articles) >= args.limit:
            break

    # Graceful fallback: never hard-fail the build. If nothing is within the
    # window (or the search came back empty), keep the freshest items we got —
    # Google News RSS is newest-first, so the top N are the most recent.
    if not articles and raw_items:
        articles = [_mk(it)[1] for it in raw_items[: args.limit]]
        print(f"note: no items within {args.hours}h; using {len(articles)} most-recent instead.",
              file=sys.stderr)

    if not args.no_body:
        for a in articles:
            body = fetch_body(a["link"])
            if body and len(body) > len(a["summary"]):
                a["summary"] = body

    verdict = None
    if not args.no_verdict and articles:
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
        src = f" · {a['source']}" if a.get("source") else ""
        print(f"{i}. {a['title']}  ({when}{src})")
        if a["link"]:
            print(f"   {a['link']}")
        if a["summary"]:
            snippet = a["summary"].replace("\n", " ")
            print(f"   {snippet[:160]}{'…' if len(snippet) > 160 else ''}")
        print()


if __name__ == "__main__":
    main()

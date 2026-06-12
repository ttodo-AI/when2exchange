#!/usr/bin/env python3
"""Weekly economic calendar — upcoming USD/KRW-moving events with expected impact.

Searches recent Korean news for the week's scheduled macro events (FOMC, CPI,
고용, 금통위 등), has Claude extract them with a date, an expected FX-impact line,
and an importance score, then saves a calendar JSON the share page renders at the
bottom. D-Day itself is computed client-side so it stays current.

Hard rule: dates must be grounded in the news (no guessing).

Standalone script. Run directly:
    python executions/econ_calendar.py
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

QUERIES = [
    "이번 주 미국 경제지표 발표 일정 CPI 고용 FOMC 연준",
    "이번 주 한국은행 금통위 기준금리 발표 일정 환율",
    "주요 경제 일정 이번주 달러 원화 지표 발표 예정",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build this week's FX economic calendar.")
    p.add_argument("--per-query", type=int, default=12, help="News results per query (default 12).")
    p.add_argument("--out", default=None, help="Output JSON (default output/calendar-<ts>.json).")
    p.add_argument("--model", default="claude-sonnet-4-6", help="Claude model.")
    return p.parse_args()


def get(item, *keys):
    for key in keys:
        if isinstance(item, dict):
            if item.get(key) not in (None, ""):
                return item[key]
        else:
            v = getattr(item, key, None)
            if v not in (None, ""):
                return v
    return None


def search(client, query, limit):
    try:
        resp = client.search(query=query, limit=limit, sources=["news"], tbs="qdr:w")
    except TypeError:
        resp = client.search(query=query, limit=limit)
    news = getattr(resp, "news", None)
    if news is None and isinstance(resp, dict):
        news = resp.get("news") or resp.get("data")
    out = []
    for it in news or []:
        out.append({
            "title": get(it, "title", "name") or "",
            "link": get(it, "url", "link") or "",
            "snippet": get(it, "snippet", "description", "summary") or "",
            "date": get(it, "date", "published") or "",
        })
    return out


def main() -> None:
    args = parse_args()
    load_dotenv()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    fc_key = os.environ.get("FIRECRAWL_API_KEY")
    an_key = os.environ.get("ANTHROPIC_API_KEY")
    if not fc_key:
        sys.exit("error: FIRECRAWL_API_KEY is not set (.env).")
    if not an_key:
        sys.exit("error: ANTHROPIC_API_KEY is not set (.env).")

    try:
        from firecrawl import Firecrawl
    except ImportError:
        try:
            from firecrawl import FirecrawlApp as Firecrawl
        except ImportError:
            sys.exit("error: firecrawl-py not installed. pip install -r requirements.txt")
    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("error: anthropic not installed. pip install -r requirements.txt")

    fc = Firecrawl(api_key=fc_key)
    seen, articles = set(), []
    print("Searching economic-calendar news…", flush=True)
    for q in QUERIES:
        for a in search(fc, q, args.per_query):
            if a["link"] and a["link"] not in seen:
                seen.add(a["link"])
                articles.append(a)
    print(f"  collected {len(articles)} articles", flush=True)
    if not articles:
        sys.exit("error: no calendar news found.")

    digest = "\n".join(
        f"[{i}] ({a['date']}) {a['title']} :: {a['snippet'][:200]}"
        for i, a in enumerate(articles[:30])
    )
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst).strftime("%Y-%m-%d")

    prompt = (
        f"오늘은 {today}(한국시간)입니다. 아래 뉴스에서, 오늘부터 약 8일 이내에 "
        "예정된, 원/달러(USD/KRW) 환율에 영향이 큰 주요 경제 일정만 뽑아주세요.\n\n"
        f"[뉴스]\n{digest}\n\n"
        "■ 규칙:\n"
        "- 날짜·시각은 반드시 뉴스에 근거. 불확실하면 그 일정은 빼라(추측 금지).\n"
        "- 이미 지난 일정은 제외. 오늘~+8일 사이만.\n"
        "- 문장은 '~다' 문어체, 쉬운 말, 채움말 금지. 수치·고유명사는 뉴스 근거.\n"
        "- 각 일정 필드: date(YYYY-MM-DD), time(한국시간 'HH:MM' 또는 ''), "
        "name(한국어, 예: '미국 5월 소비자물가지수(CPI)'), importance(1~3 정수, 3=가장 중요), "
        "summary(접힌 상태에서 보일 영향 한 줄), why(왜 중요한지 2~3문장, 쉽게), "
        "scenarios(2개의 {cond, effect}: 예 '예상보다 높게 나오면'→환율이 어떻게 / '낮게 나오면'→어떻게. "
        "가능하면 뉴스 기반 환율 레벨 포함).\n"
        "- guide: 이번 주 환전러를 위한 실전 조언 2~3문장(언제·어떻게 환전하면 좋을지).\n\n"
        '아래 정확한 JSON만 출력(코드펜스 없이): {"guide":"...","events":[{"date":"YYYY-MM-DD",'
        '"time":"HH:MM","name":"...","importance":3,"summary":"...","why":"...",'
        '"scenarios":[{"cond":"...","effect":"..."},{"cond":"...","effect":"..."}]}]}'
    )

    print("Asking Claude to extract the week's events…", flush=True)
    client = Anthropic(api_key=an_key)
    try:
        resp = client.messages.create(
            model=args.model, max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        sys.exit(f"error: Claude call failed: {exc}")
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        sys.exit(f"error: could not parse JSON:\n{text[:400]}")
    try:
        result = json.loads(text[s:e + 1])
    except json.JSONDecodeError as exc:
        sys.exit(f"error: JSON parse failed ({exc}):\n{text[:400]}")

    events = []
    for ev in result.get("events", []):
        date = (ev.get("date") or "").strip()
        if not date or not ev.get("name"):
            continue
        try:
            imp = max(1, min(3, int(ev.get("importance"))))
        except (TypeError, ValueError):
            imp = 1
        scns = []
        for s in (ev.get("scenarios") or [])[:3]:
            if isinstance(s, dict) and (s.get("effect") or "").strip():
                scns.append({"cond": (s.get("cond") or "").strip(), "effect": s.get("effect").strip()})
        events.append({
            "date": date,
            "time": (ev.get("time") or "").strip(),
            "name": ev.get("name", "").strip(),
            "importance": imp,
            "summary": (ev.get("summary") or ev.get("impact") or "").strip(),
            "why": (ev.get("why") or "").strip(),
            "scenarios": scns,
        })
    events.sort(key=lambda x: x["date"])  # chronological for the table

    now = datetime.now(timezone.utc)
    payload = {
        "generated_at": now.isoformat(), "today_kst": today,
        "guide": (result.get("guide") or "").strip(), "events": events,
    }
    out_path = args.out or os.path.join("output", f"calendar-{now.strftime('%Y-%m-%d_%H%M')}.json")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(events)} event(s) to {out_path}")
    if payload["guide"]:
        print("가이드:", payload["guide"])
    for ev in events:
        print(f"  {ev['date']} {ev.get('time','')} [{'★'*ev['importance']}] {ev['name']} — {ev['summary']}")


if __name__ == "__main__":
    main()

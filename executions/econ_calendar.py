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


def fill_results(fc, client, events, today, model):
    """이미 발표된(date < today) 일정마다 결과를 '따로 검색'해 실제 결과를 채운다. 없으면 빈 채로(창작 금지)."""
    past = [e for e in events if (e.get("date") or "") < today]
    if not past:
        return
    print(f"Searching actual results for {len(past)} past event(s)…", flush=True)
    blocks = []
    for i, e in enumerate(past):
        arts = search(fc, f"{e['name']} 발표 결과 원/달러 환율", 6)
        dig = "\n".join(f"  - ({a['date']}) {a['title']} :: {a['snippet'][:160]}" for a in arts[:6]) or "  (검색 결과 없음)"
        blocks.append(f"[{i}] {e['name']} ({e['date']})\n{dig}")
    prompt = (
        "아래는 '이미 발표·종료된 경제 일정'과, 각각에 대해 검색한 최신 뉴스다.\n"
        "각 일정의 '실제 발표 결과(수치/방향)와 그 직후 원/달러 환율 반응'을 한 줄로 정리하라.\n"
        "■ 그라운딩 규칙(엄수):\n"
        "- 검색된 그 일정의 뉴스에 '명시적으로' 나온 결과만 적는다. 없거나 애매하면 빈 문자열(추측·창작 절대 금지).\n"
        "- 수치(%, 지수, 환율 등)는 기사에 적힌 값과 정확히 일치해야 한다. 기사와 다르거나 확신이 없으면 그 항목은 빈 문자열로 둔다.\n"
        "- 작성한 뒤, 각 결과가 정말 그 일정의 검색 기사에 있는지 스스로 한 번 더 검증하라. 근거가 불충분하면 비운다.\n"
        "- '~다' 문어체, 수치·고유명사는 뉴스 근거.\n\n"
        + "\n\n".join(blocks)
        + '\n\nJSON만(코드펜스 없이): {"results":[{"i":0,"result":"..."}]}'
    )
    try:
        resp = client.messages.create(model=model, max_tokens=1000,
                                      messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        s, e = text.find("{"), text.rfind("}")
        parsed = json.loads(text[s:e + 1]) if s != -1 and e != -1 else {}
    except Exception as exc:
        print(f"  result search failed: {exc}", file=sys.stderr)
        return
    for r in parsed.get("results", []):
        try:
            idx = int(r.get("i"))
        except (TypeError, ValueError):
            continue
        res = (r.get("result") or "").strip()
        if 0 <= idx < len(past) and res:
            past[idx]["result"] = res
            print(f"  ✓ {past[idx]['name']}: {res[:50]}")
    filled = sum(1 for e in past if (e.get("result") or "").strip())
    print(f"  결과 채움 {filled}/{len(past)} (근거 부족분은 비움)")


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
        f"오늘은 {today}(한국시간)입니다. 아래 뉴스에서, 이번 주 월요일부터 다음 주 일요일까지"
        "(약 -7일 ~ +12일) 사이의, 원/달러(USD/KRW) 환율에 영향이 큰 주요 경제 일정을 뽑아주세요.\n\n"
        f"[뉴스]\n{digest}\n\n"
        "■ 규칙:\n"
        "- 날짜·시각은 반드시 뉴스에 근거. 불확실하면 그 일정은 빼라(추측 금지).\n"
        "- 이번 주에 이미 발표·종료된 일정도 포함한다. 그 경우 result에 '실제 결과와 환율 영향'을 "
        "한 줄로(뉴스에 결과가 나온 경우만, 없으면 빈 문자열, 추측 금지).\n"
        "- 아직 예정인 일정은 result는 빈 문자열로 두고 scenarios(예상 시나리오)를 채운다.\n"
        "- 문장은 '~다' 문어체, 쉬운 말, 채움말 금지. 수치·고유명사는 뉴스 근거.\n"
        "- 각 일정 필드: date(YYYY-MM-DD), time(한국시간 'HH:MM' 또는 ''), "
        "name(한국어, 예: '미국 5월 소비자물가지수(CPI)'), importance(1~3 정수, 3=가장 중요), "
        "summary(접힌 상태에서 보일 영향 한 줄), "
        "why(펼쳤을 때 맨 위에 올 '중심 내용' 한 문장. 이 일정이 환율에 왜 중요한지 핵심만 쉽게. "
        "두루뭉술 금지), "
        "result(이미 발표된 일정의 '실제 결과+환율 영향' 한 줄, 아니면 ''), "
        "scenarios(예정 일정만, 2개의 {cond, effect}: 예 '예상보다 높게 나오면'→환율 어떻게 / '낮게 나오면'→어떻게).\n"
        "- guide: 다가오는 가장 큰 변수를 정확한 시점(이번 주/다음 주)과 함께 짚고 환전러 실전 조언 2~3문장. "
        "다음 주 일정을 '이번 주'라고 하지 말 것.\n\n"
        '아래 정확한 JSON만 출력(코드펜스 없이): {"guide":"...","events":[{"date":"YYYY-MM-DD",'
        '"time":"HH:MM","name":"...","importance":3,"summary":"...","why":"...","result":"",'
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
            "result": (ev.get("result") or "").strip(),
            "scenarios": scns,
        })
    events.sort(key=lambda x: x["date"])  # chronological for the table
    fill_results(fc, client, events, today, args.model)   # 지난 일정: 결과 전용 검색으로 채움(창작 금지)

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

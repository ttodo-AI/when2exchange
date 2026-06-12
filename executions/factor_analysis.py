#!/usr/bin/env python3
"""Factor analysis — find the day's Top 4 USD/KRW drivers, with specific bullets.

For 10 fixed exchange-rate factors, searches recent Korean news (Firecrawl), then
asks Claude to pick the 4 that actually moved USD/KRW today, write 4 specific,
sourced bullets per factor, and a concrete one/two-sentence overall "why".

Hard rule (per user): logic/accuracy/specificity first — NO vague filler.

Standalone script. Run directly:
    python executions/factor_analysis.py
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

# Fixed 10 drivers of USD/KRW. Each has a Korean news query tuned for FX relevance.
FACTORS = [
    {"id": "F1", "emoji": "🇺🇸", "name": "미국 통화정책", "query": "연준 기준금리 FOMC 환율 달러"},
    {"id": "F2", "emoji": "🇰🇷", "name": "한국 통화정책", "query": "한국은행 기준금리 금통위 원화 환율"},
    {"id": "F3", "emoji": "📊", "name": "미국 경제지표", "query": "미국 CPI 고용 경제지표 달러 강세"},
    {"id": "F4", "emoji": "🏭", "name": "한국 경제·수출", "query": "한국 수출 경상수지 반도체 원화"},
    {"id": "F5", "emoji": "💵", "name": "달러 강세(달러인덱스)", "query": "달러인덱스 달러 강세 원달러 환율"},
    {"id": "F6", "emoji": "🌏", "name": "아시아 통화 동조", "query": "위안화 엔화 환율 원화 아시아 통화"},
    {"id": "F7", "emoji": "📈", "name": "외국인 증시 수급", "query": "외국인 코스피 순매도 자금유출 환율"},
    {"id": "F8", "emoji": "⚔️", "name": "지정학 리스크", "query": "중동 지정학 리스크 안전자산 환율"},
    {"id": "F9", "emoji": "🛢️", "name": "국제유가·원자재", "query": "국제유가 유가 환율 원자재 무역수지"},
    {"id": "F10", "emoji": "🏛️", "name": "무역·관세·당국개입", "query": "관세 미중 무역 외환당국 개입 환율"},
]


# Style rules for the "왜 그럴까요" summary (overall_why). Used by the full run
# and by --rewrite-why so the two stay identical.
WHY_RULES = (
    "4개 요인을 종합해 '오늘 원/달러 환율이 왜 이런지'를 대학생(비전문가)도 바로 "
    "이해되는 쉬운 말로 설명하되, 다음을 엄격히 지킬 것: "
    "(1) 최대 2~3문장. 짧고 밀도 높게. "
    "(2) '쉽게 말하면 / 한마디로 / 결론적으로' 같은 군더더기 도입부 금지. "
    "'이유가 네 군데서 동시에 터졌다'처럼 뻔하고 내용 없는 문장 금지 — "
    "첫 문장부터 가장 큰 원인으로 곧장 들어갈 것. "
    "(3) '터졌다 / 폭발했다 / 쏟아냈다' 같은 저급·과장 구어 금지. 담백하고 정확하게. "
    "(4) 핵심 수치(예: 17만2000명, 66조원)는 유지하고 전문용어(연준·DXY 등)는 괄호로 "
    "짧게 풀이. (5) 채움말·추측 금지, 뉴스 사실에만 근거. "
    "(6) 강조는 정말 핵심에만. 독자가 딱 하나만 기억한다면 그것에 해당하는 "
    "**가장 결정적인 구절 1~2곳만** 별표 두 개로 감쌀 것. 단어 하나·수치 하나를 "
    "일일이 감싸지 말고('달러화', '미·이란' 같은 짧은 조각 금지), 의미가 통하는 "
    "짧은 구절로. 과용은 절대 금지(많아야 2곳). "
    "(7) 환율 수치는 자료에서 **가장 최근 시점**의 값을 쓸 것. 여러 날짜의 수치가 "
    "섞여 있으면 며칠 전 값(예: 과거 개장가 1530원)을 '최고치'처럼 쓰지 말고, 가장 "
    "최신 값(예: 오늘 개장가·장중 고가)을 반영. 장중 최고가와 현재가는 구분해서 표현. "
    "(8) 인과관계는 중간 고리를 건너뛰지 말 것. 예: '고용 호조 → 달러 강세'(X). "
    "'고용이 강하게 나오자 → 연준의 금리 인하 기대가 줄고(혹은 인상 우려가 커지고) → "
    "미국 금리가 올라 → 그 이자를 좇아 달러 수요가 늘어 → 달러가 강해졌다'처럼 핵심 "
    "연결고리를 반드시 넣되, 장황하지 않게. "
    "(9) 모든 문장은 '~다/~했다'로 끝나는 담백한 문어체 평서문으로 일관. "
    "해요체(~요/~예요)·반말·과격식(~습니다) 금지."
)

# Shared TL;DR rule (full run + --rewrite-why use the same wording).
TLDR_RULES = (
    "오늘 상황을 비전문가(대학생)도 이해할 핵심만 3줄. 각 한 문장, 쉬운 말, 채움말 금지. "
    "순서는 '환율이 어떻게 됐는지 → 가장 큰 이유 → 실용 조언'. "
    "첫 줄(환율)은 '얼마를 넘어서 얼마로 마감/개장했는지'처럼 돌파한 기준선과 그 시점의 "
    "수준·시점을 명확히('기록하며' 같은 모호한 표현 금지). 환율 수치는 자료의 가장 최근 "
    "값을 쓰고(며칠 전 값을 최신처럼 쓰지 말 것), 장중 고가/현재가를 구분. "
    "인과관계는 중간 고리를 생략하지 말 것(예: 고용 호조 → 금리 기대 변화 → 달러 강세). "
    "문장은 '~다/~했다'로 끝나는 담백한 문어체 평서문으로 일관(해요체·반말 금지)."
)

# 지난 브리핑 목록에 쓰는 짧은 후크 제목. 다체가 아니라 클릭을 부르는 캐주얼 존댓말.
CARD_TITLE_RULES = (
    "환율 뉴스를 안 챙기는 사람도 눌러보고 싶게, 공감·놀람·궁금증을 자극하는 짧은 후크 한 줄. "
    "한국어 16자 이내(짧을수록 좋다, 길면 잘림). 캐주얼한 존댓말(해요체)이나 질문형 환영. "
    "오늘의 핵심 사실에 근거(과장·거짓·낚시 금지), 수치 1개 정도는 넣어도 좋음. "
    "이모지·따옴표 없이 글자만. 예: '환율 또 1530 넘었어요', '지금 환전 잠깐만요', "
    "'달러 더 비싸졌어요'."
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Find the day's Top 4 USD/KRW drivers.")
    p.add_argument("--per-factor", type=int, default=15, help="News results per factor (default 15).")
    p.add_argument("--out", default=None, help="Output JSON (default output/factors-<ts>.json).")
    p.add_argument("--model", default="claude-sonnet-4-6",
                   help="Claude model (default claude-sonnet-4-6 — judgment task).")
    p.add_argument("--rewrite-why", action="store_true",
                   help="Reuse the latest factors-*.json and regenerate ONLY overall_why (no new search).")
    return p.parse_args()


def get(item, *keys):
    for key in keys:
        if isinstance(item, dict):
            if item.get(key) not in (None, ""):
                return item[key]
        else:
            val = getattr(item, key, None)
            if val not in (None, ""):
                return val
    return None


def search_factor(client, query: str, limit: int):
    """Firecrawl news search → list of {title, link, snippet, date}."""
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


def latest_rate_context() -> str:
    files = glob.glob(os.path.join("output", "krw-exchange-rate-*.json"))
    if not files:
        return "(현재 환율 데이터 없음)"
    try:
        d = json.load(open(max(files, key=os.path.getmtime), encoding="utf-8"))
        v = d.get("timing_verdict") or {}
        return f"판정 {v.get('label','?')} · {(' '.join((v.get('text','') or '').split()))[:200]}"
    except (OSError, json.JSONDecodeError):
        return "(현재 환율 데이터 없음)"


def rewrite_why(client, model: str) -> None:
    """Reuse the latest factors file and regenerate overall_why + tldr (cheap, no search)."""
    files = glob.glob(os.path.join("output", "factors-*.json"))
    if not files:
        sys.exit("error: no factors-*.json found. Run a full factor_analysis first.")
    path = max(files, key=os.path.getmtime)
    data = json.load(open(path, encoding="utf-8"))
    facs = data.get("factors", [])
    digest = "\n\n".join(
        f"[{f.get('emoji','')} {f.get('name','')}] {f.get('headline','')}\n- "
        + "\n- ".join(f.get("bullets", []))
        for f in facs
    )
    prompt = (
        "아래는 오늘 원/달러 환율을 움직인 Top4 요인과 핵심 사실입니다.\n\n"
        f"{digest}\n\n"
        f"■ overall_why 작성 규칙: {WHY_RULES}\n\n"
        f"■ tldr 작성 규칙: {TLDR_RULES}\n\n"
        f"■ card_title 작성 규칙: {CARD_TITLE_RULES}\n\n"
        'JSON만 출력(코드펜스 없이): {"card_title":"...","overall_why":"...","tldr":["문장","문장","문장"]}'
    )
    resp = client.messages.create(
        model=model, max_tokens=1200, messages=[{"role": "user", "content": prompt}]
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    s, e = text.find("{"), text.rfind("}")
    parsed = json.loads(text[s:e + 1]) if s != -1 and e != -1 else {}
    why = parsed.get("overall_why", "")
    tldr = [t for t in (parsed.get("tldr") or []) if t][:3]
    if not why:
        sys.exit("error: rewrite produced empty overall_why.")
    data["overall_why"] = why
    if parsed.get("card_title"):
        data["card_title"] = parsed["card_title"].strip()
    if tldr:
        data["tldr"] = tldr
    now = datetime.now(timezone.utc)
    out = os.path.join("output", f"factors-{now.strftime('%Y-%m-%d_%H%M')}.json")
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Rewrote overall_why + tldr (reused {os.path.basename(path)}) -> {out}\n")
    for t in tldr:
        print(f"  · {t}")
    print(f"\n{why}")


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

    if args.rewrite_why:  # cheap path: reuse latest factors, reword only the summary
        rewrite_why(Anthropic(api_key=an_key), args.model)
        return

    fc = Firecrawl(api_key=fc_key)

    # 1) Search all 10 factors; build a globally-indexed article list.
    all_articles = []          # global idx -> article (+factor id)
    by_factor = {}             # factor id -> [global idx,...]
    print("Searching 10 factors…", flush=True)
    for f in FACTORS:
        arts = search_factor(fc, f["query"], args.per_factor)
        idxs = []
        for a in arts[: args.per_factor]:
            gi = len(all_articles)
            a["factor"] = f["id"]
            all_articles.append(a)
            idxs.append(gi)
        by_factor[f["id"]] = idxs
        print(f"  {f['id']:>3} {f['name']}: {len(idxs)} articles", flush=True)

    if not all_articles:
        sys.exit("error: no news found for any factor.")

    # 2) Build the prompt digest (cap snippet length to control tokens).
    lines = []
    for f in FACTORS:
        lines.append(f"=== [{f['id']}] {f['name']} ===")
        if not by_factor[f["id"]]:
            lines.append("(관련 기사 없음)")
        for gi in by_factor[f["id"]][:12]:
            a = all_articles[gi]
            lines.append(f"[{gi}] {a['title']} :: {a['snippet'][:200]}")
    digest = "\n".join(lines)

    prompt = (
        "당신은 원/달러(USD/KRW) 환율 애널리스트입니다. 아래 10개 '환율 영향 요인'별로 "
        "최근 뉴스를 모았습니다. 오늘 원/달러에 실제로 가장 크게 영향을 준 요인 4개를 고르세요.\n\n"
        f"[현재 환율 맥락]\n{latest_rate_context()}\n\n"
        f"[요인별 뉴스]\n{digest}\n\n"
        "■ 작성 원칙 (가장 중요):\n"
        "- 논리·정확도·구체성이 최우선. 두루뭉술한 채움말 절대 금지"
        "('여러 요인이 겹쳐', '복합적 요인', '대내외 불확실성', '크게 영향' 같은 알맹이 없는 말).\n"
        "- 각 불렛은 뉴스에 실제로 나온 구체적 사실(수치·기관명·국가·지표·날짜)을 담아 "
        "'무엇이 → 어떤 경로로 → 환율에 어떻게'를 인과로 설명.\n"
        "- 뉴스에 근거 없는 추측·일반론 금지. 근거 기사 번호를 반드시 표기.\n"
        "- 모든 문장(headline·bullets·impact_reason·tldr·overall_why)은 '~다/~했다'로 "
        "끝나는 담백한 문어체 평서문으로 일관되게. 해요체(~요/~예요)·반말·과한 격식"
        "(~습니다)을 섞지 말 것.\n\n"
        "선정한 요인 4개 각각에 대해: impact(1~5 정수, 오늘 환율을 움직인 영향력. "
        "5=가장 결정적인 주범. 4개는 반드시 서로 차등을 둘 것), "
        "impact_reason(그 영향도 점수를 매긴 근거 한 줄. 예: '복수 매체가 오늘 환율 "
        "상승의 1순위 원인으로 지목', '방향엔 영향을 줬으나 보조적 요인'), "
        "headline(오늘 환율 영향 한 줄, 구체적), "
        "bullets(가장 중요한 핵심 사실 2개, 각 1문장·구체적·짧게), source_ids(근거 기사 번호 3~5개).\n"
        f"tldr: {TLDR_RULES}\n"
        f"그리고 overall_why: {WHY_RULES}\n"
        f"그리고 card_title: {CARD_TITLE_RULES}\n\n"
        "아래 정확한 JSON만 출력(코드펜스 없이):\n"
        '{"card_title":"...","tldr":["문장","문장","문장"],'
        '"factors":[{"factor_id":"F1","impact":5,"impact_reason":"...","headline":"...",'
        '"bullets":["..",".."],"source_ids":[0,3,7]}, ...총 4개], "overall_why":"..."}'
    )

    print("Asking Claude to rank Top 4 + summarize…", flush=True)
    client = Anthropic(api_key=an_key)
    try:
        resp = client.messages.create(
            model=args.model, max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        sys.exit(f"error: Claude call failed: {exc}")
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        sys.exit(f"error: could not parse JSON:\n{text[:500]}")
    try:
        result = json.loads(text[s:e + 1])
    except json.JSONDecodeError as exc:
        sys.exit(f"error: JSON parse failed ({exc}):\n{text[:500]}")

    # 3) Resolve factor meta + source ids -> {title, link}, dedup sources.
    meta = {f["id"]: f for f in FACTORS}
    factors_out = []
    for fr in result.get("factors", [])[:4]:
        fid = fr.get("factor_id")
        m = meta.get(fid, {"name": fid or "?", "emoji": "•"})
        seen, sources = set(), []
        for gi in fr.get("source_ids", [])[:6]:
            if isinstance(gi, int) and 0 <= gi < len(all_articles):
                a = all_articles[gi]
                if a["link"] and a["link"] not in seen:
                    seen.add(a["link"])
                    sources.append({"title": a["title"], "link": a["link"]})
        try:
            impact = max(1, min(5, int(fr.get("impact"))))
        except (TypeError, ValueError):
            impact = 3
        factors_out.append({
            "name": m["name"],
            "emoji": m["emoji"],
            "impact": impact,
            "impact_reason": (fr.get("impact_reason") or "").strip(),
            "headline": fr.get("headline", ""),
            "bullets": [b for b in fr.get("bullets", []) if b][:2],
            "sources": sources[:5],
        })
    # Show the most influential factor first.
    factors_out.sort(key=lambda x: x["impact"], reverse=True)

    tldr = [t for t in (result.get("tldr") or []) if t][:3]

    now = datetime.now(timezone.utc)
    payload = {
        "generated_at": now.isoformat(),
        "card_title": (result.get("card_title") or "").strip(),
        "tldr": tldr,
        "overall_why": result.get("overall_why", ""),
        "factors": factors_out,
    }
    out_path = args.out or os.path.join("output", f"factors-{now.strftime('%Y-%m-%d_%H%M')}.json")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(factors_out)} top factors to {out_path}\n")
    if tldr:
        print("3줄 요약:")
        for t in tldr:
            print(f"  · {t}")
        print()
    print("종합:", payload["overall_why"], "\n")
    for i, f in enumerate(factors_out, 1):
        print(f"{i}. [{'🔥'*f['impact']}] {f['emoji']} {f['name']} — {f['headline']}")
        for b in f["bullets"]:
            print(f"   • {b}")
        print(f"   출처 {len(f['sources'])}개")
        print()


if __name__ == "__main__":
    main()

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
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET

from dotenv import load_dotenv

# Free news search (Google News RSS, no API key). Standard library only.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_RSS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

QUERIES = [
    "이번 주 미국 경제지표 발표 일정 CPI 고용 FOMC 연준",
    "이번 주 한국은행 금통위 기준금리 발표 일정 환율",
    "주요 경제 일정 이번주 달러 원화 지표 발표 예정",
]

# ── 이벤트 날짜 검증 (반복되는 미 지표 날짜오류 방지) ──────────────────────
# LLM이 CPI를 7/11(토·발표불가)로, FOMC 요일을 (수)로 자주 틀린다. data/event_schedule.json
# (웹검증한 실제 날짜)을 ① 프롬프트에 확정날짜로 주입 ② 생성 후 대조·자동보정한다.
_WD_KO = "월화수목금토일"     # weekday(): 0=월 … 6=일
_BACKBONE_KW = [
    (re.compile(r"소비자물가|CPI"), "CPI"),
    (re.compile(r"고용보고서|비농업|고용지표"), "고용"),
    (re.compile(r"PCE|개인소비지출"), "PCE"),
    (re.compile(r"FOMC.*(금리\s*결정|정례회의|금리결정)"), "FOMC"),   # '의사록'은 결정과 다른 날 → 제외
]
_US_INDICATOR = re.compile(r"소비자물가|CPI|고용보고서|비농업|PCE|개인소비지출|FOMC")


# ── LLM 엔진: Anthropic 1차 → Gemini(무료 티어) 2차 ────────────────────────────
# 2026-08-14~09-02: Anthropic 크레딧이 떨어지자 full 분석이 20일간 멈췄다(light만 성공해
# 겉보기엔 정상이라 늦게 발견). 2차 엔진을 두어 크레딧이 없어도 파이프라인이 계속 돈다.
# 어느 엔진이 쓰든 출력은 똑같이 validate_briefing·build_site.gate를 통과해야 배포되므로
# 검증 기준은 내려가지 않는다. Anthropic 클라이언트와 같은 .messages.create(...) 모양이라
# 호출부(4곳)는 고치지 않는다.
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_CANDIDATES = ["gemini-3-flash", "gemini-2.5-flash", "gemini-flash-latest"]


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, text):
        self.content = [_Block(text)]


class LLM:
    """1차 Anthropic, 2차 Gemini. engine 속성에 마지막으로 성공한 엔진 이름이 남는다."""

    def __init__(self, an_key=None, gem_key=None):
        self.gem_key = gem_key or os.environ.get("GEMINI_API_KEY") or ""
        self.gem_model = os.environ.get("GEMINI_MODEL") or ""
        self.engine = ""
        self._anthropic = None
        self.messages = self          # client.messages.create(...) 호환용
        if an_key:
            try:
                from anthropic import Anthropic
                self._anthropic = Anthropic(api_key=an_key)
            except ImportError:
                print("  anthropic 미설치 — Gemini만 사용", file=sys.stderr)

    def create(self, model=None, max_tokens=1024, messages=None):
        last = None
        if self._anthropic is not None:
            try:
                r = self._anthropic.messages.create(
                    model=model, max_tokens=max_tokens, messages=messages)
                self.engine = "claude"
                return r
            except Exception as exc:
                last = exc
                print("  ⚠ Claude 실패(%s) — Gemini로 폴백" % str(exc)[:120],
                      file=sys.stderr, flush=True)
        if self.gem_key:
            prompt = "\n\n".join((m.get("content") or "") for m in (messages or []))
            try:
                text = self._gemini(prompt, max_tokens)
                self.engine = "gemini"
                return _Response(text)
            except Exception as exc:
                last = exc
                print("  ⚠ Gemini 실패(%s)" % str(exc)[:160], file=sys.stderr, flush=True)
        raise RuntimeError("모든 LLM 엔진 실패: %s" % last)

    def _gemini(self, prompt, max_tokens):
        """OpenAI 호환 엔드포인트로 호출. 모델명은 자주 바뀌므로 후보를 순서대로 시도하고,
        다 막히면 사용 가능한 flash 모델을 조회해 한 번 더 시도한다(무료 티어=flash만)."""
        err = None
        for name in ([self.gem_model] if self.gem_model else list(GEMINI_CANDIDATES)):
            try:
                out = self._gemini_call(name, prompt, max_tokens)
                self.gem_model = name
                return out
            except Exception as exc:
                err = exc
        for name in self._gemini_discover():
            try:
                out = self._gemini_call(name, prompt, max_tokens)
                self.gem_model = name
                print("  Gemini 모델 자동선택: %s" % name, file=sys.stderr)
                return out
            except Exception as exc:
                err = exc
        raise RuntimeError(err)

    def _gemini_call(self, name, prompt, max_tokens):
        body = json.dumps({
            "model": name,
            "messages": [{"role": "user", "content": prompt}],
            # 사고(thinking) 토큰이 출력 한도를 먹을 수 있어 여유를 둔다.
            "max_tokens": min(int(max_tokens) * 2, 8192),
        }).encode("utf-8")
        req = urllib.request.Request(
            GEMINI_BASE + "/openai/chat/completions", data=body,
            headers={"Authorization": "Bearer " + self.gem_key,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            j = json.loads(r.read().decode("utf-8"))
        return ((j.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()

    def _gemini_discover(self):
        try:
            url = GEMINI_BASE + "/models?key=" + urllib.parse.quote(self.gem_key)
            with urllib.request.urlopen(url, timeout=30) as r:
                j = json.loads(r.read().decode("utf-8"))
        except Exception:
            return []
        names = []
        for m in j.get("models") or []:
            nm = (m.get("name") or "").split("/")[-1]
            if "flash" in nm and "generateContent" in (m.get("supportedGenerationMethods") or []):
                names.append(nm)
        names.sort(reverse=True)      # 버전이 큰(최신) 이름 우선
        return names[:3]


def _load_backbone():
    p = os.path.join(os.path.dirname(__file__), "..", "data", "event_schedule.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh).get("events", [])
    except Exception:
        return []


def _iso(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def confirmed_dates_block(backbone, today_str):
    """검증 일정표 중 대상 창(-10~+20일)에 드는 확정 일정을 프롬프트용 문자열로."""
    td = _iso(today_str)
    if not (backbone and td):
        return ""
    rows = []
    for be in backbone:
        d = _iso(be.get("date", ""))
        if d and -10 <= (d - td).days <= 20:
            rows.append(f"- {be.get('name','')} = {be['date']}({_WD_KO[d.weekday()]})")
    if not rows:
        return ""
    return ("\n■ 공식 확정 날짜(아래 지표는 이 날짜를 그대로 사용. 뉴스가 달라도 이걸 우선):\n"
            + "\n".join(rows) + "\n")


def verify_fix_dates(events, backbone, guide, today_str):
    """생성된 이벤트 날짜를 검증 일정표와 대조: 다르면 자동 보정, 주말발표·요일라벨은 경고.
    (fixes, warns) 반환. events는 제자리 수정."""
    fixes, warns = [], []
    for e in events:
        d = _iso(e.get("date", ""))
        if not d:
            continue
        if _US_INDICATOR.search(e.get("name", "")) and d.weekday() >= 5:
            warns.append(f"'{e['name']}' {e['date']}={_WD_KO[d.weekday()]} — 미 지표 주말발표 없음(오류 의심)")
        for pat, kw in _BACKBONE_KW:
            if pat.search(e.get("name", "")):
                for be in backbone:
                    if be.get("kw") == kw and be.get("date", "")[:7] == e["date"][:7] \
                            and be["date"] != e["date"]:
                        fixes.append(f"{e['name']}: {e['date']} → {be['date']}(일정표 확정)")
                        e["date"] = be["date"]
                break
    for m in re.finditer(r"(\d{1,2})월\s*(\d{1,2})일\s*\(([월화수목금토일])\)", guide or ""):
        mm, dd, lbl = int(m.group(1)), int(m.group(2)), m.group(3)
        td = _iso(today_str)
        try:
            real = _WD_KO[datetime(td.year if td else 2026, mm, dd).weekday()]
        except ValueError:
            continue
        if real != lbl:
            warns.append(f"guide '{mm}월 {dd}일({lbl})' — 실제 {real}요일(요일 라벨 오류)")
    return fixes, warns


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


def _strip_html(s: str) -> str:
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def search(query, limit):
    """Google News RSS search (free, no key) → list of {title, link, snippet, date}.

    `when:7d` ~ the Firecrawl version's qdr:w (past week of news)."""
    url = _RSS.format(q=urllib.parse.quote(f"{query} when:7d"))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                   "Accept-Language": "ko,en;q=0.8"})
        with urllib.request.urlopen(req, timeout=12) as r:
            xml = r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
        root = ET.fromstring(xml)
    except Exception:
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")].strip()
        out.append({
            "title": title,
            "link": (it.findtext("link") or "").strip(),
            "snippet": _strip_html(it.findtext("description") or ""),
            "date": (it.findtext("pubDate") or "").strip(),
        })
        if len(out) >= limit:
            break
    return out


def fill_results(client, events, today, model):
    """이미 발표된(date < today) 일정마다 결과를 '따로 검색'해 실제 결과를 채운다. 없으면 빈 채로(창작 금지)."""
    past = [e for e in events if (e.get("date") or "") < today]
    if not past:
        return
    print(f"Searching actual results for {len(past)} past event(s)…", flush=True)
    blocks = []
    for i, e in enumerate(past):
        arts = search(f"{e['name']} 발표 결과 원/달러 환율", 6)
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
        s = text.find("{")
        parsed = json.JSONDecoder().raw_decode(text[s:])[0] if s != -1 else {}
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

    an_key = os.environ.get("ANTHROPIC_API_KEY")
    # 둘 중 하나만 있어도 돈다 — Anthropic 크레딧이 떨어져도 Gemini가 받는다.
    if not an_key and not os.environ.get("GEMINI_API_KEY"):
        sys.exit("error: ANTHROPIC_API_KEY도 GEMINI_API_KEY도 없다 (.env).")

    seen, articles = set(), []
    print("Searching economic-calendar news (Google News, free)…", flush=True)
    for q in QUERIES:
        for a in search(q, args.per_query):
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

    backbone = _load_backbone()
    confirmed = confirmed_dates_block(backbone, today)

    prompt = (
        f"오늘은 {today}(한국시간)입니다. 아래 뉴스에서, 이번 주 월요일부터 다음 주 일요일까지"
        "(약 -7일 ~ +12일) 사이의, 원/달러(USD/KRW) 환율에 영향이 큰 주요 경제 일정을 뽑아주세요.\n\n"
        f"[뉴스]\n{digest}\n"
        f"{confirmed}\n"
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

    print("Asking LLM to extract the week's events…", flush=True)
    client = LLM(an_key=an_key)
    try:
        resp = client.messages.create(
            model=args.model, max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        sys.exit(f"error: LLM call failed: {exc}")
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    s = text.find("{")
    if s == -1:
        sys.exit(f"error: could not parse JSON:\n{text[:400]}")
    try:
        # raw_decode: 첫 JSON 객체만 파싱(모델이 뒤에 설명·둘째 객체를 붙여도 'Extra data' 안 남).
        result, _ = json.JSONDecoder().raw_decode(text[s:])
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

    # 이벤트 날짜 자동 검증·보정 — 검증 일정표 대조(자동보정) + 주말발표·요일라벨 경고
    fixes, date_warns = verify_fix_dates(events, backbone, result.get("guide") or "", today)
    for f in fixes:
        print("  [날짜보정]", f, file=sys.stderr)
    for w in date_warns:
        print("  [날짜경고]", w, file=sys.stderr)
    if fixes:
        events.sort(key=lambda x: x["date"])   # 보정 후 재정렬

    fill_results(client, events, today, args.model)   # 지난 일정: 결과 전용 검색으로 채움(창작 금지)

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

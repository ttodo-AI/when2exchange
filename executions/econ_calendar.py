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
# 사고 깊이. Sonnet 5는 thinking이 기본 on이고 사고 토큰이 출력으로 과금돼, 기본값(high)이면
# 출력이 약 2배가 된다(2026-09-03 실측). ANTHROPIC_EFFORT로 덮어쓸 수 있다.
EFFORT = os.environ.get("ANTHROPIC_EFFORT") or "low"
# effort를 받지 않는 모델(haiku-4-5, sonnet-4-5 등)에 보내면 400이라 이름으로 거른다.
_NO_EFFORT = ("haiku", "sonnet-4-5", "sonnet-4-6", "opus-4-5")


def _supports_effort(model):
    m = (model or "").lower()
    return bool(m) and not any(k in m for k in _NO_EFFORT)


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
                # effort=low: Sonnet 5는 thinking이 기본 on이라 사고 토큰이 출력으로 과금된다
                # (2026-09-03 실측: 출력이 약 2배). 사고는 남기되 깊이만 낮춰 비용을 절반쯤 줄인다.
                # effort를 모르는 모델(haiku-4-5 등)은 400이 나므로 빼고 다시 부른다.
                kw = {"model": model, "max_tokens": max_tokens, "messages": messages}
                if _supports_effort(model):
                    kw["output_config"] = {"effort": EFFORT}
                try:
                    r = self._anthropic.messages.create(**kw)
                except TypeError:                      # 구버전 SDK: output_config 미지원
                    kw.pop("output_config", None)
                    r = self._anthropic.messages.create(**kw)
                except Exception as exc:
                    if "output_config" not in kw or "effort" not in str(exc):
                        raise
                    kw.pop("output_config")
                    print("  ⚠ effort 미지원 모델(%s) — 빼고 재시도" % model, file=sys.stderr)
                    r = self._anthropic.messages.create(**kw)
                self.engine = "claude"
                # Sonnet 5 등 thinking 기본 on 모델은 사고 토큰이 max_tokens를 함께 쓴다.
                # 한도가 모자라면 text 블록 없이 끝나(빈 응답) 파싱이 실패하므로 원인을 남긴다.
                u = getattr(r, "usage", None)
                if u is not None:
                    print("  [usage] in=%s out=%s stop=%s" % (
                        getattr(u, "input_tokens", "?"), getattr(u, "output_tokens", "?"),
                        getattr(r, "stop_reason", "?")), file=sys.stderr)
                if not any(getattr(b, "type", None) == "text" for b in (r.content or [])):
                    print("  ⚠ 본문(text) 없음 — stop_reason=%s. max_tokens(%s)가 사고+출력에 부족할 수 있다."
                          % (getattr(r, "stop_reason", "?"), max_tokens), file=sys.stderr)
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
            "max_tokens": min(int(max_tokens) * 2, 16384),
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


# ── 유령 일정 방어 (A: 근거 필수 · C: 연설류 엄격 · D: 미국시간→KST) ──────────
# 2026-09-03: 근거 기사 0건인 '월러 연준 이사 연설'이 캘린더에 실렸다. 요인(factor_analysis)은
# 이미 source_ids를 요구하고 '근거 0건 요인'을 환각으로 차단하는데, 캘린더에만 그 방어가 없었다.
# 같은 규칙을 이식한다. 캘린더는 배포를 막는 대신 '그 일정만 드롭'이 안전하다 —
# 페이지는 계속 살고 거짓만 빠진다.
_SPEECH_RE = re.compile(r"(연설|발언|증언|기자회견|간담회|브리핑)")


def _us_dst(d):
    """미국 서머타임(EDT) 여부 — 3월 둘째 일요일 ~ 11월 첫째 일요일."""
    mar1 = datetime(d.year, 3, 1).date()
    second_sun = mar1 + timedelta(days=((6 - mar1.weekday()) % 7) + 7)
    nov1 = datetime(d.year, 11, 1).date()
    first_sun = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    return second_sun <= d < first_sun


def kst_from_et(et_date, et_time):
    """미국 현지(ET) 발표 일시 → 한국 (날짜, 'HH:MM'). EDT=+13h, EST=+14h. 못 읽으면 None.
    미 8:30am ET 지표는 한국 당일 밤(같은 날짜), 오후 ET 일정(FOMC 2pm·연설 등)은 한국 다음날."""
    d = _iso((et_date or "").strip())
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", (et_time or "").strip())
    if not d or not m:
        return None
    shift = 13 if _us_dst(d) else 14
    dt = datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2))) + timedelta(hours=shift)
    return dt.date().isoformat(), dt.strftime("%H:%M")


def _backbone_confirmed(ev, backbone):
    """확정 일정표(백본)에 있는 정기 지표인가 — 있으면 뉴스 근거가 없어도 신뢰한다."""
    for pat, kw in _BACKBONE_KW:
        if pat.search(ev.get("name", "")):
            return any(be.get("kw") == kw and be.get("date", "")[:7] == ev.get("date", "")[:7]
                       for be in backbone)
    return False


def _date_mentioned(datestr, sources):
    """근거 기사(제목+요약)에 그 날짜가 실제로 적혀 있나 — '9월 4일' / '9/4' / '2026-09-04'."""
    d = _iso(datestr)
    if not d:
        return False
    pats = [re.compile(r"%d\s*월\s*%d\s*일" % (d.month, d.day)),
            re.compile(r"(?<!\d)%d\s*/\s*%d(?!\d)" % (d.month, d.day)),
            re.compile(re.escape(d.isoformat()))]
    blob = " ".join((s.get("title") or "") + " " + (s.get("snippet") or "") for s in sources)
    return any(p.search(blob) for p in pats)


def evidence_filter(events, backbone):
    """(살릴 일정, [(일정, 드롭 사유)]) — A: 근거 0건 드롭 / C: 연설·발언류는 근거 2건 + 날짜 언급."""
    kept, dropped = [], []
    for e in events:
        srcs = e.get("sources") or []
        if _backbone_confirmed(e, backbone):
            kept.append(e)
            continue
        if not srcs:
            dropped.append((e, "근거 기사 0건(환각 위험)"))
            continue
        if _SPEECH_RE.search(e.get("name", "")):
            if len(srcs) < 2:
                dropped.append((e, "연설·발언류인데 근거 %d건(2건 이상 필요)" % len(srcs)))
                continue
            if not _date_mentioned(e.get("date", ""), srcs):
                dropped.append((e, "연설·발언류인데 근거 기사에 그 날짜가 없음"))
                continue
        kept.append(e)
    return kept, dropped


# guide 문장 정합성 — 드롭된 일정을 가리키는 문장은 안내문에서도 빼야 한다.
# 일정 목록에서만 지우면 "잭슨홀 연설이 이미 영향을 준 상태"처럼 근거 없는 문장이 남는다.
_GENERIC_TOK = {"미국", "한국", "중국", "일본", "유럽", "발표", "지수", "결정", "보고서", "일정",
                "연설", "발언", "증언", "회견", "기준금리", "물가", "고용", "예정", "관련"}


def _name_tokens(name):
    """일정 이름에서 식별력 있는 낱말만 (괄호 안·일반명사 제외)."""
    base = re.sub(r"\(.*?\)", " ", name or "")
    return {t for t in re.findall(r"[가-힣A-Za-z]{2,}", base) if t not in _GENERIC_TOK}


def strip_dropped_from_guide(guide, dropped, kept):
    """드롭된 일정에만 있는 낱말이 든 문장을 guide에서 제거. (새 guide, 지운 문장들) 반환.
    살아남은 일정과 공유하는 낱말은 기준에서 빼서(예: '고용') 멀쩡한 문장까지 지우지 않는다."""
    if not guide or not dropped:
        return guide, []
    kept_tok = set()
    for e in kept:
        kept_tok |= _name_tokens(e.get("name", ""))
    bad = set()
    for e, _why in dropped:
        bad |= (_name_tokens(e.get("name", "")) - kept_tok)
    if not bad:
        return guide, []
    sents = [s.strip() for s in re.split(r"(?<=다\.)\s+", guide) if s.strip()]
    keep, removed = [], []
    for s in sents:
        (removed if any(b in s for b in bad) else keep).append(s)
    return " ".join(keep).strip(), removed


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
    p.add_argument("--model", default="claude-sonnet-5", help="Claude model.")
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
        resp = client.messages.create(model=model, max_tokens=6000,
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
        "- ★ source_ids: 그 일정을 확인한 기사 번호 2개 이상(위 목록의 [번호]). 기사에 없는 일정은 절대 만들지 말 것 — 근거 없는 일정은 코드가 자동으로 버린다.\n"
        "- ★ 미국 일정이면 et_date(미국 현지 날짜 YYYY-MM-DD)와 et_time(현지 시각 24시간 'HH:MM')도 채운다(고용보고서 08:30, FOMC 14:00, 연설은 실제 시각). 한국 날짜는 코드가 계산하니 추측하지 말 것. 미국 일정이 아니면 둘 다 빈 문자열.\n"
        "- guide: 다가오는 가장 큰 변수를 정확한 시점(이번 주/다음 주)과 함께 짚고 환전러 실전 조언 2~3문장. "
        "다음 주 일정을 '이번 주'라고 하지 말 것.\n\n"
        '아래 정확한 JSON만 출력(코드펜스 없이): {"guide":"...","events":[{"date":"YYYY-MM-DD",'
        '"time":"HH:MM","name":"...","importance":3,"summary":"...","why":"...","result":"",'
        '"source_ids":[0,3],"et_date":"","et_time":"","scenarios":[{"cond":"...","effect":"..."},{"cond":"...","effect":"..."}]}]}'
    )

    print("Asking LLM to extract the week's events…", flush=True)
    client = LLM(an_key=an_key)
    try:
        resp = client.messages.create(
            model=args.model, max_tokens=10000,
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
        # A) 근거 기사 연결 — 번호(source_ids)를 실제 기사로 되돌린다. 유효하지 않은 번호는 버린다.
        pool = articles[:30]
        srcs = []
        for sid in (ev.get("source_ids") or [])[:5]:
            try:
                a = pool[int(sid)]
            except (ValueError, TypeError, IndexError):
                continue
            srcs.append({"title": a.get("title", ""), "link": a.get("link", ""),
                        "snippet": (a.get("snippet") or "")[:200]})
        # D) 미국 일정은 현지 시각(ET)에서 한국 날짜를 코드가 계산해 덮어쓴다(모델 추측 금지).
        conv = kst_from_et(ev.get("et_date"), ev.get("et_time"))
        ktime = (ev.get("time") or "").strip()
        if conv:
            if conv[0] != date:
                print("  [ET→KST] %s: %s → %s (현지 %s %s)" % (
                      ev.get("name", ""), date, conv[0], ev.get("et_date"), ev.get("et_time")),
                      file=sys.stderr)
            date, ktime = conv[0], conv[1]
        events.append({
            "date": date,
            "time": ktime,
            "name": ev.get("name", "").strip(),
            "importance": imp,
            "summary": (ev.get("summary") or ev.get("impact") or "").strip(),
            "why": (ev.get("why") or "").strip(),
            "result": (ev.get("result") or "").strip(),
            "scenarios": scns,
            "sources": srcs,
        })
    events.sort(key=lambda x: x["date"])  # chronological for the table

    # A·C) 근거 없는 유령 일정 제거 — 백본 확정 지표는 통과, 나머지는 기사 근거 필수.
    #      배포를 막지 않고 그 일정만 뺀다(페이지는 살고 거짓만 사라진다).
    events, dropped = evidence_filter(events, backbone)
    for ev_d, why_d in dropped:
        print("  [유령일정 제거] %s (%s) — %s" % (ev_d.get("name", ""), ev_d.get("date", ""), why_d),
              file=sys.stderr)
    if dropped:
        g0 = result.get("guide") or ""
        g1, cut = strip_dropped_from_guide(g0, dropped, events)
        if cut:
            result["guide"] = g1
            for c in cut:
                print("  [guide 문장 제거] %s" % c[:60], file=sys.stderr)

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

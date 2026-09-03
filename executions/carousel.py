#!/usr/bin/env python3
"""
@when2exchange 데일리 인스타 캐러셀 PNG 생성기.

`output/factors-*.json`(요인·tldr·card_title) + `site/rate.json`(환율·시계열)을 읽어,
페르소나별 판정(share_page.py renderGauge 로직 포팅)을 계산하고,
웹사이트(share_page.py)와 동일한 팔레트로 슬라이드를 Playwright로 1080x1350 PNG 렌더한다.

표준 라이브러리 + playwright만 사용. 결과는 stdout, 에러는 stderr.
  python executions/carousel.py [--cover-only] [--guides] [--out output/cards]
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _today():
    """오늘(한국시간 KST). 빌드 머신이 다른 표준시여도 환율/일정과 날짜가 안 어긋나게."""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()


def _brief_date(rate):
    """이 브리핑의 날짜 = rate.json asof 날짜. 자정 넘겨 렌더해도 데이터 날짜와 안 어긋나게.
    (asof 예: '2026-06-16 09:12 KST'). 못 읽으면 KST 오늘로 폴백."""
    try:
        return dt.date.fromisoformat((rate.get("asof", "") or "")[:10])
    except ValueError:
        return _today()

# ── 페르소나 가중치 [1주, 1달, 3개월] (share_page.py SET_W, 합=1) ───────────────
SET_W = {
    "student":  [0.10, 0.30, 0.60],
    "traveler": [0.30, 0.50, 0.20],
    "investor": [0.50, 0.35, 0.15],
}
PERSONA = [
    ("student",  "🎓", "유학생"),
    ("investor", "📈", "투자자"),
    ("traveler", "✈️", "여행자"),
]
# vIdx 0/1/2 — light=신호등 불빛(선명), fg=글씨색(웹 good/mid/bad fg, 읽기 좋게)
VERDICT = [
    {"dot": "🟢", "light": "#1f9d57", "fg": "#1f7a47", "text": "지금 환전하기 좋아요"},
    {"dot": "🟡", "light": "#ffb020", "fg": "#9a6b00", "text": "지금은 보통이에요"},
    {"dot": "🔴", "light": "#e0383e", "fg": "#c5333a", "text": "지금은 환전이 아까워요"},
]

# 사이트 마스코트(슈나우저) 인라인 SVG — 투명 배경(동그라미 없음)
DOG_SVG = (
    '<svg class="dog" viewBox="0 0 72 48" role="img">'
    '<rect x="16" y="30" width="4.6" height="12" rx="2.3" fill="#7f8893"/>'
    '<rect x="24" y="30" width="4.6" height="12" rx="2.3" fill="#8b94a0"/>'
    '<rect x="41" y="30" width="4.6" height="12" rx="2.3" fill="#7f8893"/>'
    '<rect x="49" y="30" width="4.6" height="12" rx="2.3" fill="#8b94a0"/>'
    '<rect x="11" y="16" width="46" height="18" rx="9" fill="#9aa3ad"/>'
    '<rect x="46" y="7" width="21" height="21" rx="7" fill="#9aa3ad"/>'
    '<path d="M48 9 q1 -7 7 -5 l-1 8 z" fill="#7f8893"/>'
    '<path d="M53 12 q5 -2 9 1 q-4 0 -9 2 z" fill="#d3d8de"/>'
    '<circle cx="58" cy="16.5" r="1.8" fill="#1c2230"/>'
    '<path d="M60 19 h8 v4 q0 6 -5 7 q-4 -1 -3 -7 z" fill="#d3d8de"/>'
    '<circle cx="67" cy="20" r="1.9" fill="#1c2230"/></svg>'
)


def _pct_in(vals, rate_now, n):
    w = vals[-n:]
    if len(w) < 2:
        return None
    return sum(1 for v in w if v <= rate_now) / len(w)


def _remap(p):
    xs = [0, 0.10, 0.30, 0.70, 0.90, 1]
    ys = [0, 0.20, 0.40, 0.60, 0.80, 1]
    for i in range(1, len(xs)):
        if p <= xs[i]:
            t = (p - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return 1.0


def persona_verdict(series, rate_now, persona):
    """페르소나 판정 → VERDICT[vIdx]. share_page.py renderGauge 포팅."""
    p7, p30, p90 = (_pct_in(series, rate_now, n) for n in (7, 22, 63))
    if p90 is None:
        return None
    a7 = p7 if p7 is not None else (p30 if p30 is not None else p90)
    a30 = p30 if p30 is not None else p90
    w = SET_W[persona]
    pct = w[0] * a7 + w[1] * a30 + w[2] * p90
    zone = min(4, int(_remap(pct) * 5))
    vidx = 0 if zone <= 1 else (1 if zone == 2 else 2)
    return VERDICT[vidx]


# 제목 desync 방지: 원/달러 환율류 숫자(1,513원·1513·1520선)는 표지 제목 금지(factor_analysis와 동일).
_RATE_NUM_RE = re.compile(r"\d[\d,]*\s*원|\b1[0-9]{3}\b|\d{3,}\s*선")
# check A: 오늘 종가 단정 단어(어제/전일/지난/야간이 앞에 없을 때만 = '오늘'의 마감 단정).
_CLOSE_WORD_RE = re.compile(r"(마쳤|마감(?:했|됐|을|한|하)|종가|거래를 마)")


def market_closed(asof, today):
    """서울 외환시장 마감 여부. 주말=마감(금요일 종가). 평일=asof 시각 ≥15:30. 시각 불명=None(판단보류)."""
    if today.weekday() >= 5:
        return True
    m = re.search(r"(\d{1,2}):(\d{2})", asof or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2))) >= (15, 30)


def check_intraday_close(text, asof, today, rate=None):
    """장중(마감 전)인데 '오늘 종가/마감'을 단정하면 메시지 반환(없으면 None). check A 공용 로직.
    오늘 환율 숫자(±2)에 마감/종가가 붙은 경우만 잡고, 그 숫자 앞 어제/전일/야간은 제외(전일 종가는 정당)."""
    if market_closed(asof, today) is not False:   # 마감 후/불명이면 통과
        return None
    cur = (rate or {}).get("rate")
    if not isinstance(cur, (int, float)):
        return None
    cur_i = int(cur + 0.5)
    for m in re.finditer(r"(\d[\d,]*)\s*원\s*(?:에|으로|선에서?)?\s*(마쳤|마감|종가)", text):
        try:
            n = int(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if abs(n - cur_i) > 2:   # 오늘 환율과 다른 숫자 = 어제·딴날 종가일 수 있음 → 패스
            continue
        if re.search(r"(어제|전일|지난|어젯|야간)", text[max(0, m.start() - 6):m.start()]):
            continue             # '어제 1,544원 마감' = 전일 종가라 정당
        return (f"장중(마감 전, asof {asof})인데 오늘({cur_i:,}원) '{m.group(2)}' 단정 "
                "— 15:30 전이면 '현재 ○○원'으로")
    return None


# check B: 장중엔 '오늘 추세가 이렇게 끝났다'를 제목에 단정하지 않는다.
# 실제 사고(2026-07-10): 10:02 장중 +1.5원을 보고 '원화 강세 흐름 멈췄다'로 발행했는데
# 종가는 1,502원(-5.0원) — 강세가 오히려 이어져 제목이 사후에 거짓이 됐다.
_TREND_CLAIM_RE = re.compile(r"(멈췄|멈춤|꺾였|꺾임|반전|돌아섰|되돌렸|전환됐|끝났|마무리)")


def check_intraday_trend(title, asof, today):
    """장중(마감 전)인데 제목이 오늘 추세를 단정하면 메시지 반환(없으면 None).
    '어제·전일·간밤'을 가리키는 단정은 정당하므로 제외."""
    if market_closed(asof, today) is not False:   # 마감 후/불명이면 통과
        return None
    m = _TREND_CLAIM_RE.search(title or "")
    if not m:
        return None
    if re.search(r"(어제|전일|지난|간밤|야간)", title[:m.start()]):
        return None
    return (f"장중(마감 전, asof {asof})인데 제목이 오늘 추세를 단정('{m.group(1)}…') "
            "— 종가에서 뒤집힐 수 있어요. 마감(15:30) 후 제작하거나 '장중' 표현으로")


# ── 이벤트 날짜 자동 검증 (내가 안 짚어도 스스로) ──────────────────────────
_WD_KO = "월화수목금토일"     # date.weekday(): 0=월 … 6=일
# 검증 일정표(backbone)와 대조할 지표: (캘린더 이름 패턴) → event_schedule kw
_BACKBONE_KW = [
    (re.compile(r"소비자물가|CPI"), "CPI"),
    (re.compile(r"고용보고서|비농업|고용지표"), "고용"),
    (re.compile(r"PCE|개인소비지출"), "PCE"),
    (re.compile(r"FOMC.*(금리\s*결정|정례회의|금리결정)"), "FOMC"),   # ※ '의사록'은 결정과 다른 날 → 제외
]
# 미국 지표 = 주말(토/일) 발표 불가. FOMC 의사록·결정도 미 평일→KST 평일
_US_INDICATOR = re.compile(r"소비자물가|CPI|고용보고서|비농업|PCE|개인소비지출|FOMC")


def _load_backbone():
    try:
        return json.load(open(ROOT / "data" / "event_schedule.json",
                              encoding="utf-8")).get("events", [])
    except Exception:
        return []


def check_event_dates(cal_path, extra_text, today):
    """이벤트 날짜를 스스로 검증(WARN): ① 검증 일정표 대조 ② 미국 지표 주말발표 불가
    ③ '7월 11일(금)' 요일 라벨 ↔ 실제 요일 불일치. 근거 없이 지어낸 날짜를 자동으로 잡는다."""
    warns = []
    backbone = _load_backbone()
    events, guide = [], ""
    if cal_path:
        try:
            cj = json.load(open(cal_path, encoding="utf-8"))
            events, guide = cj.get("events", []), cj.get("guide", "") or ""
        except Exception:
            pass
    for e in events:
        name, ds = e.get("name", ""), e.get("date", "")
        try:
            d = dt.date.fromisoformat(ds)
        except ValueError:
            continue
        # ② 미국 지표가 주말 발표 = 불가능(예: CPI 7/11(토))
        if _US_INDICATOR.search(name) and d.weekday() >= 5:
            warns.append(f"[날짜검증] '{name}' {ds}={_WD_KO[d.weekday()]}요일 — "
                         "미국 지표는 주말 발표 없음(날짜 오류 의심)")
        # ① 검증 일정표(backbone)와 대조 — 같은 kw·같은 달인데 날짜 다르면
        for pat, kw in _BACKBONE_KW:
            if pat.search(name):
                for be in backbone:
                    if be.get("kw") == kw and be.get("date", "")[:7] == ds[:7] \
                            and be.get("date") != ds:
                        warns.append(f"[날짜검증] '{name}' 캘린더 {ds} ↔ 검증 일정표 "
                                     f"{be['date']} 불일치 — data/event_schedule.json 우선 확인")
                break
    # ③ '7월 11일(금)' 요일 라벨이 실제 요일과 맞나 (본문+캘린더 guide 스캔)
    for m in re.finditer(r"(\d{1,2})월\s*(\d{1,2})일\s*\(([월화수목금토일])\)",
                         (extra_text or "") + " " + guide):
        mm, dd, lbl = int(m.group(1)), int(m.group(2)), m.group(3)
        try:
            real = _WD_KO[dt.date(today.year, mm, dd).weekday()]
        except ValueError:
            continue
        if real != lbl:
            warns.append(f"[날짜검증] '{mm}월 {dd}일({lbl})' — 실제로는 {real}요일(요일 라벨 오류)")
    return warns


def validate_facts(factors, rate):
    """발행 전 게이트. card_title·요약 ↔ 실제 환율/일정 대조 → (blocks=게시 금지, warns=확인).
    factor_analysis.validate_briefing와 같은 기준(표지·웹 공유). 엔진이 JSON에 박은 checks도 상속."""
    blocks, warns = [], []
    title = (factors.get("card_title", "") or "").strip()
    text = " ".join([
        title,
        " ".join(factors.get("tldr", []) or []),
        factors.get("overall_why", "") or "",
    ])
    # 0) 표지 제목에 환율 숫자 = 박스/배지와 desync (BLOCK)
    if _RATE_NUM_RE.search(title):
        blocks.append(f"표지 제목에 환율 숫자 → 박스와 desync: '{title}' (숫자 빼고 원인·이슈로)")
    # 0b) 엔진(factor_analysis)이 생성 때 박아 둔 검증 결과 상속
    ck = factors.get("checks") or {}
    for b in (ck.get("blocks") or []):
        blocks.append(f"[엔진] {b}")
    for w in (ck.get("warnings") or []):
        warns.append(f"[엔진] {w}")
    # 0c) 요인 근거 기사 0개 = 환각 위험 (직접 체크 — 엔진 checks 없는 파일도 방어)
    empties = [f.get("name", "?") for f in (factors.get("factors") or [])
               if not (f.get("sources") or [])]
    if empties:
        blocks.append(f"근거 기사 0개 요인: {', '.join(empties)} (환각 위험)")

    r, p = rate.get("rate"), rate.get("prev")
    if r and p:
        actual = r - p  # +면 상승, -면 하락
        # 1) "N원 (하락/상승)" 크기 주장 vs 실제 (BLOCK)
        for m in re.finditer(r"(\d+)\s*원\s*(?:넘게|이상|가까이|가량)?\s*"
                             r"(하락|내려|내린|떨어|급락|상승|올라|오른|급등)", text):
            claimed = int(m.group(1))
            if abs(claimed - abs(actual)) > 3:
                blocks.append(
                    f"텍스트 '{claimed}원 {m.group(2)}' ↔ 실제 전일대비 {actual:+.1f}원 "
                    f"(차이 {abs(claimed - abs(actual)):.0f}원)"
                )
        # 2) 거의 보합인데 방향/변동을 주장 (WARN)
        if abs(actual) < 1.0 and any(
            w in text for w in ("하락", "급락", "상승", "급등", "강세", "약세")
        ):
            warns.append(f"실제 전일대비 {actual:+.1f}원(거의 보합)인데 텍스트가 변동/방향을 주장")
    # 3) 핵심 정렬 점검: 임박한 ★★★ 이벤트가 요약에 반영됐나 (WARN)
    today = _brief_date(rate)
    cfs = sorted(glob.glob(str(ROOT / "output" / "calendar-*.json")))
    if cfs:
        for e in json.load(open(cfs[-1], encoding="utf-8")).get("events", []):
            try:
                days = (dt.date.fromisoformat(e.get("date", "")) - today).days
            except ValueError:
                continue
            if int(e.get("importance", 0)) >= 3 and 0 <= days <= 3:
                km = re.search(r"(FOMC|CPI|금통위|고용|금리|소비자물가|연준)", e.get("name", ""))
                kw = km.group(1) if km else e.get("name", "")[:5]
                if kw and kw not in text:
                    warns.append(
                        f"[핵심정렬] 임박 ★★★ '{e.get('name','')}'이 요약에 안 보임 — 오늘 핵심 맞는지 확인"
                    )
    # 4) check A: 장중(마감 전)인데 '오늘 종가/마감' 단정 = 거짓 (BLOCK)
    msg = check_intraday_close(text, rate.get("asof", ""), today, rate)
    if msg:
        blocks.append(msg)
    # 4b) check B: 장중인데 제목이 오늘 추세를 단정 = 종가에서 뒤집힘 (BLOCK)
    msg = check_intraday_trend(title, rate.get("asof", ""), today)
    if msg:
        blocks.append(msg)
    # 5) 이벤트 날짜 자동 검증 — 검증 일정표 대조 + 주말발표·요일 라벨 sanity (WARN)
    warns += check_event_dates(cfs[-1] if cfs else None, text, today)
    return blocks, warns


def load_data():
    fs = sorted(glob.glob(str(ROOT / "output" / "factors-*.json")))
    factors = json.load(open(fs[-1], encoding="utf-8")) if fs else {}
    rate = json.load(open(ROOT / "site" / "rate.json", encoding="utf-8"))
    series = [pt["close"] for pt in rate.get("series", []) if "close" in pt]
    return factors, rate, series


# ── HTML (웹사이트 share_page.py 팔레트 그대로) ───────────────────────────────
HEAD = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:'Pretendard',sans-serif;-webkit-font-smoothing:antialiased}
  /* 웹 팔레트: --bg #f4f5f7 / --ink #14171f / --muted #6b7280 / --line #ebedf0
     / --brand #3b5bdb / --brand-bg #eef1fd */
  /* 세이프존: 상96 / 옆76 / 하150 */
  .page{width:1080px;height:1350px;background:#f4f5f7;padding:96px 76px 150px;
        display:flex;flex-direction:column;color:#14171f;position:relative}
  .page.cover{justify-content:space-between}
  svg.dog{width:auto;flex:none}

  .m1{font-size:33px;font-weight:700;color:#6b7280;letter-spacing:-.02em;
      display:flex;justify-content:space-between;align-items:center}
  .m1 .kw{color:#3b5bdb}
  .m1-date{color:#9aa1ad;font-weight:700;font-size:30px}
  .m1-right{display:inline-flex;align-items:center;gap:14px}
  /* heat=high = 날짜 옆 작은 빨강 칩(신호등 빨강). 잠잠한 날엔 안 보임 */
  .m1-heat{font-size:24px;font-weight:800;color:#fff;background:#e0383e;
           border-radius:999px;padding:7px 16px;letter-spacing:-.01em}
  .m2{margin-top:8px;font-size:52px;font-weight:800;color:#14171f;letter-spacing:-.035em;
      line-height:1.16;white-space:nowrap;display:flex;align-items:center;gap:14px;font-size:46px}
  .m2 svg.dog{height:58px}
  .m2 .kw{color:#3b5bdb}
  /* 2안: 작은 회색 리드(브랜드 질문) + 큰 날짜·이슈 헤드라인 */
  .hl2{margin-top:24px;font-size:88px;font-weight:900;letter-spacing:-.05em;
       line-height:1.12;word-break:keep-all}
  .hl2 .hl-date{font-size:42px;color:#9aa1ad;margin-right:16px}
  .hl2 .hl-title{color:#14171f}
  .hl2 .hl-kw{color:#3b5bdb}
  .hl2 svg.dog{height:56px;margin-left:12px;vertical-align:-15px}

  /* 환율 박스 (웹 .hero 그대로) */
  .hero{margin-top:38px;background:#fff;border:1.5px solid #ebedf0;border-radius:28px;
        padding:48px 48px;box-shadow:0 6px 22px rgba(20,30,60,.05)}
  .h-label{font-size:28px;font-weight:700;color:#6b7280}
  .h-main{display:flex;align-items:baseline;margin-top:10px}
  .h-rate{font-size:82px;font-weight:800;letter-spacing:-.035em;line-height:1.02;color:#14171f}
  .h-won{font-size:30px;font-weight:800;margin-left:4px}
  .h-delta{font-size:29px;font-weight:800;margin-left:18px;letter-spacing:-.02em}
  .h-delta.up{color:#c5333a}
  .h-delta.down{color:#1f7a47}
  .h-asof{font-size:23px;font-weight:600;color:#9aa1ad;margin-top:12px}

  /* 30초 요약 (웹 .tldr 그대로) */
  .tldr{margin-top:24px;background:#eef1fd;border-radius:26px;padding:36px 48px}
  .tldr-head{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:20px}
  .tldr-cap{font-size:25px;font-weight:800;color:#3b5bdb;letter-spacing:.02em}
  .tldr-upd{font-size:22px;font-weight:600;color:#9aa1ad}
  .tldr-list{display:flex;flex-direction:column;gap:15px}
  .tldr-li{font-size:28px;line-height:1.5;color:#2b313d;letter-spacing:-.02em;
           word-break:keep-all;padding-left:27px;position:relative}
  .tldr-li::before{content:'';position:absolute;left:5px;top:.6em;width:9px;height:9px;
                   border-radius:50%;background:#3b5bdb;opacity:.55}

  /* 페르소나 탭 (웹 .tabs 그대로 + 신호 색점) */
  .ptabs{display:flex;gap:7px;background:#eceef1;border-radius:22px;padding:7px;margin-top:24px}
  .ptab{flex:1;display:flex;align-items:center;justify-content:center;gap:9px;
        padding:26px 8px;border-radius:17px;font-size:37px;font-weight:800;
        color:#14171f;letter-spacing:-.02em}
  .ptab .pico{font-size:38px}
  .ptab .pdot{width:22px;height:22px;border-radius:50%;flex:none;margin-left:2px}

  .spacer{flex:1}
  /* 미니 목차 (전체 섹션 인덱스) */
  .toc-wrap{display:flex;flex-direction:column;align-items:flex-end;gap:16px}
  .toc-cue{display:flex;align-items:center;gap:8px;font-size:33px;font-weight:800;
           color:#3b5bdb;letter-spacing:-.02em}
  .toc-cue .toc-arrow{font-size:34px;font-weight:900}
  .toc-row{display:flex;align-items:center;justify-content:flex-end;gap:11px}
  .toc-chip{font-size:27px;font-weight:700;color:#4b5563;background:#fff;
            border:1.5px solid #e3e6ea;border-radius:999px;padding:12px 19px;
            letter-spacing:-.01em;box-shadow:0 2px 8px rgba(20,30,60,.04)}
  .toc-arrow{color:#3b5bdb;font-size:40px;font-weight:800;margin-right:5px}
  .cmascot{display:flex;flex-direction:column;align-items:center;gap:18px;text-align:center;
           color:#aab2bd;font-size:29px;font-weight:700;letter-spacing:-.01em}
  .cmascot svg.dog{height:92px}

  /* 페르소나 신호 (slide 2용) */
  .card{margin-top:40px;background:#fff;border:1.5px solid #ebedf0;border-radius:28px;
        padding:50px 46px 40px;box-shadow:0 6px 22px rgba(20,30,60,.05)}
  .card-t{font-size:26px;font-weight:800;color:#6b7280;margin-bottom:36px}
  .prow{display:flex;align-items:center;justify-content:space-between;padding:19px 0}
  .prow+.prow{border-top:1.5px solid #ebedf0}
  .pleft{font-size:36px;font-weight:800;letter-spacing:-.02em}
  .pleft .pico{font-size:38px;margin-right:14px}
  .pright{display:flex;align-items:center;font-size:32px;font-weight:800}
  .pright .pdot2{width:25px;height:25px;border-radius:50%;margin-right:14px;flex:none}

  /* 공통 슬라이드 헤더/타이틀/노트 */
  .shead{font-size:26px;font-weight:700;color:#9aa1ad;display:flex;align-items:center;gap:11px}
  .shead svg.dog{height:36px}
  .stitle{margin-top:18px;font-size:50px;font-weight:800;color:#14171f;
          letter-spacing:-.035em;line-height:1.22;word-break:keep-all}
  .stitle .kw{color:#3b5bdb}
  .snote{margin-top:30px;font-size:27px;font-weight:600;color:#6b7280;line-height:1.5;
         letter-spacing:-.02em;word-break:keep-all}
  .snote b{color:#14171f;font-weight:800}
  .sfoot{display:flex;justify-content:flex-end;font-size:28px;font-weight:800;color:#3b5bdb}
  /* 마지막 페이지 닫는 블록(팔로우 CTA + 면책) */
  .endwrap{display:flex;flex-direction:column;align-items:center;text-align:center;gap:18px}
  .endmsg{font-size:35px;font-weight:800;color:#14171f;letter-spacing:-.025em;word-break:keep-all}
  .endbtn{font-size:35px;font-weight:800;color:#fff;background:#3b5bdb;border-radius:999px;
          padding:25px 46px;letter-spacing:-.01em;margin-top:2px}
  .endlink{font-size:27px;font-weight:700;color:#6b7280;letter-spacing:-.01em}
  .enddisc{font-size:22px;font-weight:600;color:#9aa1ad;letter-spacing:-.01em;margin-top:6px}
  /* '왜 주목?' 페이지(heat=high 전용) — 주목 이유 카드 + 후킹 */
  .atn-wrap{margin-top:40px;display:flex;flex-direction:column;gap:24px}
  .atn{display:flex;gap:26px;align-items:flex-start;background:#fff;border:1.5px solid #ebedf0;
       border-radius:24px;padding:34px 36px;box-shadow:0 4px 16px rgba(20,30,60,.04)}
  .atn-emoji{flex:none;font-size:48px;line-height:1.1}
  .atn-head{font-size:34px;font-weight:800;color:#14171f;letter-spacing:-.025em;
            line-height:1.3;word-break:keep-all}
  .atn-sub{margin-top:9px;font-size:28px;font-weight:600;color:#4e5968;line-height:1.45;
           letter-spacing:-.02em;word-break:keep-all}
  .atn-hook{margin-top:32px;font-size:32px;font-weight:800;color:#e0383e;letter-spacing:-.025em;
            line-height:1.4;word-break:keep-all}

  /* 그래프 슬라이드 */
  .gbig{margin-top:38px;background:#fff;border:1.5px solid #ebedf0;border-radius:28px;
        padding:38px 42px;box-shadow:0 6px 22px rgba(20,30,60,.05)}
  .grow{display:flex;align-items:baseline;justify-content:space-between}
  .glabel{font-size:28px;font-weight:800;color:#14171f}
  .gnow{font-size:29px;font-weight:800;color:#3b5bdb}
  svg.spark{width:100%;height:auto;display:block;margin:18px 0 12px}
  .grange{display:flex;justify-content:space-between;font-size:23px;font-weight:700;
          color:#9aa1ad;margin-top:12px}
  .gsmall .grange{font-size:20px;margin-top:10px}
  .gtag{margin-top:14px;font-size:27px;font-weight:700;color:#4e5968}
  .gtag b{color:#14171f;font-weight:800}
  .gsmall-wrap{display:flex;gap:24px;margin-top:24px}
  .gsmall{flex:1;background:#fff;border:1.5px solid #ebedf0;border-radius:24px;padding:26px 28px}
  .gsmall .glabel{font-size:25px}
  .gsmall svg.spark{margin:12px 0 8px}
  .gsmall .gtag{font-size:22px;margin-top:6px;word-break:keep-all}
  svg.hchart{width:100%;height:auto;display:block;margin:16px 0 6px}
  .gsmall svg.hchart{margin:12px 0 4px}
  .hchart .c-lbl{font-weight:800;fill:#8b95a1}
  .hchart .c-avg{font-weight:700;fill:#aab2bd}
  .gtag .up{color:#c5333a;font-weight:800}
  .gtag .dn{color:#1f7a47;font-weight:800}

  /* 파란 배경 변형 (그래프 슬라이드 — 흰 카드 강조) */
  .page.blue{background:#3b5bdb;color:#fff}
  .page.blue .shead{color:#c3d0fa}
  .page.blue .stitle{color:#fff}
  .page.blue .stitle .kw{color:#fff;text-decoration:underline;text-underline-offset:7px;
       text-decoration-thickness:4px;text-decoration-color:rgba(255,255,255,.45)}
  .page.blue .snote{color:#dce3fb}
  .page.blue .snote b{color:#fff}
  .page.blue .sfoot{color:#fff}

  /* 일정(이벤트) 슬라이드 */
  .ev{display:flex;gap:26px;padding:25px 0}
  .ev+.ev{border-top:1.5px solid #ebedf0}
  .ev-d{flex:none;width:120px;text-align:center}
  .ev-dd{display:block;font-size:31px;font-weight:800;color:#3b5bdb}
  .ev-date{display:block;font-size:22px;font-weight:700;color:#9aa1ad;margin-top:3px}
  .ev-main{flex:1;min-width:0}
  .ev-name{font-size:31px;font-weight:800;color:#14171f;letter-spacing:-.02em;
           line-height:1.3;word-break:keep-all}
  .ev-star{color:#f5a623;font-size:25px;letter-spacing:-2px;margin-right:5px}
  .ev-why{margin-top:9px;font-size:25px;font-weight:600;color:#6b7280;
          line-height:1.45;word-break:keep-all}
  /* 일정 v2: 가까운 일정 확장 카드 + 시나리오 */
  .evbig{margin-top:34px;background:#fff;border:1.5px solid #ebedf0;border-radius:24px;
         padding:34px 36px;box-shadow:0 4px 16px rgba(20,30,60,.04)}
  .evbig-row{display:flex;gap:16px;align-items:flex-start}
  .evbig-dd{flex:none;font-size:28px;font-weight:800;color:#3b5bdb;margin-top:2px}
  .evbig-main{flex:1;min-width:0}
  .evbig-title{font-size:32px;font-weight:800;color:#14171f;letter-spacing:-.02em;
               line-height:1.3;word-break:keep-all}
  .evbig-star{flex:none;color:#f5a623;font-size:23px;letter-spacing:-2px;margin-top:6px}
  .scen{display:flex;gap:12px;margin-top:17px;align-items:flex-start}
  .scen-arr{flex:none;width:28px;text-align:center;font-size:27px;font-weight:800;margin-top:-1px}
  .scen-arr.up{color:#c5333a}
  .scen-arr.dn{color:#1f7a47}
  .scen-arr.fl{color:#b4bbc4}
  .scen-t{font-size:30px;font-weight:600;color:#4e5968;line-height:1.45;
          letter-spacing:-.02em;word-break:keep-all}
  .scen-t b{color:#14171f;font-weight:800}
  .evbig-sum{margin-top:13px;font-size:31px;font-weight:600;color:#4e5968;
             line-height:1.45;letter-spacing:-.02em;word-break:keep-all}
  .evrow{display:flex;align-items:flex-start;gap:18px;padding:24px 6px}
  .evrow+.evrow{border-top:1.5px solid #ebedf0}
  .evrow-dd{flex:none;width:104px;font-size:28px;font-weight:800;color:#3b5bdb;
            text-align:center;margin-top:2px}
  .evrow-main{flex:1;min-width:0}
  .evrow-name{font-size:31px;font-weight:800;color:#14171f;letter-spacing:-.02em;
              line-height:1.3;word-break:keep-all}
  .evrow-sum{margin-top:8px;font-size:29px;font-weight:600;color:#4e5968;
             line-height:1.4;letter-spacing:-.02em;word-break:keep-all}
  .evrow-star{flex:none;color:#f5a623;font-size:23px;letter-spacing:-2px;margin-top:4px}
  .evlist-cap{font-size:27px;font-weight:800;color:#9aa1ad;margin:26px 2px 6px}

  /* 요인 슬라이드 — 헤더 카드 + 본문 */
  .fmid{flex:1;display:flex;flex-direction:column;justify-content:center}
  .fhdr{background:#fff;border:1.5px solid #ebedf0;border-radius:28px;
        padding:38px 40px;box-shadow:0 6px 22px rgba(20,30,60,.05);
        display:flex;align-items:center;gap:26px}
  .fhdr-badge{flex:none;width:120px;height:120px;border-radius:30px;background:#eef1fd;
              display:flex;align-items:center;justify-content:center;font-size:68px}
  .fhdr-main{flex:1;min-width:0}
  .fhdr-rank{font-size:27px;font-weight:800;color:#3b5bdb;letter-spacing:-.01em}
  .fhdr-name{margin-top:5px;font-size:46px;font-weight:800;color:#14171f;letter-spacing:-.03em;
             line-height:1.15;word-break:keep-all}
  .fimp2{margin-top:16px;display:flex;align-items:center;gap:13px}
  .fbar{display:flex;gap:6px}
  .fbar i{width:40px;height:13px;border-radius:7px;background:#e3e7ec;display:block}
  .fbar i.on{background:#3b5bdb}
  .fimp2-t{font-size:24px;font-weight:800;color:#3b5bdb}
  .fwhy{margin-top:38px;font-size:38px;font-weight:700;color:#14171f;line-height:1.4;
        letter-spacing:-.025em;word-break:keep-all}
  .fbul{margin-top:30px;display:flex;flex-direction:column;gap:20px}
  .fbul-i{display:flex;gap:16px;font-size:30px;font-weight:600;color:#4e5968;line-height:1.45;
          letter-spacing:-.02em;word-break:keep-all}
  .fbul-i::before{content:'';flex:none;width:11px;height:11px;border-radius:50%;
                  background:#3b5bdb;margin-top:.5em}
  .fsrc{margin-top:30px;font-size:25px;font-weight:700;color:#9aa1ad}
  /* 요인 B안 — 파란 배경 + 흰 카드 */
  .fcardb{background:#fff;border-radius:36px;padding:58px 54px;
          box-shadow:0 24px 64px rgba(15,23,55,.28);display:flex;flex-direction:column}
  .fcardb.gray{border:1.5px solid #ebedf0;box-shadow:0 16px 44px rgba(20,30,60,.10)}
  .fcardb-top{display:flex;align-items:center;gap:26px}
  .fcardb .fimp2{margin-top:26px}
  .fcardb .fwhy{margin-top:30px}

  /* 30초 요약 슬라이드 (크게 읽히게) */
  .sc-wrap{margin-top:44px;display:flex;flex-direction:column;gap:24px}
  .sc{display:flex;gap:26px;align-items:flex-start;background:#fff;border:1.5px solid #ebedf0;
      border-radius:24px;padding:36px 38px;box-shadow:0 4px 16px rgba(20,30,60,.04)}
  .sc-n{flex:none;width:54px;height:54px;border-radius:15px;background:#3b5bdb;color:#fff;
        font-size:29px;font-weight:800;display:flex;align-items:center;justify-content:center;margin-top:2px}
  .sc-t{font-size:32px;font-weight:600;color:#2b313d;line-height:1.5;
        letter-spacing:-.02em;word-break:keep-all}

  /* --guides */
  .guides{position:absolute;inset:0;pointer-events:none;z-index:99}
  .guides i{position:absolute;background:rgba(197,51,58,.15)}
  .guides .gt{top:0;left:0;right:0;height:96px}
  .guides .gb{bottom:0;left:0;right:0;height:150px}
  .guides .gl{top:0;bottom:0;left:0;width:40px}
  .guides .gr{top:0;bottom:0;right:0;width:40px}
  .guides .safe{position:absolute;top:96px;bottom:150px;left:40px;right:40px;
                border:3px dashed rgba(31,122,71,.8);border-radius:8px}
</style></head><body>"""
FOOT = "</body></html>"
GUIDES = ('<div class="guides"><i class="gt"></i><i class="gb"></i><i class="gl"></i>'
          '<i class="gr"></i><div class="safe"></div></div>')


def cover_html(factors, rate, series, guides=False, version=2, badge=None, hook=""):  # 2안 확정
    rate_now = rate.get("rate") or (series[-1] if series else 0)
    title = factors.get("card_title", "오늘의 환율 브리핑")
    # heat=high면 빨강 배지(엔진이 title_badge에 박음, --badge로 덮어쓰기). 후킹 문구는 큐레이션(--hook).
    badge = factors.get("title_badge", "") if badge is None else badge

    prev = rate.get("prev")
    delta = ""
    if prev:
        d = rate_now - prev
        if abs(d) >= 0.05:
            arrow, cls = ("▲", "up") if d > 0 else ("▼", "down")
            delta = f'<span class="h-delta {cls}">{arrow} {abs(d):,.1f}원</span>'
    asof = rate.get("asof", "")
    today = _brief_date(rate)  # 표지 날짜 = 브리핑(rate asof) 날짜
    wk = ["월", "화", "수", "목", "금", "토", "주일"][today.weekday()]
    ptabs = "".join(
        f'<div class="ptab"><span class="pico">{ico}</span>{name}'
        f'<span class="pdot" style="background:'
        f'{(persona_verdict(series, rate_now, key) or VERDICT[1])["light"]}"></span></div>'
        for key, ico, name in PERSONA
    )

    wk = ["월", "화", "수", "목", "금", "토", "주일"][today.weekday()]
    # 리드(질문 + 우측: heat 칩·날짜). heat=high면 날짜 옆 작은 빨강 칩으로 강조.
    heat_chip = (f'<span class="m1-heat">{badge}</span>' if badge else "")
    lead = (
        '<div class="m1"><span>이번 달, 오늘 환전해도 될까?</span>'
        f'<span class="m1-right">{heat_chip}'
        f'<span class="m1-date">{today.month}/{today.day} ({wk})</span></span></div>'
    )
    # 큰 두 줄 분할: 콤마(,)가 있고 *뒷줄이 너무 길지 않으면* 콤마에서 끊는다(자연스러운 호흡).
    # 아니면 마지막 두 단어를 뒷줄(파랑 키워드)로. 예: 'FOMC 매파 동결, 달러 더 세졌다'
    # → '동결,' 뒤가 짧으니 'FOMC 매파 동결,' / '달러 더 세졌다'.
    head, tail = "", title
    if ", " in title:
        h, t = title.split(", ", 1)
        t = t.strip()
        if t and len(t.replace(" ", "")) <= 9:   # 뒷줄 공백 제외 9자 이하만 콤마 분할
            head, tail = h + ",", t
    if not head:                                  # 콤마 분할 안 했으면 마지막 두 단어를 뒷줄로
        _w = title.split()
        if len(_w) > 2:
            head, tail = " ".join(_w[:-2]), " ".join(_w[-2:])
    title_html = (
        f'<span class="hl-kw">{tail}</span>' if not head
        else f'{head}<br><span class="hl-kw">{tail}</span>'
    )
    headline = f'<div class="hl2"><span class="hl-title">{title_html}</span>{DOG_SVG}</div>'
    mid = (
        headline
        + '<div class="hero"><div class="h-label">매매기준율</div>'
        + f'<div class="h-main"><span class="h-rate">{rate_now:,.1f}</span>'
        + f'<span class="h-won">원</span>{delta}</div>'
        + f'<div class="h-asof">{asof} 기준</div></div>'
        + f'<div class="ptabs">{ptabs}</div>'
    )
    # 표지 악센트 1개 — heat는 날짜 옆 작은 칩으로(큰 배지 X). 후킹 문구는 캡션으로.
    toc = (
        '<div class="toc-wrap">'
        + '<div class="toc-cue">넘겨서 30초요약부터 보기<span class="toc-arrow">→</span></div>'
        '<div class="toc-row">'
        '<span class="toc-chip">⏱️ 30초요약</span><span class="toc-chip">📅 영향일정</span>'
        '<span class="toc-chip">📈 환율흐름</span><span class="toc-chip">🔍 형성요인</span></div>'
        '</div>'
    )
    return (HEAD + '<div class="page cover">'
            + f'<div class="ctop">{lead}</div>'
            + f'<div class="cmid">{mid}</div>'
            + toc
            + (GUIDES if guides else "") + "</div>" + FOOT)


def _shead(today):
    wk = ["월", "화", "수", "목", "금", "토", "일"][today.weekday()]
    return f'<div class="shead">{DOG_SVG} 환전타이밍 · {today.month}/{today.day}({wk})</div>'


def slide_attention(factors, rate, guides=False):
    """'왜 주목?' 페이지 — heat=high인 날, 오늘 주목해야 할 이유 + 후킹. factors['attention'] 읽음."""
    today = _brief_date(rate)
    att = factors.get("attention") or {}
    rows = "".join(
        f'<div class="atn"><div class="atn-emoji">{r.get("emoji","")}</div>'
        f'<div class="atn-main"><div class="atn-head">{r.get("head","")}</div>'
        f'<div class="atn-sub">{r.get("sub","")}</div></div></div>'
        for r in (att.get("reasons") or [])[:3]
    )
    hook = att.get("hook", "")
    body = (
        _shead(today)
        + '<div class="stitle">이번 주, <span class="kw">왜 주목</span>하개? 🐶</div>'
        + f'<div class="atn-wrap">{rows}</div>'
        + (f'<div class="atn-hook">{hook}</div>' if hook else "")
        + '<div class="spacer"></div>'
        + '<div class="enddisc">* 예측이 아니라 지켜볼 포인트예요 · 투자조언 아님</div>'
    )
    return HEAD + '<div class="page">' + body + (GUIDES if guides else "") + "</div>" + FOOT


def slide_signal(factors, rate, series, guides=False):
    """슬라이드 2: 페르소나별 환전 신호(전체 판정 텍스트)."""
    rate_now = rate.get("rate") or (series[-1] if series else 0)
    today = _brief_date(rate)
    rows = ""
    for key, ico, name in PERSONA:
        v = persona_verdict(series, rate_now, key) or VERDICT[1]
        rows += (
            f'<div class="prow"><div class="pleft"><span class="pico">{ico}</span>{name}</div>'
            f'<div class="pright" style="color:{v["fg"]}">'
            f'<span class="pdot2" style="background:{v["light"]}"></span>{v["text"]}</div></div>'
        )
    body = (
        _shead(today)
        + '<div class="stitle">오늘, <span class="kw">내 상황</span>별 환전 신호</div>'
        + f'<div class="card" style="margin-top:46px">{rows}</div>'
        + '<div class="snote">같은 환율도 <b>구매 시야</b>에 따라 기준이 달라요. '
        + '유학·송금은 길게(3개월), 투자는 짧게(1주) 봐서 신호가 갈려요.</div>'
        + '<div class="spacer"></div>'
        + '<div class="endwrap">'
        + '<div class="endmsg">🐶 맞든 틀리든, 매일 정직하게 알려드릴개요</div>'
        + '<div class="endbtn">팔로우하고 매일 환율 받기 🐾</div>'
        + '<div class="endlink">지금 내 상황 실시간 분석 → 프로필 링크</div>'
        + '<div class="enddisc">* 예측 아니라 오늘까지의 사실 정리예요 · 투자조언 아님</div>'
        + '</div>'
    )
    return HEAD + '<div class="page">' + body + (GUIDES if guides else "") + "</div>" + FOOT


def _spark(vals, W, H):
    n = len(vals)
    if n < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pad = 10
    pts = [(pad + (W - 2 * pad) * i / (n - 1),
            pad + (H - 2 * pad) * (1 - (v - lo) / rng)) for i, v in enumerate(vals)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    lx, ly = pts[-1]
    return (f'<svg class="spark" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            f'<polyline points="{poly}" fill="none" stroke="#3b5bdb" stroke-width="4" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="8" fill="#3b5bdb"/></svg>')


def _wtag(series, rate_now, n):
    vals = series[-n:] if len(series) >= n else series[:]
    p = _pct_in(series, rate_now, n)
    tag = "중간" if p is None else ("높은 편" if p >= 0.66 else ("낮은 편" if p <= 0.34 else "중간"))
    lo, hi = (min(vals), max(vals)) if vals else (0, 0)
    return vals, lo, hi, tag


def _mdy(s):
    p = s.split("-")
    return f"{int(p[1])}/{int(p[2])}"


def _chart(pairs, rate_now, W, H):
    """심플 라인 + 회색 평균 점선 + 오늘 점(파랑). 반환: (svg, avg)."""
    vals = [c for _, c in pairs]
    n = len(vals)
    if n < 2:
        return "", float(rate_now), rate_now, rate_now
    lo, hi = min(vals), max(vals)
    avg = sum(vals) / n
    rng = (hi - lo) or 1
    pad = 12

    def X(i):
        return pad + (W - 2 * pad) * i / (n - 1)

    def Y(v):
        return pad + (H - 2 * pad) * (1 - (v - lo) / rng)

    poly = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
    yavg = Y(avg)
    return (
        f'<svg class="hchart" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
        f'<line x1="{pad}" y1="{yavg:.1f}" x2="{W-pad}" y2="{yavg:.1f}" '
        f'stroke="#c8cdd6" stroke-width="2.5" stroke-dasharray="9 7"/>'
        f'<polyline points="{poly}" fill="none" stroke="#3b5bdb" stroke-width="4" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{X(n-1):.1f}" cy="{Y(vals[-1]):.1f}" r="9" fill="#3b5bdb" '
        f'stroke="#fff" stroke-width="3"/></svg>',
        avg, lo, hi,
    )


def _avg_txt(label, avg, rate_now):
    delta = rate_now - avg
    if abs(delta) < 0.5:
        return "평균과 비슷해요"
    if delta > 0:
        return f'평균보다 <b>높은</b> 편이에요 <b class="up">▲{abs(delta):,.0f}원</b>'
    return f'평균보다 <b>낮은</b> 편이에요 <b class="dn">▼{abs(delta):,.0f}원</b>'


def slide_graph(factors, rate, series, guides=False):
    """슬라이드 2: 1주/1달/3개월 흐름 (파란 배경)."""
    rate_now = rate.get("rate") or (series[-1] if series else 0)
    today = _brief_date(rate)
    pairs_all = [(p["date"], p["close"]) for p in rate.get("series", [])
                 if "close" in p and "date" in p]
    hero_svg, avg30, lo30, hi30 = _chart(pairs_all[-22:], rate_now, 840, 210)
    big = (
        '<div class="gbig"><div class="grow"><span class="glabel">최근 1달</span>'
        f'<span class="gnow">지금 {rate_now:,.1f}원</span></div>'
        f'{hero_svg}'
        f'<div class="grange"><span>저 {lo30:,.0f}원</span><span>고 {hi30:,.0f}원</span></div>'
        f'<div class="gtag">{_avg_txt("한 달", avg30, rate_now)}</div></div>'
    )
    sm7, avg7, lo7, hi7 = _chart(pairs_all[-7:], rate_now, 360, 140)
    sm90, avg90, lo90, hi90 = _chart(pairs_all[-63:], rate_now, 360, 140)
    small = (
        '<div class="gsmall-wrap">'
        f'<div class="gsmall"><span class="glabel">최근 1주</span>{sm7}'
        f'<div class="grange"><span>저 {lo7:,.0f}</span><span>고 {hi7:,.0f}</span></div>'
        f'<div class="gtag">{_avg_txt("1주", avg7, rate_now)}</div></div>'
        f'<div class="gsmall"><span class="glabel">최근 3개월</span>{sm90}'
        f'<div class="grange"><span>저 {lo90:,.0f}</span><span>고 {hi90:,.0f}</span></div>'
        f'<div class="gtag">{_avg_txt("3개월", avg90, rate_now)}</div></div></div>'
    )
    body = (
        _shead(today)
        + '<div class="stitle">오늘 환율, <span class="kw">최근 흐름</span> 속 어디쯤?</div>'
        + big + small
        + '<div class="snote">며칠 안에 쓸 돈이면 <b>1주</b>, 길게 모을 돈이면 <b>3개월</b>을 보세요!</div>'
        + '<div class="spacer"></div>'
        + '<div class="sfoot">오늘 이 흐름의 이유 →</div>'
    )
    return HEAD + '<div class="page blue">' + body + (GUIDES if guides else "") + "</div>" + FOOT


def slide_summary(factors, rate, series, guides=False):
    """슬라이드 2: 30초 요약 (tldr 3줄, 크게)."""
    today = _brief_date(rate)
    tldr = factors.get("tldr", []) or []
    cards = "".join(
        f'<div class="sc"><span class="sc-n">{i+1}</span><div class="sc-t">{t}</div></div>'
        for i, t in enumerate(tldr[:3])
    )
    body = (
        _shead(today)
        + '<div class="stitle"><span class="kw">30초</span>로 보는 오늘 환율</div>'
        + f'<div class="sc-wrap">{cards}</div>'
        + '<div class="spacer"></div>'
        + '<div class="sfoot">이번 주 주목할 일정 →</div>'
    )
    return HEAD + '<div class="page">' + body + (GUIDES if guides else "") + "</div>" + FOOT


def _dday(ed, today):
    return {0: "오늘", 1: "내일", 2: "모레"}.get((ed - today).days, f"D-{(ed - today).days}")


def slide_calendar(factors, rate, series, guides=False):
    """가까운 일정은 시나리오(▲/▼)로 확장, 나머지는 compact + ★★★(최대 변수) 강조."""
    today = _brief_date(rate)
    fs = sorted(glob.glob(str(ROOT / "output" / "calendar-*.json")))
    cal = json.load(open(fs[-1], encoding="utf-8")) if fs else {"events": []}
    def _valid_iso(s):   # LLM이 '2026-07-00' 같은 무효 날짜를 넣으면 스킵(크래시 방지)
        try:
            dt.date.fromisoformat(s)
            return True
        except (ValueError, TypeError):
            return False
    evs = [e for e in cal.get("events", [])
           if _valid_iso(e.get("date", "")) and e.get("date", "") >= today.isoformat()]
    evs.sort(key=lambda e: e["date"])
    near, rest = (evs[0] if evs else None), evs[1:3]

    near_html = ""
    if near:
        ed = dt.date.fromisoformat(near["date"])
        sc = ""
        for s in (near.get("scenarios") or [])[:2]:
            eff = s.get("effect", "")
            if any(w in eff for w in ("내려", "하락", "내림")):
                arr, cls = "▼", "dn"
            elif any(w in eff for w in ("올라", "상승", "오름")):
                arr, cls = "▲", "up"
            else:
                arr, cls = "↔", "fl"
            sc += (f'<div class="scen"><span class="scen-arr {cls}">{arr}</span>'
                   f'<div class="scen-t"><b>{s.get("cond","")}</b> {eff}</div></div>')
        summ = (near.get("summary") or "").strip()
        near_html = (
            '<div class="evbig"><div class="evbig-row">'
            f'<span class="evbig-dd">{_dday(ed, today)}</span><div class="evbig-main">'
            f'<div class="evbig-title">{near.get("name","")}</div>'
            f'<div class="evbig-sum">{summ}</div>{sc}</div>'
            f'<span class="evbig-star">{"★"*max(1,int(near.get("importance",1)))}</span>'
            '</div></div>'
        )

    rest_html = ""
    for e in rest:
        ed = dt.date.fromisoformat(e["date"])
        imp = int(e.get("importance", 1))
        rsum = (e.get("summary") or "").strip()
        rest_html += (
            f'<div class="evrow"><span class="evrow-dd">{_dday(ed, today)}</span>'
            f'<div class="evrow-main"><div class="evrow-name">{e.get("name","")}</div>'
            f'<div class="evrow-sum">{rsum}</div></div>'
            f'<span class="evrow-star">{"★"*max(1,imp)}</span></div>'
        )

    body = (
        _shead(today)
        + '<div class="stitle">이번 주, 환율 <span class="kw">흔들 수 있는</span> 일정</div>'
        + near_html
        + (f'<div class="evlist-cap">다음 일정</div>{rest_html}' if rest_html else "")
        + '<div class="spacer"></div>'
        + '<div class="sfoot">오늘 환율은 어디쯤? →</div>'
    )
    return HEAD + '<div class="page">' + body + (GUIDES if guides else "") + "</div>" + FOOT


def slide_factor(f, rank, today, is_last=False, guides=False):
    """요인 1장 (이모지+이름+짧은 why+출처). 심플."""
    impact = max(1, min(5, int(f.get("impact", 3))))
    bar = "".join(f'<i class="{"on" if i < impact else ""}"></i>' for i in range(5))
    ilabel = {5: "매우 큼", 4: "큼", 3: "보통", 2: "작음", 1: "미미"}[impact]
    head = (f.get("headline") or "").strip()
    bullets = (f.get("bullets") or [])[:2]
    bul = "".join(f'<div class="fbul-i">{b}</div>' for b in bullets)
    nsrc = len(f.get("sources", []) or [])
    foot = "오늘 환전, 내 상황은? →" if is_last else "다음 요인 →"
    body = (
        _shead(today)
        + '<div class="fmid"><div class="fhdr">'
        + f'<div class="fhdr-badge">{f.get("emoji","")}</div><div class="fhdr-main">'
        + f'<div class="fhdr-rank">환율 형성 요인 {rank}위</div>'
        + f'<div class="fhdr-name">{f.get("name","")}</div>'
        + f'<div class="fimp2"><span class="fbar">{bar}</span>'
        + f'<span class="fimp2-t">영향도 {ilabel}</span></div></div></div>'
        + f'<div class="fwhy">{head}</div>'
        + (f'<div class="fbul">{bul}</div>' if bul else "")
        + (f'<div class="fsrc">📰 출처 {nsrc}곳</div>' if nsrc else "")
        + '</div>'
        + f'<div class="sfoot">{foot}</div>'
    )
    return HEAD + '<div class="page">' + body + (GUIDES if guides else "") + "</div>" + FOOT


def slide_factor_b(f, rank, today, is_last=False, guides=False):
    """요인 B안: 파란 배경 + 흰 카드 한 장에 전부."""
    impact = max(1, min(5, int(f.get("impact", 3))))
    bar = "".join(f'<i class="{"on" if i < impact else ""}"></i>' for i in range(5))
    ilabel = {5: "매우 큼", 4: "큼", 3: "보통", 2: "작음", 1: "미미"}[impact]
    head = (f.get("headline") or "").strip()
    bullets = (f.get("bullets") or [])[:2]
    bul = "".join(f'<div class="fbul-i">{b}</div>' for b in bullets)
    nsrc = len(f.get("sources", []) or [])
    foot = "오늘 환전, 내 상황은? →" if is_last else "다음 요인 →"
    body = (
        _shead(today)
        + '<div class="fmid"><div class="fcardb">'
        + '<div class="fcardb-top">'
        + f'<div class="fhdr-badge">{f.get("emoji","")}</div><div class="fhdr-main">'
        + f'<div class="fhdr-rank">환율 형성 요인 {rank}위</div>'
        + f'<div class="fhdr-name">{f.get("name","")}</div></div></div>'
        + f'<div class="fimp2"><span class="fbar">{bar}</span>'
        + f'<span class="fimp2-t">영향도 {ilabel}</span></div>'
        + f'<div class="fwhy">{head}</div>'
        + (f'<div class="fbul">{bul}</div>' if bul else "")
        + (f'<div class="fsrc">📰 출처 {nsrc}곳</div>' if nsrc else "")
        + '</div></div>'
        + f'<div class="sfoot">{foot}</div>'
    )
    return HEAD + '<div class="page blue">' + body + (GUIDES if guides else "") + "</div>" + FOOT


def slide_factor_c(f, rank, today, is_last=False, guides=False):
    """요인 C안: 회색 배경 + 흰 카드 한 장 (B 구조 + A 색 리듬)."""
    impact = max(1, min(5, int(f.get("impact", 3))))
    bar = "".join(f'<i class="{"on" if i < impact else ""}"></i>' for i in range(5))
    ilabel = {5: "매우 큼", 4: "큼", 3: "보통", 2: "작음", 1: "미미"}[impact]
    head = (f.get("headline") or "").strip()
    bullets = (f.get("bullets") or [])[:2]
    bul = "".join(f'<div class="fbul-i">{b}</div>' for b in bullets)
    nsrc = len(f.get("sources", []) or [])
    foot = "오늘 환전, 내 상황은? →" if is_last else "다음 요인 →"
    body = (
        _shead(today)
        + '<div class="fmid"><div class="fcardb gray">'
        + '<div class="fcardb-top">'
        + f'<div class="fhdr-badge">{f.get("emoji","")}</div><div class="fhdr-main">'
        + f'<div class="fhdr-rank">환율 형성 요인 {rank}위</div>'
        + f'<div class="fhdr-name">{f.get("name","")}</div></div></div>'
        + f'<div class="fimp2"><span class="fbar">{bar}</span>'
        + f'<span class="fimp2-t">영향도 {ilabel}</span></div>'
        + f'<div class="fwhy">{head}</div>'
        + (f'<div class="fbul">{bul}</div>' if bul else "")
        + (f'<div class="fsrc">📰 출처 {nsrc}곳</div>' if nsrc else "")
        + '</div></div>'
        + f'<div class="sfoot">{foot}</div>'
    )
    return HEAD + '<div class="page">' + body + (GUIDES if guides else "") + "</div>" + FOOT


def render(html, out_path, scale=2):
    from playwright.sync_api import sync_playwright
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=scale)
        pg.set_content(html, wait_until="networkidle")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(out_path), clip={"x": 0, "y": 0, "width": 1080, "height": 1350})
        b.close()
    return out_path


# 인스타 캡션 톤·구조 고정용 예시(2026-06-16 작성분). 사실은 매일 바뀌고 톤만 따른다.
CAPTION_EXAMPLE = """🐶 오늘 환전하개?

원/달러 환율은 어제와 거의 같은 **1,516원, 제자리**예요.
미·이란 종전 합의로 달러 수요가 줄었지만, 미국 물가(CPI)가 4.2%로 높게 나오면서 달러를 다시 떠받치고 있거든요. 그래서 1,500원대에서 안 내려오고 있어요.

내일은 미국이 금리를 정하는 회의(FOMC)가 있어요. 케빈 워시 새 의장의 첫 회의라, 결과에 따라 환율이 다시 움직일 수 있어요. 👀

⸻

매달 달러로 바꾸는 분들을 위해, 매일 아침 '오늘 환전해도 될까?'를 쉽게 정리해드려요.
팔로우하고 매일 같이 확인해요 🐾

* 예보가 아니라 오늘까지의 사실 정리예요. 투자 조언이 아니에요.

#환율 #환전 #원달러환율 #환전타이밍 #달러환율"""


# 한 번 풀이한 용어는 다음부터 '이미 아시겠지만'을 붙인다 — 그 추적용 원장.
TERM_LEDGER = ROOT / "output" / "explained_terms.json"
# 캡션에서 풀이할 만한 환율·경제 용어(반복 감지용). 캡션에 등장하면 '설명함'으로 기록.
GLOSS_TERMS = [
    "약보합", "강보합", "보합", "횡보", "급락", "급등", "원화 약세", "원화 강세",
    "약세", "강세", "순매수", "순매도", "FOMC", "CPI", "PPI", "연준", "DXY",
    "위험회피", "위험선호", "긴축", "완화", "기준금리",
]


def _load_ledger():
    try:
        return json.load(open(TERM_LEDGER, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _update_ledger(caption, ledger):
    seen = set(ledger)
    for t in GLOSS_TERMS:
        if t in caption:
            seen.add(t)
    out = sorted(seen)
    try:
        TERM_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        TERM_LEDGER.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def build_caption(factors, rate, model="claude-sonnet-5"):
    """오늘 캐러셀과 함께 쓸 인스타 캡션을 생성한다(어제 톤 고정 + 헤드라인 환율 용어 풀이 포함).
    숫자는 rate.json(현재/어제/전일대비) 단일 소스만. 실패해도 카드 생성은 막지 않게 best-effort.
    이미 풀이했던 용어는 '이미 아시겠지만'을 붙여 재설명한다(원장 TERM_LEDGER)."""
    try:
        from dotenv import load_dotenv
        from anthropic import Anthropic
    except ImportError:
        return None
    load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    rnd = lambda x: int(x + 0.5) if isinstance(x, (int, float)) else None  # JS Math.round(half-up, 양수)
    cur, prev = rnd(rate.get("rate")), rnd(rate.get("prev"))
    dr = (cur - prev) if (cur is not None and prev is not None) else None
    chg = ("보합(어제와 비슷, 0원)" if dr == 0 else
           f"{'▲' if dr > 0 else '▼'}{abs(dr):,}원 (어제보다 {'올라' if dr > 0 else '내려'})" if dr is not None else "")
    ctx = (f"현재 {cur:,}원" if cur is not None else "") + \
          (f" / 어제 {prev:,}원 / 전일대비 {chg}" if prev is not None else "")
    facs = factors.get("factors") or []
    fac_lines = "\n".join(f"- {f.get('emoji','')} {f.get('name','')}: {f.get('headline','')}" for f in facs[:4])
    ledger = _load_ledger()
    known = ", ".join(ledger) if ledger else "(아직 없음)"
    prompt = (
        "너는 인스타그램 계정 @when2exchange(환전타이밍)의 캡션 작가다. "
        "아래 [예시 캡션]의 톤·구조·길이를 그대로 따라 [오늘 데이터]로 새 캡션을 써라.\n\n"
        f"[예시 캡션 — 톤·구조만 참고, 사실은 무시]\n{CAPTION_EXAMPLE}\n\n"
        f"[오늘 데이터]\n"
        f"표지 헤드라인: {factors.get('card_title','')}\n"
        f"환율(단일 소스, 이 숫자만 쓸 것): {ctx}\n"
        f"30초요약:\n- " + "\n- ".join(factors.get("tldr", []) or []) + "\n"
        f"종합: {factors.get('overall_why','')}\n"
        f"오늘의 요인:\n{fac_lines}\n\n"
        "■ 규칙:\n"
        "- 구조 순서 고정: ①🐶 오늘 환전하개? ②환율+상태(**볼드**, 표지 헤드라인의 상태 표현을 그대로 살릴 것 "
        "— 명사형이면 '**1,513원, 약보합**'예요처럼. ※헤드라인이 '~다'로 끝나는 문장이면 '멈췄다예요'처럼 "
        "붙이지 말고 해요체로 자연스럽게 풀 것 — 예: '원화 강세 흐름 멈췄다' → '**1,508.5원**, 원화 강세 흐름이 멈췄어요') "
        "③(표지 헤드라인에 환율 용어가 있으면 "
        "그 뜻을 *한 줄로 쉽게 풀이* — 예: '약보합=큰 변화 없이 살짝 약세') ④쉬운말 인과 2~3문장(거든요/예요) "
        "⑤다음에 지켜볼 이벤트 한 문장 👀 ⑥⸻ ⑦계정 소개+팔로우 🐾 (페르소나 '유학생·여행자·투자자' 나열 금지) ⑧* 예보 아님·투자조언 아님 ⑨해시태그 5개\n"
        "- 환율 숫자(현재·어제·전일대비)는 위 [오늘 데이터]의 값만. 다른 숫자 지어내기 금지.\n"
        "- 해요체, 쉬운 말, 채움말·과장·예보·낚시 금지. 중학생도 이해하게.\n"
        "- 헤드라인의 환율 용어(약보합·강보합·보합·급락·급등 등)는 반드시 한 줄 풀이를 넣을 것.\n"
        f"- [이미 설명한 단어] 목록: {known}. 오늘 이 목록의 단어를 다시 풀이/설명하게 되면 그 풀이 "
        "앞에 '이미 아시겠지만'을 자연스럽게 붙여 반복 학습임을 알린다(예: '이미 아시겠지만, 보합은 …'). "
        "목록에 없는 새 단어는 평소대로 처음 설명하듯 풀이한다.\n"
        "- 해시태그는 #환율 #환전 #원달러환율 #환전타이밍 + 오늘 토픽 1개(예: #FOMC).\n"
        "- 캡션 본문만 출력(설명·코드펜스 없이)."
    )
    try:
        resp = Anthropic(api_key=key).messages.create(
            model=model, max_tokens=6000, messages=[{"role": "user", "content": prompt}])
        caption = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    except Exception as exc:
        print(f"캡션 생성 건너뜀({exc})", file=sys.stderr)
        return None
    if caption:
        _update_ledger(caption, ledger)   # 오늘 풀이한 용어를 원장에 누적 → 다음부터 '이미 아시겠지만'
    return caption


def _heat_brief(factors, rate):
    """표지 제목 추천(에이전트가 5개 제안)을 돕는 근거 — heat·임박일정·어제 제목."""
    today = _brief_date(rate)
    r, p = rate.get("rate"), rate.get("prev")
    move = (f"{r - p:+.0f}원" if isinstance(r, (int, float)) and isinstance(p, (int, float)) else "?")
    cfs = sorted(glob.glob(str(ROOT / "output" / "calendar-*.json")))
    imminent = []
    if cfs:
        for e in json.load(open(cfs[-1], encoding="utf-8")).get("events", []):
            try:
                d = (dt.date.fromisoformat(e.get("date", "")) - today).days
            except ValueError:
                continue
            if int(e.get("importance", 0)) >= 3 and 0 <= d <= 5:
                imminent.append((e, d))
    imminent.sort(key=lambda x: x[1])
    event_hot = any(d <= 1 for _, d in imminent)
    move_hot = isinstance(r, (int, float)) and isinstance(p, (int, float)) and abs(r - p) >= 10
    heat = "high" if (event_hot or move_hot) else "normal"
    # 어제 제목(직전 factors)
    fs = sorted(glob.glob(str(ROOT / "output" / "factors-*.json")))
    prev_title = ""
    if len(fs) >= 2:
        try:
            prev_title = (json.load(open(fs[-2], encoding="utf-8")).get("card_title") or "").strip()
        except (OSError, json.JSONDecodeError):
            pass
    lines = ["─" * 48, f"📌 제목 추천 근거  (heat={heat}, 전일대비 {move})"]
    if prev_title:
        lines.append(f"  · 어제 제목(차별화 기준): {prev_title}")
    for e, d in imminent[:4]:
        dd = "오늘(D-0)" if d == 0 else ("내일(D-1)" if d == 1 else f"D-{d}")
        lines.append(f"  · 임박 ★★★: {e.get('name','')} = {dd}")
    facs = factors.get("factors") or []
    if facs:
        lines.append(f"  · 오늘 1순위 요인: {facs[0].get('emoji','')} {facs[0].get('name','')} — {facs[0].get('headline','')}")
    if heat == "high":
        lines.append("  → heat=high: 표지에 빨강 배지(--badge '⚡ 이번주 최대 변수, 오늘') + 후킹(--hook) 권장")
    lines.append("─" * 48)
    return "\n".join(lines)


def _gate(factors, rate, force):
    """발행 게이트. blocks 있으면 게시 금지(--force로만 강행). warns는 출력만."""
    blocks, warns = validate_facts(factors, rate)
    for w in warns:
        print("  ⚠ " + w, file=sys.stderr)
    if blocks:
        print("=" * 56, file=sys.stderr)
        print("❌ 검증 실패 — 게시 금지! (사실/정직성 위반)", file=sys.stderr)
        for b in blocks:
            print("  • " + b, file=sys.stderr)
        print("=" * 56, file=sys.stderr)
        if not force:
            print("→ 제목/텍스트 수정 후 다시. (강행하려면 --force)", file=sys.stderr)
            sys.exit(2)
        print("⚠️ --force: 검증 실패에도 강행함.", file=sys.stderr)


def main():
    for stream in (sys.stdout, sys.stderr):   # Windows cp949 이모지 깨짐 방지
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "output" / "cards"))
    ap.add_argument("--slides", action="store_true",
                    help="본문(2~8p)만 렌더 + 제목 추천 근거 출력(표지 제외).")
    ap.add_argument("--cover", action="store_true", help="표지(1p)만 렌더.")
    ap.add_argument("--cover-only", action="store_true", help="(별칭) --cover와 동일.")
    ap.add_argument("--title", default=None, help="표지 제목 덮어쓰기(고른 제목).")
    ap.add_argument("--badge", default=None, help="heat 배지 문구(예: '⚡ 이번주 최대 변수, 오늘').")
    ap.add_argument("--hook", default="", help="표지 후킹 문구(주목 유도, 행동지시 금지).")
    ap.add_argument("--guides", action="store_true")
    ap.add_argument("--no-caption", action="store_true", help="인스타 캡션 생성 생략")
    ap.add_argument("--force", action="store_true", help="검증 실패에도 강행(주의).")
    args = ap.parse_args()

    factors, rate, series = load_data()
    if not series:
        print("ERROR: rate.json series 비어있음", file=sys.stderr)
        sys.exit(1)
    if args.title is not None:
        factors["card_title"] = args.title.strip()

    if _brief_date(rate).weekday() >= 5:  # 토(5)·일(6) = 외환시장 휴장
        print("⚠️ 주말(외환시장 휴장) — 환율이 금요일 종가로 고정. 데일리 브리핑 대신 "
              "주말 콘텐츠(토=주간 회고/track record, 일=다음주 예고) 권장.", file=sys.stderr)

    out_dir = Path(args.out) / _brief_date(rate).isoformat()
    today = _brief_date(rate)
    mode_cover = args.cover or args.cover_only
    do_slides = not mode_cover           # cover 단독이 아니면 본문 렌더
    do_cover = not args.slides           # slides 단독이 아니면 표지 렌더

    # 본문(2~8p) — 제목 없이도 만들 수 있어 게이트 불필요.
    if do_slides:
        print(f"30초: {render(slide_summary(factors, rate, series), out_dir / '02-summary.png')}")
        print(f"일정: {render(slide_calendar(factors, rate, series), out_dir / '03-event.png')}")
        print(f"그래프: {render(slide_graph(factors, rate, series), out_dir / '04-graph.png')}")
        facs = (factors.get("factors") or [])[:4]
        for i, f in enumerate(facs):
            render(slide_factor_c(f, i + 1, today, is_last=(i == len(facs) - 1)),
                   out_dir / f"{5+i:02d}-factor{i+1}.png")
        print(f"요인 {len(facs)}장")
        # '왜 주목?' 페이지 — heat=high이고 attention 필드 있는 날만(마무리 직전). 요인 → 왜주목 → 마무리.
        nxt = 5 + len(facs)
        if factors.get("attention", {}).get("reasons"):
            print(f"왜주목: {render(slide_attention(factors, rate), out_dir / f'{nxt:02d}-attention.png')}")
            nxt += 1
        # 마지막 페이지(닫기) — 페르소나별 환전 신호 + 팔로우 CTA. 마지막 요인의 "내 상황은? →"가 여기로.
        print(f"마무리: {render(slide_signal(factors, rate, series), out_dir / f'{nxt:02d}-signal.png')}")

    # 표지(1p) — 제목이 들어가는 곳 → 검증 게이트(실패 시 게시 금지).
    if do_cover:
        _gate(factors, rate, args.force)
        print(f"표지: {render(cover_html(factors, rate, series, badge=args.badge, hook=args.hook), out_dir / '01-cover.png')}")
        if args.guides:
            render(cover_html(factors, rate, series, guides=True, badge=args.badge, hook=args.hook),
                   out_dir / "01-cover-guides.png")

    # slides 모드면 제목 추천 근거를 띄워 준다(에이전트가 5개 제안 → 사용자 선택 → --cover).
    if args.slides:
        print(_heat_brief(factors, rate))

    rate_now = rate.get("rate") or series[-1]
    print(f"환율: {rate_now:,.1f}원 / 페르소나 판정:")
    for key, ico, name in PERSONA:
        v = persona_verdict(series, rate_now, key) or VERDICT[1]
        print(f"  {ico} {name}: {v['dot']} {v['text']}")

    # 인스타 캡션도 카드와 함께 생성(어제 톤 고정 + 헤드라인 용어 풀이). caption.txt로 저장.
    if not args.no_caption and not args.slides:
        cap = build_caption(factors, rate)
        if cap:
            cap_path = out_dir / "caption.txt"
            cap_path.write_text(cap, encoding="utf-8")
            print("\n" + "=" * 56 + f"\n📝 인스타 캡션  ({cap_path})\n" + "=" * 56)
            print(cap)


if __name__ == "__main__":
    main()

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
import html
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta, date
from xml.etree import ElementTree as ET

from dotenv import load_dotenv

# Free news search (Google News RSS, no API key). Standard library only.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_RSS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

# Fixed drivers of USD/KRW. Each has a Korean news query tuned for FX relevance.
# F1–F10 = 표준 거시 동인. F11(기타) = 광역 캐치올 — 10개 틀 밖 이슈를 풀에 넣고, 강한 동인이
# 4개 안 되는 날 '앞 카드와 안 겹치는' 4번째 칸을 채운다. F12 = 국가신용·정책 리스크(원화 고유
# 리스크). 정치색은 본 프롬프트의 '정치 중립=시장 영향만' 규칙으로 차단한다.
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
    {"id": "F11", "emoji": "🔎", "name": "그 외 환율 이슈", "query": "원달러 환율 외환시장 마감 환율 급등 하락 이유"},
    {"id": "F12", "emoji": "🏦", "name": "국가신용·정책 리스크", "query": "한국 신용등급 재정 정치 불확실성 외국인 자금 환율"},
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
    "(7) 현재가·어제 종가·전일대비는 *반드시* [현재 환율 맥락]의 값(정수)만 쓸 것 — 화면 상단 박스와 "
    "동일한 단일 소스다. 오늘 장중 고가·저가(급등·급락)는 뉴스 근거가 있으면 사실로 덧붙여도 되나, "
    "현재가·어제 종가와 혼동시키지 말고 다른 날·다른 출처 수치(예: 지난주 1,511원)를 오늘 상태로 "
    "끌어오지 말 것. 맥락이 '보합/어제와 비슷'이면 변화를 지어내지 말 것. "
    "(8) 인과관계는 중간 고리를 건너뛰지 말 것. 예: '고용 호조 → 달러 강세'(X). "
    "'고용이 강하게 나오자 → 연준의 금리 인하 기대가 줄고(혹은 인상 우려가 커지고) → "
    "미국 금리가 올라 → 그 이자를 좇아 달러 수요가 늘어 → 달러가 강해졌다'처럼 핵심 "
    "연결고리를 반드시 넣되, 장황하지 않게. "
    "(9) 모든 문장은 '~다/~했다'로 끝나는 담백한 문어체 평서문으로 일관. "
    "해요체(~요/~예요)·반말·과격식(~습니다) 금지. "
    "(10) 역할(반복 금지): overall_why는 *요인들을 하나의 인과로 잇는 종합*이다. tldr이나 "
    "요인 카드의 문장·수치를 그대로 옮겨 적지 말고, 개별 사실의 나열이 아니라 '이 힘들이 왜 "
    "동시에 작동해 오늘 환율을 이렇게 만들었는지'를 한 줄기로 꿰어 설명할 것."
)

# Shared TL;DR rule (full run + --rewrite-why use the same wording).
TLDR_RULES = (
    "오늘 상황을 비전문가(중학생)도 한 번에 이해할 핵심만 3줄. 각 한 문장, 짧고 쉬운 말, 채움말 금지. "
    "순서: ① *어제 대비 오늘 새로 바뀐 점*(환율이 더 내렸는지·올랐는지·비슷한지, 새로 나온 지표 결과, "
    "임박한 이벤트가 하루 더 다가옴 등)을 맨 앞에 → ② 그 배경·가장 큰 이유 → ③ 지켜볼 것. "
    "■ 매일 새 느낌(가장 중요): 아래 [직전 발행 30초요약]과 첫 문장이 거의 같으면 실패다. 같은 사건"
    "(예: 종전 협상·이번 주 FOMC)이 며칠째 이어져도, '서 있는 상황'을 처음부터 다시 설명하지 말고 "
    "어제 대비 *달라진 점*을 ①에 앞세워 매일 다르게 읽히게 할 것. 단 진짜로 달라진 게 없으면 억지로 "
    "지어내지 말고 '어제와 비슷한 ○○원대 보합'이라고 정직하게(드라마 금지). "
    "■ 환율 숫자 규칙(매우 중요): *현재가·어제 종가·전일대비* 이 셋은 반드시 [현재 환율 맥락]의 값(정수)을 "
    "그대로 쓴다 — 화면 상단 박스와 동일한 단일 소스라, 다른 값으로 쓰면 박스와 어긋나 거짓이 된다. "
    "전일대비는 맥락의 방향·폭(예: '보합', '▲1원')을 그대로 따르고 '보합/어제와 비슷'이면 억지 변화를 지어내지 말 것. "
    "단 *오늘 장중 고가·저가*(예: '장중 1,504원까지 급락했다가')는 뉴스 근거가 있으면 사실로 덧붙여도 좋다 — "
    "단 그것이 *오늘*의 장중 움직임일 때만, 그리고 현재가·어제 종가와 혼동시키지 말 것. 다른 날·다른 출처 수치"
    "(예: 지난주 1,511원)를 오늘 환율 상태로 끌어오지 말 것. "
    "■ ② 인과의 *핵심 고리*만 풀어 준다(예: '달러는 불안할 때 찾는 안전한 돈이라'). 뻔한 중간 단계는 생략. "
    "■ ③ *중립적 사실/지켜볼 것*만(예: '이번 주 FOMC 결과에 따라 더 움직일 수 있다'). "
    "'서두르지 말라/지금 사라/기다려라' 같은 타이밍 조언은 금지 — 상황(여행·투자·송금)마다 달라 투자조언이 된다. "
    "■ 쉬운 말: 어려운 전문용어는 풀 것(순매수→사들이다, 가시화→보이기 시작, 완화→누그러지다, 위험회피·긴축 등). "
    "단 상승·하락·금리·환율·매수·매도는 일상어라 그대로 OK. "
    "문장은 친근한 해요체로 끝낸다(예: '~됐어요', '~샀어요', '~움직일 수 있어요'). 반말·과장·예보·낚시 금지."
)

# 지난 브리핑 목록에 쓰는 짧은 후크 제목. 다체가 아니라 클릭을 부르는 캐주얼 존댓말.
CARD_TITLE_RULES = (
    "'지난 브리핑' 목록에 쓰일, 그날 환율의 핵심 이슈를 담은 임팩트 있는 헤드라인 한 줄. "
    "그날 환율을 움직인 1순위 '원인'(예: FOMC·중동 정세·미국 고용지표·달러 강세·외국인 매도 등)과 "
    "그 결과(원화 약세/강세, 급등/급락 등)를 뉴스 헤드라인처럼 단정적으로. "
    "한국어 18자 이내(짧을수록 좋다, 길면 잘림). "
    "■ 환율·변동 수치(예: '1,527원', '1530', '1520선', '9원', '10원 급락')는 절대 넣지 말 것 — "
    "환율과 전일 대비 변동(▲/▼)은 목록에 이미 따로 표시되므로 중복이고, 숫자를 넣으면 그날 실제 변동과 어긋날 수 있다. "
    "숫자 대신 '원인·이슈'로. "
    "■ 막연한 '환율 높아요/비싸졌어요' 류 금지 — 무엇 때문에(원인) 어떻게 됐는지(약세/강세 등) 구체 이슈로. "
    "독자가 '무슨 일이지?' 하고 눌러보고 싶게 그날의 핵심을 또렷이 짚을 것. "
    "단 큰 이슈가 없는 잠잠한 날은 억지 드라마 금지, 차분히(예: '큰 이슈 없이 보합'). "
    "오늘의 핵심 사실에 근거(과장·낚시 금지). 이모지·따옴표 없이 글자만. "
    "예: 'FOMC 앞두고 원화 약세 가속', '중동 리스크 완화에 환율 급락', "
    "'미국 고용 깜짝에 달러 강세', '외국인 매도에 환율 출렁'. "
    "■ 그날 '결과'(급락/급등/강세/약세)는 [현재 환율 맥락]의 *실제 전일대비(종가)* 방향·강도와 일치해야 한다. "
    "전일대비가 작으면(±5원 미만) '급락/급등'으로 단정 금지(보합/소폭). 장중 드라마를 담고 싶으면 '장중'을 "
    "명시할 것 — 모범: '장중 1504 찍고 반등'(장중=명시, 종가는 배지가 따로 보여줌)."
)

# 환율 레벨·움직임 그라운딩 — 헤드라인·불릿 공통. 장중 수치는 *유지하되 '장중'으로 명시*,
# 그날 종가·전일대비(배지·현재가)는 [현재 환율 맥락] 단일 소스. 둘을 뒤섞지 않는다.
LEVEL_RULE = (
    "■ 환율 레벨·움직임 규칙(매우 중요): 헤드라인·불릿에서 '그날 환율 레벨/마감/전일대비'를 말할 땐 "
    "반드시 [현재 환율 맥락]의 *종가·전일대비*(정수)와 일치시킨다 — 화면 배지와 동일한 단일 소스다. "
    "기사 속 장중 고점·저점(예: '장중 1,504원까지 급락')은 *사실이니 빼지 말고 살리되, 반드시 '장중'으로 "
    "명시*하고 그날 종가·결론으로 둔갑시키지 말 것. 다른 날·다른 기준 수치(예: 어제 장중 고점 1,530원)를 "
    "'오늘 종가/움직임'으로 끌어오지 말 것. 실제 전일대비(종가)가 작으면(±5원 미만) 그날 결론을 "
    "'급락/급등'으로 단정하지 말고 '보합/소폭'으로 쓰되, 장중 급등락은 '장중'으로 따로 적는다. "
    "단 이 규칙은 headline·bullets·요약에만 적용되고, *card_title(지난 브리핑 제목)에는 환율 숫자를 "
    "절대 넣지 않는다*(CARD_TITLE_RULES대로 원인·이슈로만 — 숫자는 목록 배지가 따로 보여준다). "
    "또 '오를 것/내릴 것/오를 전망' 같은 예보·단정 표현은 금지(원인·현상만 적는다), "
    "tldr·overall_why엔 '전일대비 ±N원' 같은 정밀 숫자를 박지 말 것 — 박스가 보여주고 "
    "장중에 환율이 움직이면 그 숫자가 어긋난다(방향은 숫자 없이 원인으로 설명)."
)


# ── 검증·heat·차별화 (제목 엔진 = 카드뉴스·웹 공유 첫인상 보호) ─────────────────
# 날짜/D-day·이벤트 정렬 점검에 쓰는 핵심 이벤트 키워드.
EVENT_KW = ["FOMC", "CPI", "PPI", "금통위", "고용", "비농업", "소비자물가",
            "생산자물가", "연준", "한국은행", "금리결정", "GDP", "PCE", "ECB"]
# 제목/요약에서 금지되는 예보·협박·낚시 표현(§6 인과정직·§0 광고주 톤).
FORECAST_BAN = ["오를 것", "내릴 것", "오를 전망", "내릴 전망", "급등 예고", "급락 예고",
                "폭등", "폭락 예고", "마지막 기회", "지금 사", "지금 팔", "무조건",
                "반드시 사", "후회", "수익 보장", "이득 보장"]
# 예보·낚시 금지어 → 안전 표현 치환(자가치유, API 0). validate_briefing 직전에 적용해
# 게이트 차단(=사람이 수동으로 고치던 일)을 무인으로 푼다.
_FORECAST_FIX = [
    (r"오를\s*것", "상승 흐름"), (r"내릴\s*것", "하락 흐름"),
    (r"오를\s*전망", "상승 흐름"), (r"내릴\s*전망", "하락 흐름"),
    (r"급등\s*예고", "상승 흐름"), (r"급락\s*예고", "하락 흐름"),
    (r"폭락\s*예고", "하락 흐름"), (r"폭등", "강한 상승"),
    (r"마지막\s*기회", ""), (r"무조건", ""), (r"반드시\s*사\S*", ""),
    (r"지금\s*사\S*", ""), (r"지금\s*팔\S*", ""),
    (r"수익\s*보장", ""), (r"이득\s*보장", ""), (r"후회", ""),
]
# 정밀 전일대비(±N원 + 방향)는 환율 박스가 보여주므로 본문에선 '숫자만' 떼낸다(방향어는 유지).
# → light가 옛 텍스트를 재사용해도 '2원 내린' ↔ 실제 상승 같은 desync가 원천 차단됨.
_DELTA_NUM_RE = re.compile(
    r"\d+\s*원\s*(?:넘게|이상|가까이|가량)?\s*"
    r"(하락|내려|내린|떨어|급락|상승|올라|오른|급등)")


def _heal_text(s: str) -> str:
    if not s:
        return s
    for pat, rep in _FORECAST_FIX:
        s = re.sub(pat, rep, s)
    s = _DELTA_NUM_RE.sub(lambda m: m.group(1), s)   # 숫자만 제거, 방향어 유지
    return re.sub(r"\s{2,}", " ", s).strip()


def _heal_intraday_close(s: str, rate: dict, today) -> str:
    """장중(마감 전)에 '오늘 N원 마감/종가' 단정을 '현재 N원'으로 바꾼다(거짓 종가 방지, API 0).
    어제/전일/야간 종가나 현재가와 2원 넘게 다른 숫자는 그대로 둔다(check-A와 동일 판정).
    마감 후(또는 시각 불명)면 종가 표현이 정상이므로 손대지 않는다."""
    if not s or not isinstance(rate, dict):
        return s
    if market_closed(rate.get("asof", ""), today) is not False:
        return s
    cur = rate.get("rate")
    if not isinstance(cur, (int, float)):
        return s
    cur_i = int(cur + 0.5)

    def _repl(m):
        try:
            n = int(m.group(1).replace(",", ""))
        except ValueError:
            return m.group(0)
        pre = s[max(0, m.start() - 6):m.start()]
        if abs(n - cur_i) > 2 or re.search(r"(어제|전일|지난|어젯|야간)", pre):
            return m.group(0)            # 과거 종가·다른 숫자는 유지
        return f"현재 {m.group(1)}원"     # '마감/종가' 단정 → 현재가 표현

    return re.sub(r"(?:오늘\s*)?(\d[\d,]*)\s*원\s*(?:에|으로|선에서?)?\s*(?:마쳤|마감|종가)[가-힣]*",
                  _repl, s)


def sanitize_copy(payload: dict, rate: dict = None, today=None) -> dict:
    """예보 금지어·정밀 델타숫자·장중 거짓종가를 코드로 중화(API 0). validate 직전·build_site에서 호출.
    멱등(여러 번 적용해도 동일) — full엔 무해, light 재사용분 desync를 무인으로 해소.
    rate·today를 주면 장중 거짓종가(check-A)까지 자가치유한다."""
    def h(x):
        x = _heal_text(x)
        if rate is not None and today is not None:
            x = _heal_intraday_close(x, rate, today)
        return x
    if payload.get("card_title"):
        payload["card_title"] = h(payload["card_title"])
    if payload.get("overall_why"):
        payload["overall_why"] = h(payload["overall_why"])
    if payload.get("tldr"):
        payload["tldr"] = [h(t) for t in payload["tldr"] if t]
    for f in (payload.get("factors") or []):
        for k in ("headline", "impact_reason"):
            if f.get(k):
                f[k] = h(f[k])
        if f.get("bullets"):
            f["bullets"] = [h(b) for b in f["bullets"] if b]
    return payload
# 제목 desync 방지: 원/달러 환율류 숫자(1,513원·1513·1520선 등)는 제목 금지.
_RATE_NUM_RE = re.compile(r"\d[\d,]*\s*원|\b1[0-9]{3}\b|\d{3,}\s*선")
# check A: 오늘 종가 단정 단어(앞에 어제/전일/지난/야간 없을 때만).
_CLOSE_WORD_RE = re.compile(r"(마쳤|마감(?:했|됐|을|한|하)|종가|거래를 마)")


def market_closed(asof, today):
    """서울 외환시장 마감 여부. 주말=마감. 평일=asof 시각 ≥15:30. 시각 불명=None."""
    if today.weekday() >= 5:
        return True
    m = re.search(r"(\d{1,2}):(\d{2})", asof or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2))) >= (15, 30)


def check_intraday_close(text, asof, today, rate=None):
    """장중(마감 전)인데 '오늘 종가/마감'을 단정하면 메시지 반환(없으면 None). carousel과 동일 로직.
    오늘 환율 숫자(±2)에 마감/종가가 붙은 경우만 잡고, 그 숫자 앞 어제/전일/야간은 제외."""
    if market_closed(asof, today) is not False:
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
        if abs(n - cur_i) > 2:
            continue
        if re.search(r"(어제|전일|지난|어젯|야간)", text[max(0, m.start() - 6):m.start()]):
            continue
        return (f"장중(마감 전, asof {asof})인데 오늘({cur_i:,}원) '{m.group(2)}' 단정 "
                "— 15:30 전이면 '현재 ○○원'으로")
    return None


def market_status_block(rate, today):
    """프롬프트 주입용 시장 상태 — 장중이면 '오늘 마감/종가' 금지 지시(check A 예방)."""
    closed = market_closed(rate.get("asof", ""), today)
    if closed is False:
        return ("[시장 상태] 장중(마감 전, 15:30 전 — asof " + str(rate.get("asof", "")) + "). "
                "오늘 환율을 '종가/마감/마쳤다'로 쓰지 말 것. '현재 ○○원'·'지금 ○○원'으로. "
                "단 *어제·전일·야간* 거래의 종가는 그대로 OK.")
    if closed is True:
        return "[시장 상태] 장 마감 후 — 오늘 종가/마감 표현 사용 가능."
    return ""


def _output_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")


def load_rate_obj() -> dict:
    """site/rate.json 전체 객체(heat·검증이 rate/prev/series를 직접 본다)."""
    path = os.path.join(os.path.dirname(_output_dir()), "site", "rate.json")
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_calendar_events() -> list:
    """최신 calendar-*.json의 events. carousel/검증이 D-day·정렬 점검에 쓴다."""
    files = sorted(glob.glob(os.path.join(_output_dir(), "calendar-*.json")))
    if not files:
        return []
    try:
        return json.load(open(files[-1], encoding="utf-8")).get("events", []) or []
    except (OSError, json.JSONDecodeError):
        return []


def prev_card_title(exclude: str = "") -> str:
    """직전 발행 factors의 card_title — 오늘 제목이 어제와 차별화되도록 비교 기준."""
    ex = os.path.abspath(exclude) if exclude else ""
    files = [f for f in glob.glob(os.path.join("output", "factors-*.json"))
             if os.path.abspath(f) != ex]
    if not files:
        return ""
    try:
        d = json.load(open(max(files, key=os.path.getmtime), encoding="utf-8"))
        return (d.get("card_title") or "").strip()
    except (OSError, json.JSONDecodeError):
        return ""


def imminent_events(cal_events, today, max_days=7):
    """importance≥3(★★★) 이면서 오늘~max_days 안에 있는 일정 → [(event, d-day), …] 가까운 순."""
    out = []
    for e in cal_events:
        try:
            d = (date.fromisoformat(e.get("date", "")) - today).days
        except ValueError:
            continue
        if int(e.get("importance", 0)) >= 3 and 0 <= d <= max_days:
            out.append((e, d))
    out.sort(key=lambda x: x[1])
    return out


def compute_heat(rate, cal_events, today):
    """이슈 강도. high = ★★★ 임박(D-0~1) or 실제 |전일대비|≥10원. → (level, reason, badge)."""
    reasons, event_hot, move_hot = [], False, False
    for e, d in imminent_events(cal_events, today, max_days=1):
        event_hot = True
        reasons.append(f"{(e.get('name') or '')[:14]} D-{d}")
    r, p = rate.get("rate"), rate.get("prev")
    if isinstance(r, (int, float)) and isinstance(p, (int, float)) and abs(r - p) >= 10:
        move_hot = True
        reasons.append(f"전일대비 {r - p:+.0f}원")
    level = "high" if (event_hot or move_hot) else "normal"
    badge = "⚡ 오늘 주목" if level == "high" else ""
    return level, "; ".join(reasons), badge


def dday_prompt_context(cal_events, today) -> str:
    """제목 생성용 — 임박 ★★★ 일정의 정확한 D-day(당일/내일 오표기 방지)."""
    ev = imminent_events(cal_events, today, max_days=5)
    if not ev:
        return "(임박한 ★★★ 일정 없음)"
    wd = ["월", "화", "수", "목", "금", "토", "일"]
    lines = []
    for e, d in ev[:4]:
        dd = "오늘(D-0)" if d == 0 else ("내일(D-1)" if d == 1 else
                                        ("모레(D-2)" if d == 2 else f"D-{d}"))
        ed = date.fromisoformat(e["date"])
        lines.append(f"- {e.get('name','')}: {e['date']}({wd[ed.weekday()]}) = {dd}")
    return "\n".join(lines)


def title_engine_context(rate, cal_events, today, exclude=""):
    """제목 생성 프롬프트에 끼울 동적 블록(어제 제목·D-day·heat) + heat 메타."""
    level, reason, badge = compute_heat(rate, cal_events, today)
    prev = prev_card_title(exclude)
    block = (
        f"[어제 제목] {prev or '(없음)'}\n"
        f"[임박 ★★★ 일정(D-day 정확)]\n{dday_prompt_context(cal_events, today)}\n"
        f"[오늘 이슈 강도] heat={level}" + (f" ({reason})" if reason else "")
    )
    return block, {"heat": level, "heat_reason": reason, "title_badge": badge}


# 동적 제목 규칙(어제 차별화·날짜 정확·heat 후크) — 위 context 블록과 함께 쓴다.
CARD_TITLE_DYNAMIC = (
    "■ 제목 추가 규칙(매우 중요):\n"
    "- [어제 제목]과 첫인상이 비슷하면 실패다. 같은 핵심 이슈가 며칠 이어져도, 어제와 *오늘 진짜 "
    "달라진 사실*(제자리↔되돌림, D-day가 하루 더 임박 등)로 각도를 바꿔라. 진짜 다른 게 없으면 "
    "억지 차별화 말고 차분히(드라마 금지).\n"
    "- 시간 표현은 [임박 ★★★ 일정]의 *실제 D-day*와 일치시켜라. 결정이 내일이면 '당일/오늘'이라 "
    "쓰지 말 것(예: 결정 D-1이면 '코앞'·'하루 앞', D-0이어야 '오늘/당일'). 날짜를 틀리면 기각된다.\n"
    "- heat=high면 사건의 *임박·크기*를 키운 후크 허용(허용: '이번주 최대 변수, 오늘' / "
    "'결과에 환율 갈림길' / '오늘 밤 FOMC 결정' / 질문형). 단 방향 단정·예보·협박·낚시는 금지. "
    "heat=normal이면 담백하게.\n"
    "- 제목엔 환율 숫자(1,513원·1520선 등) 절대 금지(목록·박스와 어긋남)."
)


def validate_briefing(payload, rate, cal_events, prev_title, today):
    """발행 전 사실·정직성 게이트. (blocks=게시 금지, warns=확인 권고) 두 리스트 반환.
    factor_analysis(생성)·carousel(표지)·build_site(웹배포) 셋이 같은 기준을 공유한다."""
    blocks, warns = [], []
    title = (payload.get("card_title") or "").strip()
    tldr = [t for t in (payload.get("tldr") or []) if t]
    text = " ".join([title, " ".join(tldr), payload.get("overall_why") or ""])

    # 1) 제목 환율 숫자 = desync 위험
    if _RATE_NUM_RE.search(title):
        blocks.append(f"제목에 환율 숫자 포함 → 박스와 desync: '{title}' (숫자 빼고 원인·이슈로)")
    # 2) 빈 값·길이
    if not title:
        blocks.append("card_title 비어 있음")
    elif len(title) > 24:
        warns.append(f"제목 {len(title)}자(>24, 잘릴 수 있음): '{title}'")
    if len(tldr) < 3:
        warns.append(f"tldr {len(tldr)}개(<3)")
    if len(payload.get("factors") or []) < 4:
        warns.append(f"factors {len(payload.get('factors') or [])}개(<4)")
    # 3) 환율 변동 주장 ↔ 실제 전일대비 모순
    r, p = rate.get("rate"), rate.get("prev")
    if isinstance(r, (int, float)) and isinstance(p, (int, float)):
        actual = r - p
        for m in re.finditer(r"(\d+)\s*원\s*(?:넘게|이상|가까이|가량)?\s*"
                             r"(하락|내려|내린|떨어|급락|상승|올라|오른|급등)", text):
            if abs(int(m.group(1)) - abs(actual)) > 3:
                blocks.append(f"텍스트 '{m.group(1)}원 {m.group(2)}' ↔ 실제 전일대비 {actual:+.1f}원")
        if abs(actual) < 1.0 and any(w in text for w in
                                     ("하락", "급락", "상승", "급등", "강세", "약세")):
            warns.append(f"실제 전일대비 {actual:+.1f}원(보합)인데 방향/변동 주장")
    # 4) 날짜/D-day 정확성 — 이벤트별 '당일/내일' 표기 ↔ 실제 D-day (수동 점검 자동화)
    dday_by_kw = {}
    for e, d in imminent_events(cal_events, today, max_days=7):
        for kw in EVENT_KW:
            if kw in e.get("name", ""):
                dday_by_kw.setdefault(kw, d)
    for kw, d in dday_by_kw.items():
        if re.search(rf"오늘\s*(?:밤|예정|발표)?\s*{kw}|{kw}\s*(?:당일|오늘)", text) and d != 0:
            blocks.append(f"'{kw} 당일/오늘'로 썼으나 실제 D-{d}(오늘 아님) — 날짜 점검")
        if re.search(rf"내일\s*{kw}|{kw}\s*내일", text) and d != 1:
            blocks.append(f"'{kw} 내일'로 썼으나 실제 D-{d} — 날짜 점검")
    # 4b) 임박 ★★★(D-0~2)이 요약에 아예 없으면 경고
    for e, d in imminent_events(cal_events, today, max_days=2):
        km = re.search(r"(FOMC|CPI|금통위|고용|금리|소비자물가|연준)", e.get("name", ""))
        kw = km.group(1) if km else (e.get("name", "")[:5])
        if kw and kw not in text:
            warns.append(f"임박 ★★★ '{e.get('name','')}'(D-{d})이 요약에 안 보임")
        break
    # 5) 어제 제목과 과유사
    if prev_title and title:
        a = set(re.findall(r"[가-힣A-Za-z]+", title))
        b = set(re.findall(r"[가-힣A-Za-z]+", prev_title))
        if a and b and len(a & b) / len(a | b) >= 0.6:
            warns.append(f"어제 제목과 유사: 오늘 '{title}' ↔ 어제 '{prev_title}' (차별점 점검)")
    # 6) 예보·협박·낚시 금지어
    for w in FORECAST_BAN:
        if w in text:
            blocks.append(f"예보/낚시 금지어 '{w}' 포함(§6·§0)")
    # 7) 요인 근거 기사 0개 = 환각 위험 (BLOCK)
    empties = [f.get("name", "?") for f in (payload.get("factors") or [])
               if not (f.get("sources") or [])]
    if empties:
        blocks.append(f"근거 기사 0개 요인: {', '.join(empties)} (환각 위험 — 출처 확인)")
    # 8) 시제 모순: 이미 지난 ★★★ 이벤트를 '앞두고/예정' 같은 미래형으로 표기 (BLOCK)
    dd_all = {}
    for e in cal_events:
        try:
            d = (date.fromisoformat(e.get("date", "")) - today).days
        except ValueError:
            continue
        if int(e.get("importance", 0)) >= 3:
            for kw in EVENT_KW:
                if kw in e.get("name", ""):
                    dd_all.setdefault(kw, []).append(d)
    for kw, ds in dd_all.items():
        past_only = any(d < 0 for d in ds) and not any(d >= 0 for d in ds)
        if past_only and re.search(rf"{kw}[^.。]{{0,12}}(앞두|예정|열린다|열릴|개최|앞둔)", text):
            blocks.append(f"이미 지난 '{kw}'을 미래형(앞두고/예정)으로 표기 — 시제 점검")
    # 9) check A: 장중(마감 전)인데 '오늘 종가/마감' 단정 = 거짓 (BLOCK)
    msg = check_intraday_close(text, rate.get("asof", ""), today, rate)
    if msg:
        blocks.append(msg)
    # 10) 주말(외환시장 휴장)
    if today.weekday() >= 5:
        warns.append("주말(외환시장 휴장) — 변동 주장 주의")
    return blocks, warns


def print_validation(blocks, warns, where="검증"):
    """검증 결과를 stderr에 일관 포맷으로 출력. blocks 있으면 게시 금지 배너."""
    if blocks:
        print("=" * 56, file=sys.stderr)
        print(f"❌ {where} 실패 — 게시 금지! (사실/정직성 위반)", file=sys.stderr)
        for b in blocks:
            print("  • " + b, file=sys.stderr)
        print("=" * 56, file=sys.stderr)
    for w in warns:
        print("  ⚠ " + w, file=sys.stderr)


def fix_title_number(client, model, title):
    """card_title에 환율 숫자(1,500원·1520선 등)가 있으면 게이트가 배포를 막는다. 숫자를 빼고
    원인·이슈만 살려 다시 쓴다(LLM 한 번, 실패 시 정규식 제거). 숫자 없으면 그대로 둠."""
    title = (title or "").strip()
    if not title or not _RATE_NUM_RE.search(title):
        return title
    try:
        r = client.messages.create(model=model, max_tokens=80, messages=[{"role": "user", "content":
            "다음 '지난 브리핑' 제목에서 환율 숫자(예: 1,500원·1520선·1530)를 빼고, 그날의 원인·이슈만 "
            "살려 한국어 18자 이내 한 줄로 다시 써줘. 숫자·따옴표·이모지 없이 제목 글자만 출력:\n" + title}])
        t = "".join(b.text for b in r.content if getattr(b, "type", None) == "text").strip().strip('"\'')
        if t and not _RATE_NUM_RE.search(t):
            print(f"  제목 숫자 자동제거: '{title}' → '{t[:30]}'", file=sys.stderr)
            return t[:30]
    except Exception as exc:
        print(f"  제목 숫자 재작성 실패({exc}) — 정규식 제거로 폴백", file=sys.stderr)
    # 폴백: 숫자 토큰 + 잔여 조사(원대/선/돌파 등) 제거
    t = _RATE_NUM_RE.sub("", title)
    t = re.sub(r"\s*(원대|대|선|선에서|돌파|까지)\s*", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip(" ,·") or title


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


def _strip_html(s: str) -> str:
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def search_factor(query: str, limit: int):
    """Google News RSS search (free, no key) → list of {title, link, snippet, date}.

    `when:2d` keeps results recent (the Firecrawl version used qdr:d ~ past day).
    """
    url = _RSS.format(q=urllib.parse.quote(f"{query} when:2d"))
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


def latest_rate_context() -> str:
    """화면 상단 박스와 *동일한* 단일 소스(site/rate.json)에서 정확한 환율을 읽어 온다.
    박스: 현재=rate, 어제=prev, 전일대비=Math.round(rate-prev). 요약·종합이 다른 숫자를
    쓰면 박스와 어긋나 거짓이 되므로, 여기 정수값(JS Math.round과 동일)을 그대로 쓰게 한다."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "site", "rate.json")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "(현재 환율 데이터 없음)"
    rnd = lambda x: int(math.floor(x + 0.5)) if isinstance(x, (int, float)) else None  # JS Math.round(half-up)
    cur, prev = rnd(d.get("rate")), rnd(d.get("prev"))
    WD = ["월", "화", "수", "목", "금", "토", "일"]
    lines = []
    if cur is not None:
        lines.append(f"현재(최신 종가): {cur:,}원  ※ 화면 상단 박스의 '현재'와 동일")
    if prev is not None:
        dr = (cur - prev) if cur is not None else None
        if dr == 0:
            chg = "보합(어제와 비슷, 0원)"
        elif dr is not None:
            chg = f"{'▲' if dr > 0 else '▼'}{abs(dr):,}원 (어제보다 {'올라' if dr > 0 else '내려'})"
        else:
            chg = ""
        lines.append(f"어제(전일 종가): {prev:,}원  ※ 박스의 '어제'와 동일" + (f" / 전일대비 {chg}" if chg else ""))
    ser = [s for s in (d.get("series") or []) if s.get("date") and s.get("close") is not None][-5:]
    flow = []
    for s in ser:
        try:
            wd = WD[datetime.strptime(s["date"], "%Y-%m-%d").weekday()]
            flow.append(f"{s['date']}({wd}) {rnd(s['close']):,}원")
        except (ValueError, TypeError):
            pass
    if flow:
        lines.append("최근 종가 흐름: " + ", ".join(flow))
    if d.get("asof"):
        lines.append(f"기준 시각: {d['asof']} (이 요일·직전 거래일 기준으로 '어제'를 판단)")
    return "\n".join(lines) if lines else "(현재 환율 데이터 없음)"


def prev_tldr_context(exclude: str = "") -> str:
    """직전(가장 최근 발행) factors의 30초요약 — 오늘 요약이 '어제 대비 새로 바뀐 점'을 앞세우도록
    비교 기준을 준다. exclude는 지금 기반으로 쓰는 파일(rewrite 시 자기 자신)을 제외하기 위함."""
    ex = os.path.abspath(exclude) if exclude else ""
    files = [f for f in glob.glob(os.path.join("output", "factors-*.json"))
             if os.path.abspath(f) != ex]
    if not files:
        return ""
    try:
        d = json.load(open(max(files, key=os.path.getmtime), encoding="utf-8"))
        prev = [t for t in (d.get("tldr") or []) if t][:3]
        return "\n".join(f"- {t}" for t in prev)
    except (OSError, json.JSONDecodeError):
        return ""


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
    today_kst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    today_date = datetime.now(timezone(timedelta(hours=9))).date()
    prev_block = prev_tldr_context(exclude=path)   # 직전 발행 요약(자기 자신 제외) — '어제 대비 변화' 기준
    cal_events = load_calendar_events()
    rate_obj = load_rate_obj()
    title_ctx, heat_meta = title_engine_context(rate_obj, cal_events, today_date, exclude=path)
    prompt = (
        f"오늘은 {today_kst}(한국시간)입니다. 아래는 오늘 원/달러 환율을 움직인 Top4 요인과 핵심 사실입니다.\n\n"
        f"{digest}\n\n"
        f"[현재 환율 맥락]\n{latest_rate_context()}\n\n"
        f"{market_status_block(rate_obj, today_date)}\n\n"
        f"[직전 발행 30초요약(어제/직전)]\n{prev_block or '(없음 — 비교 대상 없음)'}\n\n"
        f"[제목 차별화·날짜·heat 맥락]\n{title_ctx}\n\n"
        f"{LEVEL_RULE}\n"
        "■ 시제: 이미 발표·종료된 지표(예: 어제 나온 CPI)를 '발표를 앞두고'·'예정' 같은 미래형으로 "
        "쓰지 말 것. 이미 나온 결과를 과거형으로 반영하라.\n"
        "■ 쉬운 말: 어려운 전문용어·한자어 금지(예: '하방압력'→'끌어내리는 힘', '횡보'→'큰 변화 없이 머묾', "
        "'센티먼트'→'투자 심리', '위험선호 심리'→'위험자산을 사려는 분위기', '안전선호·위험회피'→'안전한 달러로 돈이 몰림'). "
        "대학생이 한 번에 이해하게 직관적으로 풀어 쓸 것.\n\n"
        f"■ overall_why 작성 규칙: {WHY_RULES}\n\n"
        f"■ tldr 작성 규칙: {TLDR_RULES}\n\n"
        f"■ card_title 작성 규칙: {CARD_TITLE_RULES}\n{CARD_TITLE_DYNAMIC}\n\n"
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
        data["card_title"] = fix_title_number(client, model, parsed["card_title"])
    if tldr:
        data["tldr"] = tldr
    data["heat"] = heat_meta["heat"]
    data["heat_reason"] = heat_meta["heat_reason"]
    data["title_badge"] = heat_meta["title_badge"]
    sanitize_copy(data, rate_obj, today_date)   # 예보어·델타숫자·장중거짓종가 자가치유(API 0)
    blocks, warns = validate_briefing(data, rate_obj, cal_events, prev_card_title(exclude=path), today_date)
    data["checks"] = {"passed": not blocks, "blocks": blocks, "warnings": warns}
    now = datetime.now(timezone.utc)
    out = os.path.join("output", f"factors-{now.strftime('%Y-%m-%d_%H%M')}.json")
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Rewrote overall_why + tldr (reused {os.path.basename(path)}) -> {out}  (heat={heat_meta['heat']})\n")
    for t in tldr:
        print(f"  · {t}")
    print(f"\n{why}")
    print_validation(blocks, warns, where="제목/사실 검증")


def verify_factors(client, model, result, digest):
    """근거 자가검증: 생성된 요인 headline·bullets가 기사 원문에 실제로 있는지 한 번 더 확인하고,
    기사에 없는 사실·틀린 수치는 기사에 맞게 정정/제거한다(환각·수치오류 방지). 문체·길이는 유지."""
    factors = result.get("factors", [])
    if not factors:
        return result
    payload = json.dumps(
        [{"factor_id": f.get("factor_id"), "headline": f.get("headline", ""),
          "bullets": f.get("bullets", []), "source_ids": f.get("source_ids", [])} for f in factors],
        ensure_ascii=False,
    )
    prompt = (
        "아래는 번호가 매겨진 [기사 원문], 그날의 [현재 환율 맥락](종가·전일대비, 화면 배지와 동일 소스), "
        "그리고 이 기사들을 보고 작성한 [요인 분석]이다. 각 요인의 headline·bullets를 엄격히 검증·정정하라.\n"
        "■ 규칙:\n"
        "- 기사에 없는 사실·인과·추측은 삭제하고, 기사에 분명히 있는 핵심 사실로 교체.\n"
        "- 숫자(환율·%·날짜·기관명)는 기사와 정확히 일치해야 함. 틀리면 기사 값으로 정정.\n"
        "- 그날 환율 '레벨/마감/전일대비'를 말하는 문장은 [현재 환율 맥락]의 종가·전일대비와 일치해야 한다. "
        "기사 속 장중 고점·저점(예: '장중 1,504원')은 *유지하되 '장중'으로 명시*하고, 그날 종가/움직임으로 "
        "둔갑한 표현(예: 실제 전일대비는 −4원인데 '10원 급락'으로 단정, 또는 어제 장중 고점을 오늘 레벨로 사용)은 "
        "맥락의 종가·전일대비에 맞게 정정한다. 전일대비가 작은데 '급락/급등'으로 단정했으면 '소폭/보합'으로 낮춘다.\n"
        "- 근거가 약하면 단정을 낮출 것(평서문 '~다'는 유지하되 과장 제거).\n"
        "- 문체(담백한 '~다' 평서문)·각 불렛 길이·불렛 2개·source_ids는 그대로 유지. 검증·정정만 하라.\n\n"
        f"[현재 환율 맥락]\n{latest_rate_context()}\n\n"
        f"[기사 원문]\n{digest}\n\n[요인 분석]\n{payload}\n\n"
        '정정된 factors만 JSON으로(코드펜스 없이): '
        '{"factors":[{"factor_id":"F1","headline":"...","bullets":["..",".."],"source_ids":[0,3]}]}'
    )
    try:
        resp = client.messages.create(model=model, max_tokens=2500,
                                      messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        s, e = text.find("{"), text.rfind("}")
        fixed = json.loads(text[s:e + 1]) if s != -1 and e != -1 else {}
        by_id = {c.get("factor_id"): c for c in fixed.get("factors", []) if isinstance(c, dict)}
        changed = 0
        for f in factors:
            c = by_id.get(f.get("factor_id"))
            if not c:
                continue
            if c.get("headline") and c["headline"] != f.get("headline"):
                f["headline"] = c["headline"]; changed += 1
            bl = [b for b in (c.get("bullets") or []) if isinstance(b, str) and b.strip()]
            if bl:
                if bl != f.get("bullets"):
                    changed += 1
                f["bullets"] = bl
            if c.get("source_ids"):
                f["source_ids"] = c["source_ids"]
        print(f"  자가검증 완료(정정 {changed}건)")
    except Exception as exc:   # 무슨 일이 있어도 빌드는 안 죽고 원본 유지
        print(f"  자가검증 건너뜀({exc}) — 원본 유지", file=sys.stderr)
    return result


def main() -> None:
    args = parse_args()
    load_dotenv()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    an_key = os.environ.get("ANTHROPIC_API_KEY")
    if not an_key:
        sys.exit("error: ANTHROPIC_API_KEY is not set (.env).")

    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("error: anthropic not installed. pip install -r requirements.txt")

    if args.rewrite_why:  # cheap path: reuse latest factors, reword only the summary
        rewrite_why(Anthropic(api_key=an_key), args.model)
        return

    # 1) Search all 10 factors (free Google News RSS); build a global article list.
    all_articles = []          # global idx -> article (+factor id)
    by_factor = {}             # factor id -> [global idx,...]
    print("Searching 10 factors (Google News, free)…", flush=True)
    for f in FACTORS:
        arts = search_factor(f["query"], args.per_factor)
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

    today_kst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    today_date = datetime.now(timezone(timedelta(hours=9))).date()
    prev_block = prev_tldr_context()   # 직전 발행 요약 — '어제 대비 변화'를 앞세우는 비교 기준
    cal_events = load_calendar_events()
    rate_obj = load_rate_obj()
    title_ctx, heat_meta = title_engine_context(rate_obj, cal_events, today_date)
    prompt = (
        f"당신은 원/달러(USD/KRW) 환율 애널리스트입니다. 오늘은 {today_kst}(한국시간)입니다. "
        "아래 '환율 영향 요인'별로 최근 뉴스를 모았습니다. "
        "오늘 원/달러에 실제로 가장 크게 영향을 준 요인 4개를 impact 순으로 고르세요.\n"
        "■ 4번째 칸(중요): 그날 강한 동인이 4개가 안 되면 약한 요인을 억지로 끼워 앞 카드와 같은 "
        "사실을 반복하지 말 것. 대신 'F11 그 외 환율 이슈'를 골라 앞 카드들과 겹치지 않는 그날의 "
        "구체적 FX 이슈(특정 수급·발언·해외 시장 이벤트·거래 동향 등)를 새로 담을 것. F11·F12는 "
        "평소 impact가 낮아 잘 안 뽑히는 보조 버킷이다.\n\n"
        f"[현재 환율 맥락]\n{latest_rate_context()}\n\n"
        f"{market_status_block(rate_obj, today_date)}\n\n"
        f"[직전 발행 30초요약(어제/직전)]\n{prev_block or '(없음 — 비교 대상 없음)'}\n\n"
        f"[제목 차별화·날짜·heat 맥락]\n{title_ctx}\n\n"
        f"[요인별 뉴스]\n{digest}\n\n"
        "■ 작성 원칙 (가장 중요):\n"
        "- 논리·정확도·구체성이 최우선. 두루뭉술한 채움말 절대 금지"
        "('여러 요인이 겹쳐', '복합적 요인', '대내외 불확실성', '크게 영향' 같은 알맹이 없는 말).\n"
        "- 어려운 전문용어·한자어 금지. 비전문가(대학생)가 한 번에 이해하도록 쉬운 말로 풀어 쓸 것. "
        "예: '하방압력'→'환율을 끌어내리는 힘', '상방압력'→'밀어 올리는 힘', '되돌림'→'다시 되돌아옴', "
        "'횡보'→'큰 변화 없이 머묾', '센티먼트'→'투자 심리', '양해각서'→'합의 문서', "
        "'리스크오프'→'위험을 피해 안전자산으로 돈이 몰림', "
        "'위험선호(심리)'→'위험자산을 사려는 분위기', '안전선호·위험회피'→'안전한 달러로 돈이 몰림'. "
        "최대한 직관적으로.\n"
        "- 각 불렛은 뉴스에 실제로 나온 구체적 사실(수치·기관명·국가·지표·날짜)을 담아 "
        "'무엇이 → 어떤 경로로 → 환율에 어떻게'를 인과로 설명.\n"
        "- 모든 문장(headline·bullets)은 아래 [요인별 뉴스] 기사에 실제로 나온 사실만 담을 것. "
        "기사에 없는 수치·인과·일반론·추측은 절대 금지. 각 요인의 근거 기사 번호(source_ids)를 반드시 표기.\n"
        f"{LEVEL_RULE}\n"
        "- 시제 정확: 오늘 날짜 기준으로 이미 발표·종료된 지표(예: 어제 나온 CPI)를 '발표를 앞두고'·"
        "'발표 예정' 같은 미래형으로 쓰지 말 것. 발표 전에 작성된 옛 기사의 표현을 그대로 옮기지 말고, "
        "이미 나온 결과를 반영해 과거형으로 쓸 것.\n"
        "- 모든 문장(headline·bullets·impact_reason·tldr·overall_why)은 '~다/~했다'로 "
        "끝나는 담백한 문어체 평서문으로 일관되게. 해요체(~요/~예요)·반말·과한 격식"
        "(~습니다)을 섞지 말 것.\n\n"
        "■ 섹션 역할 분담(반복 금지 — 매우 중요): tldr·overall_why·요인 카드는 한 화면에 함께 "
        "보이므로 같은 사실을 같은 말로 되풀이하면 안 된다. 각자 다른 층위를 맡는다 — tldr=결과·"
        "핵심 변화·지켜볼 것(가장 압축, 숫자 중심), overall_why=요인들을 잇는 인과 한 줄기와 더 깊은 "
        "'왜'(개별 불릿을 그대로 나열·복사 금지), 요인 카드=그 요인에서만 나오는 고유한 구체 근거"
        "(다른 카드와 같은 사실 반복 금지 — 한 사실은 가장 알맞은 한 곳에서만). 같은 수치·표현"
        "(예: '800억 달러', '17년 만 최고')은 페이지 전체에서 1~2회까지만, 요약에서 썼으면 카드에선 "
        "다른 각도의 사실로.\n"
        "- 메커니즘 보존(쉬움 ≠ 알맹이 제거): 한 요인이 그 자체로 '결과'(예: 달러 강세·원화 약세)면 "
        "*왜 그렇게 됐는지*(동인: 연준 금리 신호·지표 결과·정책 변화 등)를 한 단계 더 들어가 설명할 "
        "것. '달러가 전 세계적으로 올랐다'처럼 결과만 되풀이하지 말 것. 쉬운 말로 풀되 인과의 핵심 "
        "고리는 절대 생략하지 말 것.\n"
        "- 정치 중립(매우 중요): 정치 관련 사실은 *환율·시장에 준 영향만* 서술한다. 정당·정치인·정부의 "
        "옳고 그름이나 정책의 잘잘못을 평가하지 말고, 진영을 가르는 표현·특정 인물 칭찬·비난 금지. "
        "'누가 잘못했다'가 아니라 '정치 불확실성이 커지며 → 외국인 자금이 빠져 → 환율이 올랐다'처럼 "
        "사실→시장 경로만, 반드시 출처 기사에 근거해서. 선거·정국 전망으로 편들지 말고 중립어"
        "(정치 불확실성·정정 불안·정책 리스크)만 쓸 것.\n\n"
        "선정한 요인 4개 각각에 대해: impact(1~5 정수, 오늘 환율을 움직인 영향력. "
        "5=가장 결정적인 주범. 4개는 반드시 서로 차등을 둘 것), "
        "impact_reason(그 영향도 점수를 매긴 근거 한 줄. 예: '복수 매체가 오늘 환율 "
        "상승의 1순위 원인으로 지목', '방향엔 영향을 줬으나 보조적 요인'), "
        "headline(오늘 환율 영향 한 줄, 구체적), "
        "bullets(가장 중요한 핵심 사실 2개, 각 1문장·구체적·짧게), source_ids(근거 기사 번호 3~5개).\n"
        f"tldr: {TLDR_RULES}\n"
        f"그리고 overall_why: {WHY_RULES}\n"
        f"그리고 card_title: {CARD_TITLE_RULES}\n{CARD_TITLE_DYNAMIC}\n\n"
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

    # 2.5) 근거 자가검증: 불렛이 정말 기사에 있는지 한 번 더 확인·정정(환각·수치오류 방지).
    print("Self-checking factors against sources…", flush=True)
    result = verify_factors(client, args.model, result, digest)

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
        if not sources:   # 근거 기사 0개 = 검토 필요(환각 위험)
            print(f"  ⚠ 근거 기사 0개: {m['name']} — 검토 필요", file=sys.stderr)
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
        "card_title": fix_title_number(client, args.model, result.get("card_title") or ""),
        "tldr": tldr,
        "overall_why": result.get("overall_why", ""),
        "factors": factors_out,
        "heat": heat_meta["heat"],
        "heat_reason": heat_meta["heat_reason"],
        "title_badge": heat_meta["title_badge"],
    }
    # 발행 전 검증(웹·캐러셀과 동일 기준) → checks를 JSON에 박아 하류 게이트가 상속.
    sanitize_copy(payload, rate_obj, today_date)   # 예보어·델타숫자·장중거짓종가 자가치유(API 0)
    blocks, warns = validate_briefing(payload, rate_obj, cal_events, prev_card_title(), today_date)
    payload["checks"] = {"passed": not blocks, "blocks": blocks, "warnings": warns}

    out_path = args.out or os.path.join("output", f"factors-{now.strftime('%Y-%m-%d_%H%M')}.json")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(factors_out)} top factors to {out_path}  (heat={heat_meta['heat']})\n")
    print_validation(blocks, warns, where="제목/사실 검증")
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

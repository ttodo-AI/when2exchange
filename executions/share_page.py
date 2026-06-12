#!/usr/bin/env python3
"""share_page — build a friendly, shareable HTML page for people who exchange
KRW->USD every month.

Unlike the internal dashboard (scores/briefs/drafts), this is consumer-facing:
a big "is now a good time to exchange?" verdict, the current rate, and the latest
news in Korean. Single self-contained file, mobile-first — share it via link or
KakaoTalk and friends open it in a browser.

Standalone script. Run directly:
    python executions/share_page.py
    python executions/share_page.py --in output/krw-exchange-rate-2026-06-04_2243.json
"""
import argparse
import base64
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

VIEWS_NS = "krw-hwanjeon-share"  # namespace for the Abacus view counter
# Google Form for reader feedback — replace with the real form link once created.
FEEDBACK_URL = "https://forms.gle/REPLACE_WITH_YOUR_FORM"
# 헤더의 강아지를 누르면 뜨는 자기소개. 자유롭게 교체하세요.
ABOUT_ME = (
    "🐶 멍멍! 제 주인은 매달 환전하느라 머리가 터지는 유학생이랍니다.\n\n"
    "매월 $2~3,000씩 환전해야 하는데, 환율이 10원만 올라도 손이 벌벌 떨리더라고요. "
    "'어제 환전 더 해둘걸…' 후회하는 매일을 보내고 있어요. 😢\n\n"
    "요즘 매일 아침 눈뜨자마자 환율 앱부터 확인하는 제 모습이 너무 지치고 서글퍼서… "
    "시간과 돈을 아끼려고 이 환전 타이밍 분석 페이지를 직접 만들었어요!\n\n"
    "매크로 경제 뉴스니 연준 금리니 하는 복잡한 얘기 말고, "
    "\"그래서 내 월세, 내 커피값이 어제보다 얼마 더 드는데?\"를 한눈에 보고 싶었거든요.\n\n"
    "환율의 압박 속에서 살아가는 우리 동지분들, 이 페이지가 조금이나마 짐을 "
    "덜어드렸으면 좋겠습니다! 함께 현명하게 버텨봐요 🐾"
)
# 강아지 머리 위 말풍선 (클릭 유도).
DOG_BUBBLE = "왜 만들었개? 🐶"
# Short status badge text per verdict class (color comes from CSS).
BADGE_KR = {"good": "환전 추천", "mid": "지금은 보통", "bad": "환전 비추천"}

from dotenv import load_dotenv

LABEL_KR = {
    "GOOD": ("지금 환전하기 좋아요", "달러가 평소보다 싼 편이에요", "good", "🟢"),
    "NEUTRAL": ("지금은 보통이에요", "평소와 비슷한 수준이에요", "mid", "🟡"),
    "BAD": ("지금은 환전하기 아까워요", "달러가 평소보다 비싼 편이에요", "bad", "🔴"),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a shareable KRW->USD timing page.")
    p.add_argument("--in", dest="infile", default=None,
                   help="Scout JSON (default: latest output/krw-exchange-rate-*.json).")
    p.add_argument("--out", default=None,
                   help="Output HTML (default output/share-<ts>.html).")
    p.add_argument("--no-ai", action="store_true",
                   help="Skip the Claude Korean rewrite; use the raw verdict text.")
    p.add_argument("--archive", default=None,
                   help="archive.json with past entries to list as '지난 브리핑'.")
    return p.parse_args()


def find_latest(pattern: str):
    matches = glob.glob(os.path.join("output", pattern))
    return max(matches, key=os.path.getmtime) if matches else None


def parse_verdict(text: str) -> dict:
    """Pull RATE / WHY / TIP out of the Scout's verdict block."""
    out = {}
    for key, field in (("RATE", "rate"), ("WHY", "why"), ("TIP", "tip")):
        m = re.search(rf"{key}:\s*(.+?)(?:\n[A-Z]+:|\Z)", text, re.DOTALL)
        if m:
            out[field] = m.group(1).strip()
    return out


def korean_copy(label: str, verdict_text: str, articles, monthly_usd, model: str):
    """Rewrite the verdict as friendly Korean consumer copy via Claude, or None."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    news_lines = "\n".join(
        f"[{i}] {a.get('title','')} :: {(a.get('summary') or '')[:280]}"
        for i, a in enumerate(articles[:10])
    )
    prompt = (
        "다음 환율 정보를 바탕으로, 매달 원화를 달러로 환전하는 일반인 친구에게 "
        f"보여줄 한국어 브리핑을 써주세요 (매달 약 ${monthly_usd:,.0f} 환전). "
        "모든 문장은 '~다/~했다'로 끝나는 담백한 문어체 평서문으로 일관되게 — "
        "해요체(~요/~예요)·반말·과한 격식(~습니다)을 섞지 말 것. 전문용어는 쉽게 풀되.\n\n"
        f"판정(영문): {label}\n원본 분석:\n{verdict_text}\n\n"
        f"뉴스 목록 (각 줄: [번호] 제목 :: 요약):\n{news_lines}\n\n"
        "■ 작성 원칙 (가장 중요):\n"
        "- 논리·정확도·구체성이 최우선. 두루뭉술한 채움말 절대 금지.\n"
        "- 금지 예시: '여러 이유가 겹쳐서', '복합적 요인으로', '대내외 불확실성', "
        "'크게 떨어진 상태입니다' 같은 알맹이 없는 말.\n"
        "- 'why'는 반드시 구체적 동인 1~2개를 콕 집어 '무엇이 → 어떤 경로로 → 환율을 "
        "어떻게' 인과로 설명. 가능하면 뉴스에 나온 수치·고유명사(기관·국가·지표명)를 인용.\n"
        "- 뉴스에 근거 없는 추측·일반론은 쓰지 말고, 모르면 뉴스가 말하는 사실만.\n\n"
        "각 뉴스 insight도 같은 원칙: 제목·요약에서 핵심 사실(수치·주체·인과)을 1~2문장으로. "
        "잘린 문장이나 메뉴/광고 잡음은 제거.\n"
        "tip에는 '$2,000' 같은 구체적 환전 금액을 넣지 말 것(분할 환전 같은 행동 조언만).\n\n"
        '아래 정확한 JSON만 출력 (코드펜스 없이): {"headline": "한 줄 요약 헤드라인", '
        '"rate": "현재 원/달러 환율 숫자(예: 1,530원)", "why": "구체적 동인+인과 1-2문장", '
        '"tip": "매달 환전하는 사람을 위한 실용 팁 1문장", '
        '"news": [{"index": <번호>, "insight": "그 뉴스의 핵심 사실 1~2문장"}]}'
    )
    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model, max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        s, e = text.find("{"), text.rfind("}")
        return json.loads(text[s:e + 1]) if s != -1 and e != -1 else None
    except Exception:
        return None


def build_logos() -> str:
    # assets/logos/<TICKER>.png -> JS object {TICKER:"data:image/png;base64,..."} (자체 완결).
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(root, "assets", "logos")
    items = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith(".png"):
                tk = os.path.splitext(fn)[0]
                with open(os.path.join(d, fn), "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode()
                items.append(f'"{tk}":"data:image/png;base64,{b64}"')
    return "{" + ",".join(items) + "}"


def main() -> None:
    args = parse_args()
    load_dotenv()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    infile = args.infile or find_latest("krw-exchange-rate-*.json")
    if not infile:
        sys.exit("error: no Scout JSON in output/. Run the watcher (Scout) first.")
    try:
        with open(infile, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"error: could not read Scout file {infile}: {exc}")

    articles = data.get("articles", [])
    verdict = data.get("timing_verdict") or {}
    label = (verdict.get("label") or "NEUTRAL").upper()
    monthly_usd = data.get("monthly_usd", 2000)
    parsed = parse_verdict(verdict.get("text", "") or "")

    kr = None if args.no_ai else korean_copy(
        label, verdict.get("text", "") or "", articles, monthly_usd, "claude-haiku-4-5-20251001"
    )

    headline, subtitle, cls, emoji = LABEL_KR.get(label, LABEL_KR["NEUTRAL"])
    badge = BADGE_KR.get(cls, "보통")
    rate = (kr or {}).get("rate") or parsed.get("rate") or "—"
    why = (kr or {}).get("why") or parsed.get("why") or ""
    tip = (kr or {}).get("tip") or parsed.get("tip") or ""

    # Build news cards.
    def fmt_date(raw):
        if not raw:
            return ""
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).strftime("%m월 %d일")
        except ValueError:
            return str(raw)

    insights = {}
    for n in (kr or {}).get("news", []) or []:
        if isinstance(n, dict) and isinstance(n.get("index"), int):
            insights[n["index"]] = (n.get("insight") or "").strip()

    cards = []
    for i, a in enumerate(articles):
        insight = insights.get(i, "")
        if not insight:  # fallback: trimmed raw summary
            insight = (a.get("summary") or "").replace("\n", " ").strip()
            if len(insight) > 160:
                insight = insight[:160] + "…"
        summary = insight
        link = a.get("link") or ""
        title = a.get("title") or "(제목 없음)"
        cards.append(
            f'<a class="news" href="{esc(link)}" target="_blank" rel="noopener">'
            f'<div class="news-date">{esc(fmt_date(a.get("date")))}</div>'
            f'<div class="news-title">{esc(title)}</div>'
            f'<div class="news-sum">{esc(summary)}</div>'
            f'<span class="news-link">원문 보기 ↗</span></a>'
        )
    news_html = "\n".join(cards) if cards else '<p class="muted">표시할 뉴스가 없어요.</p>'
    news_title = "📰 오늘의 환율 뉴스"

    # Glossary annotator (page-wide dedupe). Process WHY first so the top-of-page
    # occurrence of each term gets the 🔍 marker.
    annotate = make_term_annotator()
    why_html = None
    tldr_html = ""

    # Prefer the factor Top4 analysis if present (richer, sourced, specific).
    factor_file = find_latest("factors-*.json")
    if factor_file:
        try:
            fj = json.load(open(factor_file, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fj = None
        if fj and fj.get("factors"):
            if fj.get("overall_why"):
                why = fj["overall_why"]
            why_html = annotate(emphasize(why))  # WHY annotated before bullets
            tldr = [t for t in (fj.get("tldr") or []) if t][:3]
            if tldr:
                items = "".join(f"<li>{emphasize(t)}</li>" for t in tldr)
                tldr_html = (
                    '<section><div class="tldr">'
                    '<div class="tldr-cap">30초 요약</div>'
                    f'<ul class="tldr-list">{items}</ul></div></section>'
                )
            blocks = []
            for i, f in enumerate(fj["factors"], 1):
                bullets = "".join(f"<li>{annotate(esc(strip_cite(b)))}</li>" for b in f.get("bullets", [])[:2])
                links = [s for s in f.get("sources", []) if s.get("link")]
                srcs = "".join(
                    f'<a class="src-chip" href="{esc(s["link"])}" target="_blank" rel="noopener">{n}</a>'
                    for n, s in enumerate(links, 1)
                )
                src_html = f'<div class="factor-src">관련기사 {srcs}</div>' if srcs else ""
                impact = int(f.get("impact") or 0)
                fires = "🔥" * impact
                reason = esc(f.get("impact_reason", ""))
                nm = esc(f.get("name", ""))
                info_html = (
                    f'<button class="impact-info" data-term="{nm} · 영향도 {impact}/5" '
                    f'data-def="{reason}">i</button>' if reason else ""
                )
                blocks.append(
                    '<div class="factor">'
                    f'<div class="factor-head"><span class="rank">{i}</span>'
                    f'<span class="factor-name">{nm}</span>'
                    f'<span class="impact" title="오늘 영향도">{fires}</span>{info_html}</div>'
                    f'<div class="factor-line">{esc(f.get("headline",""))}</div>'
                    f'<ul class="factor-bullets">{bullets}</ul>'
                    f'{src_html}'
                    '</div>'
                )
            note = '<div class="sec-note">🔥 영향도는 그날 뉴스 분석을 토대로 한 AI 추정이에요.</div>'
            news_html = note + "\n".join(blocks)
            news_title = "오늘 환율을 움직인 요인 Top 4"

    if why_html is None:  # no factor file -> annotate the fallback why
        why_html = annotate(emphasize(why))

    # This week's economic calendar (D-Day computed client-side).
    cal_section = ""
    cal_file = find_latest("calendar-*.json")
    if cal_file:
        try:
            cj = json.load(open(cal_file, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cj = None
        if cj and cj.get("events"):
            wd = ["월", "화", "수", "목", "금", "토", "일"]
            guide = esc(cj.get("guide", ""))
            guide_html = f'<div class="cal-guide">💡 {guide}</div>' if guide else ""
            rows = []
            for ev in cj["events"]:
                try:
                    dt = datetime.fromisoformat(ev["date"])
                    disp = f"{dt.month}/{dt.day}({wd[dt.weekday()]})"
                except (ValueError, KeyError):
                    disp = ev.get("date", "")
                t = (ev.get("time") or "").strip()
                when = disp + (f" {t}" if t else "")
                stars = "★" * int(ev.get("importance", 1))
                scn = "".join(
                    f'<div class="cal-scn"><b>{esc(s.get("cond",""))}</b> {esc(s.get("effect",""))}</div>'
                    for s in ev.get("scenarios", []) if s.get("effect")
                )
                why = esc(ev.get("why", ""))
                why_html = f'<div class="cal-why">{why}</div>' if why else ""
                detail = why_html + scn
                rows.append(
                    '<details class="cal-item"><summary class="cal-row" '
                    f'data-date="{esc(ev.get("date",""))}">'
                    f'<span class="cal-dday">·</span>'
                    f'<div class="cal-main"><div class="cal-name">{esc(ev.get("name",""))} '
                    f'<span class="cal-star">{stars}</span></div>'
                    f'<div class="cal-impact">{esc(ev.get("summary",""))}</div></div>'
                    f'<span class="cal-date">{esc(when)}</span>'
                    f'<span class="cal-more">자세히<span class="cal-caret">▾</span></span>'
                    f'</summary><div class="cal-detail">{detail}</div></details>'
                )
            cal_section = (
                '<section><h3 class="sec">이번주 환율 영향 일정</h3>'
                + guide_html + "\n".join(rows)
                + '<div class="sec-note">항목을 누르면 상세가 열려요 · 시각은 한국시간(KST) 기준 · 날짜는 뉴스 기반이라 변동될 수 있어요.</div></section>'
            )

    # Archive list ("지난 브리핑") from a manifest of past daily pages.
    arc_section = ""
    if args.archive and os.path.exists(args.archive):
        try:
            arc = json.load(open(args.archive, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            arc = []
        today_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        entries = [e for e in arc if isinstance(e, dict) and e.get("file") and e.get("date") != today_str]
        entries.sort(key=lambda e: e.get("date", ""), reverse=True)
        rows = []
        for e in entries[:14]:
            try:
                dt = datetime.fromisoformat(e["date"])
                disp = f"{dt.month}/{dt.day}"
            except ValueError:
                disp = e.get("date", "")
            rate_html = ""
            if e.get("rate") is not None:
                try:
                    rate_html = f'{round(float(e["rate"])):,}원'
                except (TypeError, ValueError):
                    rate_html = ""
                chg = e.get("chg")
                if rate_html and chg not in (None, ""):
                    try:
                        c = float(chg)
                        if round(c) != 0:
                            ar, cc = ("▲", "up") if c > 0 else ("▼", "down")
                            rate_html += f' <i class="arc-chg {cc}">{ar}{abs(round(c))}</i>'
                    except (TypeError, ValueError):
                        pass
            rows.append(
                f'<a class="arc-row" href="{esc(e["file"])}">'
                f'<span class="arc-date">{esc(disp)}</span>'
                f'<span class="arc-rate">{rate_html}</span>'
                f'<span class="arc-head">{esc(e.get("headline",""))}</span></a>'
            )
        if rows:
            arc_section = '<section><h3 class="sec">지난 브리핑</h3>' + "\n".join(rows) + "</section>"

    now = datetime.now(timezone.utc)
    gen = data.get("generated_at", now.isoformat())
    try:
        gen_disp = datetime.fromisoformat(str(gen).replace("Z", "+00:00")).strftime("%Y년 %m월 %d일")
    except ValueError:
        gen_disp = str(gen)

    # Numeric fallback rate (used by JS if the live fetch fails).
    m = re.search(r"([\d,]+(?:\.\d+)?)", str(rate))
    rate_num = float(m.group(1).replace(",", "")) if m else 0

    # Publish timestamp in KST, down to the second.
    kst = timezone(timedelta(hours=9))
    published = now.astimezone(kst).strftime("%Y-%m-%d %H:%M:%S KST")

    html = (
        SHARE_TEMPLATE
        .replace("__CLS__", cls)
        .replace("__BADGE__", esc(badge))
        .replace("__FEEDBACK_URL__", FEEDBACK_URL)
        .replace("__ABOUT__", esc(ABOUT_ME))
        .replace("__BUBBLE__", esc(DOG_BUBBLE))
        .replace("__HEADLINE__", esc(headline))
        .replace("__SUBTITLE__", esc(subtitle))
        .replace("__RATE__", esc(str(rate)))
        .replace("__RATE_NUM__", str(rate_num))
        .replace("__WHY__", why_html)
        .replace("__MONTHLY__", f"{monthly_usd:,.0f}")
        .replace("__MONTHLY_NUM__", str(int(monthly_usd)))
        .replace("__TLDR__", tldr_html)
        .replace("__NEWS__", news_html)
        .replace("__NEWS_TITLE__", esc(news_title))
        .replace("__CALENDAR_SECTION__", cal_section)
        .replace("__ARCHIVE_SECTION__", arc_section)
        .replace("__DATE__", esc(gen_disp))
        .replace("__PUBLISHED__", esc(published))
        .replace("__VIEWS_NS__", VIEWS_NS)
        .replace("__LOGOS__", build_logos())
    )
    out_path = args.out or os.path.join("output", f"share-{now.strftime('%Y-%m-%d_%H%M')}.html")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    print(f"Built share page from {infile}")
    print(f"Saved to {out_path}")
    print(f"Open it: file:///{os.path.abspath(out_path).replace(os.sep, '/')}")


def esc(s) -> str:
    return (s if s is not None else "").__str__().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def strip_cite(s) -> str:
    """Remove source markers like [1], [3][30], or [30,38] from a bullet."""
    return re.sub(r"\s*\[[\d,\s]+\]", "", s or "").strip()


def emphasize(s) -> str:
    """Escape, then turn **key term** markers into bold+underlined highlights."""
    html = esc(s)
    html = re.sub(r"\*\*(.+?)\*\*", r'<b class="hl">\1</b>', html)
    return html.replace("**", "")  # drop any stray, unmatched markers


# Beginner-friendly glossary. Terms found in the copy get a 🔍 + tap-to-define.
GLOSSARY = {
    "연준": "미국의 중앙은행(Fed). 미국 기준금리를 정해 시중 돈의 양과 값을 조절해요.",
    "FOMC": "미국 연준이 기준금리를 결정하는 회의예요.",
    "기준금리": "중앙은행이 정하는 '돈의 기본 이자율'. 오르면 돈 빌리기가 비싸지고 그 나라 통화가 강해지는 편이에요.",
    "금통위": "한국은행이 기준금리를 정하는 회의(금융통화위원회)예요.",
    "비농업고용": "미국에서 농업을 뺀 일자리가 한 달에 얼마나 늘었는지 보는 핵심 고용 지표. 많이 늘면 경기가 튼튼하다는 신호예요.",
    "비농업 고용": "미국에서 농업을 뺀 일자리가 한 달에 얼마나 늘었는지 보는 핵심 고용 지표. 많이 늘면 경기가 튼튼하다는 신호예요.",
    "달러인덱스": "달러가 주요 6개 통화 대비 얼마나 센지 나타내는 지수. 오르면 '강달러'예요.",
    "DXY": "달러인덱스. 달러가 주요 통화 대비 얼마나 강한지 보여주는 지수예요.",
    "국채금리": "정부가 돈을 빌릴 때 내는 이자율. 미국 국채금리가 오르면 더 높은 이자를 좇아 돈이 달러로 몰려요.",
    "경상수지": "한 나라가 무역 등으로 외국과 돈을 주고받은 결과. 흑자면 달러가 들어오는 편이에요.",
    "CPI": "소비자물가지수. 물가가 얼마나 올랐는지 보는 지표로, 높으면 금리를 올릴 가능성이 커져요.",
    "소비자물가": "물가가 얼마나 올랐는지 보는 지표(CPI). 높으면 금리 인상 압력이 커져요.",
    "ISM": "미국 기업들이 느끼는 경기 체감을 보여주는 지수(제조업·서비스업). 좋게 나오면 경기 호조 신호예요.",
    "외국인 순매도": "외국인 투자자가 산 것보다 판 주식이 더 많은 상태. 판 돈을 달러로 바꿔 나가면 환율이 올라요.",
    "수급": "사려는 힘(수요)과 팔려는 힘(공급)의 균형이에요. '외국인 수급'은 외국인의 매수·매도 흐름을 뜻해요.",
    "안전자산": "위기 때 사람들이 몰리는 비교적 안전한 자산(달러·금 등)이에요.",
    "지정학 리스크": "전쟁·분쟁 같은 국제 정세 불안. 커지면 안전한 달러로 돈이 몰려요.",
    "강달러": "달러가 다른 나라 통화에 비해 강한 상태예요.",
    "원화 약세": "원화 가치가 떨어져, 같은 1달러를 사는 데 원화가 더 드는 상태(환율 상승)예요.",
}


def make_term_annotator():
    """Returns annotate(html): wraps the FIRST page-wide occurrence of each
    glossary term with a clickable 🔍 marker. Longest terms first; deduped."""
    used = set()
    terms = sorted(GLOSSARY, key=len, reverse=True)

    def annotate(html: str) -> str:
        spans = []
        for term in terms:
            if term in used:
                continue
            idx = html.find(term)
            if idx == -1:
                continue
            used.add(term)
            token = f"\x00{len(spans)}\x00"
            spans.append(
                f'<span class="term" data-term="{esc(term)}" data-def="{esc(GLOSSARY[term])}">'
                f'{esc(term)}<span class="mag">🔍</span></span>'
            )
            html = html[:idx] + token + html[idx + len(term):]
        for i, span in enumerate(spans):
            html = html.replace(f"\x00{i}\x00", span)
        return html

    return annotate


SHARE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>이번 달 환전, 지금 괜찮을까?</title>
<meta property="og:title" content="이번 달 환전, 지금 괜찮을까?">
<meta property="og:description" content="원/달러 환율 타이밍을 매일 체크해 드려요.">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@latest/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<style>
  :root{ --bg:#f4f5f7; --card:#fff; --ink:#14171f; --muted:#6b7280; --line:#ebedf0;
         --up:#e0383e; --up-bg:#fdecec; --down:#1f9d57; --down-bg:#e7f6ec;
         --brand:#3b5bdb; --brand-bg:#eef1fd;
         --good-fg:#1f7a47; --good-bg:#e7f6ec; --mid-fg:#9a6b00; --mid-bg:#fbf2dd;
         --bad-fg:#c5333a; --bad-bg:#fdeaea; }
  *{ box-sizing:border-box; }
  body{ margin:0; background:var(--bg); color:var(--ink);
        font-family:"Pretendard Variable",Pretendard,-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;
        line-height:1.6; -webkit-font-smoothing:antialiased; font-variant-numeric:tabular-nums; }
  .wrap{ max-width:480px; margin:0 auto; padding:18px 16px 48px; }
  .top{ padding:6px 2px 0; display:flex; align-items:flex-end; gap:6px; }
  .top-text{ flex:1 1 auto; min-width:0; }
  .ey{ font-size:13px; color:var(--muted); font-weight:600; }
  .head{ font-size:20px; font-weight:800; letter-spacing:-.02em; line-height:1.32; margin:3px 0 0; }
  .dog-track{ position:relative; flex:0 0 104px; height:34px; }
  .about-dog{ position:absolute; left:0; bottom:0; background:none; border:none; padding:2px;
              cursor:pointer; animation:roam 24s ease-in-out infinite; }
  .dog-bubble{ position:absolute; left:50%; bottom:calc(100% - 2px); transform:translateX(-50%);
               white-space:nowrap; background:#fff; border:1px solid var(--line); color:var(--ink);
               font-size:10.5px; font-weight:700; padding:3px 7px; border-radius:9px;
               box-shadow:0 2px 7px rgba(20,30,60,.13); animation:bubbleBob 1.7s ease-in-out infinite; }
  .dog-bubble::after{ content:""; position:absolute; left:50%; top:100%; transform:translateX(-50%);
                      border:4px solid transparent; border-top-color:#fff; }
  @keyframes bubbleBob{ 0%,100%{transform:translateX(-50%) translateY(0)} 50%{transform:translateX(-50%) translateY(-2px)} }
  .dog-facing{ display:block; animation:face 24s ease-in-out infinite; }
  .dog{ display:block; animation:dogBob .8s ease-in-out infinite; }
  .dog .leg{ transform-box:fill-box; }
  .dog .legA{ animation:stepA .8s ease-in-out infinite; }
  .dog .legB{ animation:stepB .8s ease-in-out infinite; }
  .dog .tail{ transform-box:fill-box; transform-origin:bottom right; animation:wag .5s ease-in-out infinite; }
  @keyframes roam{ 0%{left:0} 47%{left:calc(100% - 52px)} 53%{left:calc(100% - 52px)} 100%{left:0} }
  @keyframes face{ 0%,47%{transform:scaleX(1)} 53%,100%{transform:scaleX(-1)} }
  @keyframes dogBob{ 0%,100%{transform:translateY(0)} 50%{transform:translateY(-1.5px)} }
  @keyframes stepA{ 0%,100%{transform:translateY(0)} 50%{transform:translateY(-3px)} }
  @keyframes stepB{ 0%,100%{transform:translateY(-3px)} 50%{transform:translateY(0)} }
  @keyframes wag{ 0%,100%{transform:rotate(0)} 50%{transform:rotate(-20deg)} }
  @media (prefers-reduced-motion: reduce){ .about-dog,.dog-facing,.dog,.dog *{ animation:none !important; } }
  .hero{ background:var(--card); border:1px solid var(--line); border-radius:18px;
         padding:22px 20px; margin-top:14px; box-shadow:0 4px 18px rgba(20,30,60,.05); }
  .badge{ display:inline-block; font-size:12.5px; font-weight:800; padding:5px 11px; border-radius:999px; }
  .badge-good{ background:var(--good-bg); color:var(--good-fg); }
  .badge-mid{ background:var(--mid-bg); color:var(--mid-fg); }
  .badge-bad{ background:var(--bad-bg); color:var(--bad-fg); }
  .hero-row{ display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
  .hero-left{ min-width:0; }
  .hero-right{ flex:none; display:flex; flex-direction:column; align-items:flex-end; gap:6px; padding-top:4px; text-align:right; }
  .gauge-mini{ font-size:14px; font-weight:800; max-width:140px; text-align:right; line-height:1.2; }
  .hero-label{ font-size:12.5px; color:var(--muted); }
  .hero-rate{ font-size:42px; font-weight:800; letter-spacing:-.02em; line-height:1.05; margin-top:2px; }
  .hero-chips{ display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
  .chip{ display:inline-block; font-size:12px; font-weight:700; padding:4px 9px;
         border-radius:8px; background:#f1f2f5; color:var(--muted); }
  .chip.up{ background:var(--up-bg); color:var(--up); }
  .chip.down{ background:var(--down-bg); color:var(--down); }
  .hero-meta{ font-size:11px; color:var(--muted); margin-top:8px; }
  .chip.flat{ background:#eceef1; color:var(--muted); }
  .feel-amt.flat{ color:var(--muted); }
  .verdict{ margin-top:16px; padding-top:14px; border-top:1px solid var(--line); }
  .verdict-head{ font-size:16px; font-weight:800; letter-spacing:-.01em; }
  .verdict-sub{ font-size:13px; color:var(--muted); margin-top:2px; }
  .verdict-sub2{ font-size:12.5px; color:var(--muted); margin-top:4px; font-weight:600; }
  .verdict-sub2.up{ color:var(--up); } .verdict-sub2.down{ color:var(--down); }
  .chart-tabs{ display:flex; gap:3px; background:#eceef1; border-radius:9px; padding:3px; margin:10px 0 8px; }
  .ctab{ flex:1; border:none; background:transparent; padding:6px 4px; border-radius:7px;
         font-size:11.5px; font-weight:700; color:var(--muted); cursor:pointer; }
  .ctab.active{ background:var(--card); color:var(--ink); box-shadow:0 1px 3px rgba(20,30,60,.1); }
  .gauge-wrap{ background:var(--card); border:1px solid var(--line); border-radius:14px;
               padding:14px 16px; margin-top:12px; }
  .gauge-top{ display:flex; align-items:baseline; justify-content:space-between; margin-bottom:9px; }
  .gauge-cap{ font-size:13px; font-weight:800; }
  .gauge-label{ font-size:13px; font-weight:800; }
  .gauge-bar{ position:relative; height:12px; border-radius:6px;
              background:linear-gradient(90deg,#1f9d57 0%,#79c267 27%,#d9b441 50%,#e2873a 73%,#e0383e 100%); }
  .gauge-ptr{ position:absolute; top:-5px; width:3px; height:22px; border-radius:2px;
              background:var(--ink); transform:translateX(-50%); transition:left .4s; box-shadow:0 0 0 2px #fff; }
  .gauge-scale{ display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-top:7px; }
  .gauge-basis{ font-size:12px; font-weight:700; color:var(--muted); margin-top:8px; text-align:center; }
  .chart-wrap{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; margin-top:12px; }
  #chart{ position:relative; height:132px; margin-top:2px; }
  .chart-svg{ width:100%; height:100%; display:block; border-radius:8px; }
  .mk{ position:absolute; transform:translate(-50%,-50%); pointer-events:none; }
  .mk-dot{ display:block; width:6px; height:6px; border-radius:50%; background:var(--ink); margin:0 auto; }
  .mk-lab{ position:absolute; left:50%; transform:translateX(-50%); white-space:nowrap;
           font-size:10.5px; font-weight:800; color:var(--ink); }
  .mk-hi .mk-lab{ bottom:calc(50% + 6px); }
  .mk-lo .mk-lab{ top:calc(50% + 6px); }
  .mk-now .mk-dot{ width:8px; height:8px; background:var(--brand); border:2px solid #fff; }
  .mk-avg{ left:auto; transform:translateY(-50%); }
  .mk-avg .mk-lab{ position:static; transform:none; left:auto; color:var(--muted); font-weight:700;
                   background:rgba(255,255,255,.78); padding:0 3px; border-radius:4px; }
  .mk-el .mk-lab{ left:0; transform:none; }       /* 왼쪽 끝: 안쪽(오른쪽)으로 */
  .mk-er .mk-lab{ left:auto; right:0; transform:none; }  /* 오른쪽 끝: 안쪽(왼쪽)으로 */
  .chart-cap{ font-size:11.5px; color:var(--muted); margin-top:9px; text-align:center; }
  .tldr{ background:var(--brand-bg); border-radius:12px; padding:13px 16px; margin-top:12px; }
  .tldr-cap{ font-size:11.5px; font-weight:800; color:var(--brand); letter-spacing:.02em; margin-bottom:5px; }
  .tldr-list{ margin:0; padding-left:17px; }
  .tldr-list li{ font-size:13.5px; line-height:1.6; margin:3px 0; color:#2b313d; }
  .impact{ margin-left:auto; font-size:12px; letter-spacing:-2px; }
  .impact-info, .gauge-info{ flex:none; margin-left:6px; width:18px; height:18px; border:none; border-radius:50%;
                background:#eceef1; color:var(--muted); font-size:12px; cursor:pointer; vertical-align:middle; padding:0;
                display:inline-flex; align-items:center; justify-content:center; }
  .impact-info:active, .gauge-info:active{ background:#dfe2e7; }
  .sec-note{ font-size:11.5px; color:var(--muted); margin:-2px 2px 10px; }
  .cal-guide{ background:var(--brand-bg); border-radius:12px; padding:12px 14px; margin-bottom:10px;
              font-size:13px; line-height:1.62; color:#2b313d; }
  .cal-item{ border-top:1px solid var(--line); }
  .cal-item:first-of-type{ border-top:none; }
  .cal-row{ display:flex; align-items:center; gap:10px; padding:11px 0; cursor:pointer; list-style:none; }
  .cal-row::-webkit-details-marker{ display:none; }
  .cal-dday{ flex:none; min-width:48px; text-align:center; font-size:12px; font-weight:800;
             color:var(--brand); background:var(--brand-bg); border-radius:8px; padding:6px 6px; }
  .cal-dday.today{ color:#fff; background:var(--brand); }
  .cal-dday.past{ color:var(--muted); background:#eceef1; }
  .cal-main{ flex:1; min-width:0; }
  .cal-name{ font-size:14px; font-weight:700; }
  .cal-star{ color:var(--mid-fg); font-size:11px; }
  .cal-impact{ font-size:12.5px; color:#41485a; margin-top:2px; line-height:1.5; }
  .cal-date{ flex:none; font-size:12px; color:var(--muted); }
  .cal-more{ flex:none; display:inline-flex; align-items:center; gap:3px; font-size:11.5px; font-weight:700;
             color:var(--brand); background:var(--brand-bg); padding:5px 10px; border-radius:999px; }
  .cal-caret{ font-size:9px; transition:transform .2s; }
  .cal-item[open] .cal-caret{ transform:rotate(180deg); }
  .cal-item[open] .cal-more{ background:var(--brand); color:#fff; }
  .cal-detail{ padding:2px 0 13px 58px; }
  .cal-why{ font-size:13px; color:#2b313d; line-height:1.62; }
  .cal-scn{ font-size:12.5px; color:#41485a; margin-top:7px; line-height:1.55; }
  .cal-scn b{ color:var(--ink); display:block; }
  .arc-row{ display:flex; align-items:center; gap:9px; padding:9px 0; border-top:1px solid var(--line);
            text-decoration:none; color:inherit; }
  .arc-row:first-of-type{ border-top:none; }
  .arc-date{ flex:none; font-size:12.5px; font-weight:700; color:var(--muted); min-width:40px; }
  .arc-badge{ flex:none; font-size:11px; padding:3px 8px; }
  .arc-rate{ flex:none; font-size:12.5px; font-weight:800; min-width:62px; }
  .arc-chg{ font-style:normal; font-weight:700; font-size:11px; }
  .arc-chg.up{ color:var(--up); } .arc-chg.down{ color:var(--down); }
  .arc-head{ flex:1; min-width:0; font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sec{ font-size:14px; font-weight:800; margin:26px 2px 11px; letter-spacing:-.01em;
        display:flex; align-items:center; gap:7px; }
  .sec::before{ content:""; width:3px; height:14px; border-radius:2px; background:var(--brand); }
  .persona{ margin-top:12px; }
  .persona-q{ font-size:12px; color:var(--muted); font-weight:700; margin:0 2px 6px; }
  .tabs{ display:flex; gap:3px; background:#eceef1; border-radius:10px; padding:3px; margin-bottom:10px; }
  .tab{ flex:1; border:none; background:transparent; padding:8px 4px; border-radius:8px;
        font-size:12px; font-weight:700; color:var(--muted); cursor:pointer; white-space:nowrap; }
  .tab.active{ background:var(--card); color:var(--ink); box-shadow:0 1px 3px rgba(20,30,60,.1); }
  .feel-grid{ display:grid; grid-template-columns:1fr 1fr; gap:9px; }
  .feel-cell{ border:1px solid var(--line); border-radius:12px; padding:12px; background:var(--card); }
  .feel-top{ display:flex; align-items:baseline; gap:5px; flex-wrap:wrap; }
  .feel-ico{ font-size:15px; }
  .feel-logo{ width:18px; height:18px; padding:2px; box-sizing:border-box; border-radius:4px;
              object-fit:contain; background:#fff; align-self:center; }
  #rotCell{ transition:opacity .22s; }
  .feel-name{ font-size:13.5px; font-weight:700; }
  .feel-tag{ font-size:11px; color:var(--muted); }
  .feel-amt{ font-size:17px; font-weight:800; margin-top:8px; letter-spacing:-.01em; }
  .feel-amt.up{ color:var(--up); } .feel-amt.down{ color:var(--down); }
  .feel-amt.total{ color:var(--ink); }
  .feel-chg{ font-size:11px; font-weight:600; }
  .feel-chg.up{ color:var(--up); } .feel-chg.down{ color:var(--down); }
  .prose{ font-size:14.5px; line-height:1.72; color:#2b313d; margin:0; }
  .prose .hl{ font-weight:700; text-decoration:underline; text-underline-offset:3px;
              text-decoration-thickness:1.5px; text-decoration-color:rgba(59,91,219,.55); }
  .term{ border-bottom:1px dashed var(--brand); cursor:pointer; white-space:nowrap; }
  .mag{ font-size:.72em; margin-left:1px; opacity:.65; }
  .def-backdrop{ position:fixed; inset:0; background:rgba(0,0,0,.35); opacity:0;
                 pointer-events:none; transition:opacity .2s; z-index:40; }
  .def-backdrop.on{ opacity:1; pointer-events:auto; }
  .def-sheet{ position:fixed; left:0; right:0; bottom:0; max-width:480px; margin:0 auto;
              background:var(--card); border-radius:16px 16px 0 0; padding:18px 20px 26px;
              box-shadow:0 -6px 24px rgba(0,0,0,.15); transform:translateY(110%);
              transition:transform .25s; z-index:41; }
  .def-sheet.on{ transform:translateY(0); }
  .def-sheet{ max-height:74vh; overflow-y:auto; }
  .def-term{ font-size:16px; font-weight:800; }
  .def-text{ font-size:14px; color:#2b313d; line-height:1.66; margin-top:6px; white-space:pre-line; }
  .def-hint{ font-size:11.5px; color:var(--muted); margin-top:12px; }
  .tip{ background:var(--brand-bg); border-radius:12px; padding:13px 15px; }
  .th-label{ font-size:12px; color:var(--muted); font-weight:700; }
  .th-big{ font-size:24px; font-weight:800; letter-spacing:-.02em; margin:2px 0 3px; line-height:1.15; }
  .th-big.save{ color:var(--down); } .th-big.cost{ color:var(--up); }
  .th-sub{ font-size:12px; color:var(--muted); }
  .tip-foot{ font-size:12.5px; color:#41485a; margin-top:12px; line-height:1.5; }
  .factor{ padding:14px 0; border-top:1px solid var(--line); }
  .factor:first-of-type{ border-top:none; padding-top:2px; }
  .factor-head{ display:flex; align-items:center; gap:8px; font-size:14.5px; font-weight:800; }
  .rank{ flex:none; width:20px; height:20px; border-radius:6px; background:var(--brand); color:#fff;
         font-size:12px; font-weight:800; display:flex; align-items:center; justify-content:center; }
  .factor-line{ font-size:13px; color:var(--brand); margin:6px 0 8px; font-weight:700; }
  .factor-bullets{ margin:0; padding-left:17px; }
  .factor-bullets li{ font-size:13.5px; line-height:1.62; margin:4px 0; color:#2b313d; }
  .factor-src{ font-size:11.5px; color:var(--muted); margin-top:9px; display:flex;
               align-items:center; gap:5px; flex-wrap:wrap; }
  .src-chip{ display:inline-flex; align-items:center; justify-content:center; min-width:18px; height:18px;
             padding:0 5px; border-radius:5px; background:#eef0f3; color:var(--muted);
             font-size:11px; font-weight:700; text-decoration:none; }
  .actions{ display:flex; gap:8px; margin-top:26px; }
  .btn{ flex:1; text-align:center; padding:13px; border-radius:12px; font-size:14px; font-weight:800;
        cursor:pointer; border:1px solid var(--line); text-decoration:none; }
  .btn-primary{ background:var(--brand); color:#fff; border-color:var(--brand); }
  .btn-secondary{ background:var(--card); color:var(--ink); }
  footer{ text-align:center; color:var(--muted); font-size:11.5px; margin-top:22px; line-height:1.7; }
  .views{ font-size:12px; color:var(--muted); margin-bottom:8px; }
  .views b{ color:var(--ink); font-weight:800; }
  .muted{ color:var(--muted); }
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="top-text">
      <div class="ey">매달 환전하는 우리를 위한</div>
      <h1 class="head">이번 달, 지금 환전해도 될까?</h1>
    </div>
    <div class="dog-track">
    <button class="about-dog" data-term="만든 사람" data-def="__ABOUT__" aria-label="만든 사람 소개" title="눌러서 소개 보기">
      <span class="dog-bubble">__BUBBLE__</span>
      <span class="dog-facing">
        <svg class="dog" viewBox="0 0 72 48" width="50" height="34" role="img">
          <path class="tail" d="M13 18 q-7 -2 -6 -10" stroke="#8b94a0" stroke-width="4" fill="none" stroke-linecap="round"/>
          <rect class="leg legA" x="16" y="30" width="4.6" height="12" rx="2.3" fill="#7f8893"/>
          <rect class="leg legB" x="24" y="30" width="4.6" height="12" rx="2.3" fill="#8b94a0"/>
          <rect class="leg legB" x="41" y="30" width="4.6" height="12" rx="2.3" fill="#7f8893"/>
          <rect class="leg legA" x="49" y="30" width="4.6" height="12" rx="2.3" fill="#8b94a0"/>
          <rect x="11" y="16" width="46" height="18" rx="9" fill="#9aa3ad"/>
          <rect x="46" y="7" width="21" height="21" rx="7" fill="#9aa3ad"/>
          <path d="M48 9 q1 -7 7 -5 l-1 8 z" fill="#7f8893"/>
          <path d="M53 12 q5 -2 9 1 q-4 0 -9 2 z" fill="#d3d8de"/>
          <circle cx="58" cy="16.5" r="1.8" fill="#1c2230"/>
          <path d="M60 19 h8 v4 q0 6 -5 7 q-4 -1 -3 -7 z" fill="#d3d8de"/>
          <circle cx="67" cy="20" r="1.9" fill="#1c2230"/>
        </svg>
      </span>
    </button>
    </div>
  </div>

  <div class="persona">
    <div class="tabs" id="tabs">
      <button class="tab active" data-set="student">미국 유학생</button>
      <button class="tab" data-set="investor">해외 주식 투자자</button>
      <button class="tab" data-set="traveler">미국 여행자</button>
    </div>
  </div>

  <section class="hero">
    <div class="hero-row">
      <div class="hero-left">
        <div class="hero-label">지금 원·달러 환율</div>
        <div class="hero-rate" id="rateNow">__RATE__</div>
      </div>
      <div class="hero-right">
        <span id="rateDelta" class="chip"></span>
        <span id="gaugeMini" class="gauge-mini"></span>
        <div id="rateMeta" class="hero-meta">실시간 …</div>
      </div>
    </div>
  </section>

  __TLDR__

  <section id="feelBox">
    <h3 class="sec">어제보다 이만큼 더 들어요</h3>
    <div id="feelList" class="feel-grid"><p class="muted">실시간 환율로 계산 중…</p></div>
  </section>

  <section class="gauge-wrap" id="gaugeWrap" style="display:none">
    <div class="gauge-top"><span class="gauge-cap">환전 매력도 <button class="gauge-info" id="gaugeInfo" aria-label="페르소나별 기준 설명">i</button></span><span id="gaugeLabel" class="gauge-label"></span></div>
    <div class="gauge-bar"><span class="gauge-ptr" id="gaugePtr"></span></div>
    <div class="gauge-scale"><span>지금 사기 좋음</span><span>아까움</span></div>
  </section>

  <section class="chart-wrap" id="chartWrap" style="display:none">
    <div class="gauge-top"><span class="gauge-cap">환율 흐름</span><span id="chartNow" class="gauge-label"></span></div>
    <div class="chart-tabs" id="chartTabs">
      <button class="ctab" data-d="7">1주</button>
      <button class="ctab" data-d="22">1개월</button>
      <button class="ctab active" data-d="63">3개월</button>
    </div>
    <div id="chart"></div>
    <div class="chart-cap" id="chartCap"></div>
  </section>

  __CALENDAR_SECTION__

  <section>
    <h3 class="sec">왜 이런 걸까요?</h3>
    <p class="prose">__WHY__</p>
  </section>

  <section id="tipBox">
    <h3 class="sec">매달 환전한다면</h3>
    <div class="tip">
      <p class="prose" id="tipAdvice">한 번에 몰아 사기보다 나눠 사면, 환율이 출렁여도 평균 단가로 살 수 있어요.</p>
      <div class="tip-hero" id="tipHero"></div>
    </div>
  </section>

  <section>
    <h3 class="sec">__NEWS_TITLE__</h3>
    __NEWS__
  </section>

  __ARCHIVE_SECTION__

  <div class="actions">
    <button id="shareBtn" class="btn btn-primary" onclick="sharePage(this)">친구에게 공유하기</button>
    <a class="btn btn-secondary" href="__FEEDBACK_URL__" target="_blank" rel="noopener">피드백 남기기</a>
  </div>

  <footer>
    <div class="views" id="views">조회 오늘 <b id="vToday">–</b> · 누적 <b id="vTotal">–</b></div>
    발행 __PUBLISHED__ · 환율 <span id="rateSrc">ECB</span> · 뉴스 __DATE__<br>
    참고용이며 투자 조언이 아니에요. 실제 환전 전 한 번 더 확인하세요
  </footer>
</div>
<div class="def-backdrop" id="defBackdrop"></div>
<div class="def-sheet" id="defSheet">
  <div class="def-term"></div>
  <div class="def-text"></div>
  <div class="def-hint">아무 곳이나 누르면 닫혀요</div>
</div>
<script>
// Live USD->KRW rate, fetched every time the page opens (no API key, CORS-friendly).
const RATE_FALLBACK = __RATE_NUM__;     // build-time rate, used only if fetch fails
// Comparison item sets per audience (toggle). All three are identical for now —
// to be customized later (e.g. investor: 테슬라 1주 / 애플 1주).
const LOGO = __LOGOS__;   // {TICKER: data-URI} 빌드 때 삽입(자체 완결)
const ITEM_SETS = {
  student: [
    { ico:'🏠', name:'월세', tag:'서블렛 한 달', usd:1000 },
    { ico:'🛒', name:'마트 장보기', tag:'일주일 치 식비', usd:80 },
    { ico:'🤖', name:'챗GPT 플러스', tag:'AI 월구독료', usd:20 },
    { ico:'☕', name:'스타벅스 아아', tag:'톨 사이즈', usd:3.65 },
  ],
  investor: [
    { ico:'🐷', name:'시드머니', tag:'투자할 돈', usd:1000 },
    { ico:'📈', name:'S&P500', tag:'SPY · 1주', usd:600, stock:'SP500' },
    { rot:[
        { ico:'🚗', name:'테슬라',  tk:'TSLA',  usd:400 },
        { ico:'🎮', name:'엔비디아', tk:'NVDA',  usd:180 },
        { ico:'📦', name:'아마존',  tk:'AMZN',  usd:230 },
        { ico:'🔍', name:'구글',    tk:'GOOGL', usd:200 },
        { ico:'🔮', name:'팔란티어', tk:'PLTR',  usd:90 },
      ] },
    { ico:'🎁', name:'배당금 수령', tag:'오르면 이득!', usd:100, income:true },
  ],
  traveler: [
    { ico:'🏨', name:'호텔 1박', tag:'3성급 평균 1박', usd:160 },
    { ico:'🍔', name:'쉐이크쉑', tag:'쉑버거 세트', usd:15 },
    { ico:'🚇', name:'대중교통', tag:'편도 요금', usd:3 },
    { ico:'💧', name:'마트 생수 한 병', tag:'500ml', usd:1.99 },
  ],
};
// 게이지 가중치 = [1주, 1달, 3개월]. 페르소나의 '구매 시야'에 맞춤(합=1).
const SET_W = {
  student:  { w:[0.10, 0.30, 0.60], prim:'long',  lens:'유학생은 상대적으로 장기 관점',
              labels:['지갑 평화','선방하는 중','평소 그대로','살짝 쓰라림','눈물이 앞을 가림'] },
  traveler: { w:[0.30, 0.50, 0.20], prim:'mid',   lens:'여행은 상대적으로 중기 관점',
              labels:['환전 오픈런','가벼운 발걸음','예상했던 예산','강제 아이쇼핑','숨만 쉬어도 텅장'] },
  investor: { w:[0.50, 0.35, 0.15], prim:'short', lens:'투자자는 상대적으로 단기 관점',
              labels:['환차익 왕이득','줍줍하기 좋은 날','시드머니 평단가 수준','눈물의 고점 물타기','예수금 삭제 마술'] },
};
let activeSet = 'student';
let lastDelta = null;   // 어제 대비 정수 편차 기억(탭 전환 시 재렌더용)
let curRate = null;     // 현재 환율(여행자 탭 실제 금액 계산용)
let avg1w = null;       // 최근 7영업일 평균(투자자 탭 손해/이득 기준)
let stockPx = {};       // 종목 전일 종가 맵 {TSLA:403, ...}
let rotI = 0;           // 회전 종목 인덱스
let chartRates = null, chartNow = null, chartDays = 63;  // 차트 기간 토글용(영업일 점 수)
const won = n => Math.round(n).toLocaleString('ko-KR');

function render(rateNow, rateYest){
  const elNow = document.getElementById('rateNow');
  const elDelta = document.getElementById('rateDelta');
  const elMeta = document.getElementById('rateMeta');
  const elList = document.getElementById('feelList');
  const elBox = document.getElementById('feelBox');
  const elTitle = elBox.querySelector('h3');

  elNow.textContent = won(rateNow) + '원';

  if(rateYest == null){            // no day-over-day data: show rate only
    elDelta.textContent = '';
    elMeta.textContent = '실시간 환율 기준';
    elBox.style.display = 'none';
    return;
  }
  const d = rateNow - rateYest;     // +면 달러 비싸짐(원화 약세)
  const dr = Math.round(d);          // 표시 환율 편차와 환산 금액을 같은 정수로 통일(일관성)
  const up = dr > 0, flat = (dr === 0);
  elDelta.textContent = flat ? '어제와 비슷' : `어제 ${up?'▲':'▼'} ${won(Math.abs(dr))}원`;
  elDelta.className = 'chip ' + (flat ? 'flat' : (up?'up':'down'));
  elMeta.textContent = '실시간 기준 · 어제 ' + won(rateYest) + '원';

  curRate = rateNow;
  lastDelta = dr;
  renderFeel(dr);   // 제목·셀은 renderFeel이 페르소나별로 처리
}

// 상황별 고정 조언 10개(수준×추세 + 신고가). 모두 사실만, AI 아님.
const ADV = [
  { adv:'지금 사상 최고 수준이라 큰돈을 한 번에 고정하면 위험해요. 꼭 필요한 만큼만 사고 나머진 나눠서요.', calc:'A' },
  { adv:'평소보다 비싼데 더 오르는 중이에요. 한 번에 몰지 말고 나눠 사며 위험을 줄이세요.', calc:'A' },
  { adv:'비싼 편이지만 잠잠해요. 서두르지 말고 나눠 사며 관망하세요.', calc:'B' },
  { adv:'비쌌지만 내려오는 중이에요. 나눠 담으며 추가 하락을 노려도 좋아요.', calc:'C', ref:'high' },
  { adv:'평범하지만 오르는 추세예요. 나눠 사며 평균 단가를 관리하세요.', calc:'A' },
  { adv:'큰 변동이 없어요. 평소대로 매주 조금씩 사면 충분해요.', calc:'A' },
  { adv:'내려오는 중이에요. 나눠 사다 더 빠지면 비중을 늘려도 돼요.', calc:'C', ref:'high' },
  { adv:'평소보다 싸지만 반등하는 중이에요. 필요한 만큼은 지금, 나머지는 나눠서요.', calc:'C', ref:'low' },
  { adv:'싼 편이고 잠잠해요. 이럴 때 평소보다 조금 더 담아둘 만해요.', calc:'B' },
  { adv:'싸고 더 내리는 중이에요. 천천히 나눠 사며 바닥을 노려보세요.', calc:'C', ref:'low' },
];
const heroHTML = r => `<div class="th-label">${r.label}</div><div class="th-big ${r.dir}">${r.big}</div><div class="th-sub">${r.sub}</div>`;
function calcA(cl, n){  // 매주 $200×4 분할 vs 오늘 $800 일괄
  const weekly = [curRate]; [5,10,15].forEach(o => { const i = n-1-o; if(i>=0) weekly.push(cl[i]); });
  if(weekly.length < 2) return null;
  const avg = Math.round(weekly.reduce((a,b)=>a+b,0)/weekly.length), today = Math.round(curRate);
  const d = today - avg, amt = Math.round(Math.abs(d)*800);
  let big, dir;
  if(amt < 100){ big = '거의 같았어요'; dir = ''; }
  else if(d > 0){ big = won(amt)+'원 아꼈어요'; dir = 'save'; }
  else { big = won(amt)+'원 더 들었어요'; dir = 'cost'; }
  return { label:'매주 $200씩 4주 나눠 샀다면', big, dir, sub:`나눠 사기 평균 ${won(avg)}원 · 오늘 한 번에 ${won(today)}원` };
}
function calcB(v){  // 오늘 vs 최근 한 달 평균 ($800 기준)
  if(v.length < 5) return null;
  const avg = Math.round(v.reduce((a,b)=>a+b,0)/v.length), today = Math.round(curRate);
  const d = today - avg, amt = Math.round(Math.abs(d)*800);
  let big, dir;
  if(amt < 100){ big = '평균과 비슷해요'; dir = ''; }
  else if(d > 0){ big = '$800에 '+won(amt)+'원 더 비싸요'; dir = 'cost'; }
  else { big = '$800에 '+won(amt)+'원 더 싸요'; dir = 'save'; }
  return { label:'최근 한 달 평균과 비교하면', big, dir, sub:`한 달 평균 ${won(avg)}원 · 오늘 ${won(today)}원` };
}
function calcC(v, ref){  // 오늘 vs 최근 3개월 최고/최저
  if(v.length < 10) return null;
  const today = Math.round(curRate);
  if(ref === 'high'){ const hi = Math.round(Math.max(...v)), d = hi - today;
    return { label:'최근 3개월 최고와 비교하면', big: d <= 0 ? '최고치 수준이에요' : '▼'+won(d)+'원 내려왔어요', dir:'',
             sub:`3개월 최고 ${won(hi)}원 · 오늘 ${won(today)}원` }; }
  const lo = Math.round(Math.min(...v)), d = today - lo;
  return { label:'최근 3개월 최저와 비교하면', big: d <= 0 ? '최저치 수준이에요' : '▲'+won(d)+'원 위예요', dir:'',
           sub:`3개월 최저 ${won(lo)}원 · 오늘 ${won(today)}원` };
}
function renderTip(){
  // 오늘 상황 판정(수준×추세 + 신고가) -> 고정 조언 + 그에 맞는 예시 계산. 전부 실측.
  const adv = document.getElementById('tipAdvice'), hero = document.getElementById('tipHero');
  if(!adv || !hero || !chartRates || !curRate) return;
  const cl = Object.keys(chartRates).sort().map(k => chartRates[k].KRW), n = cl.length;
  if(n < 5) return;
  const v63 = cl.slice(-63), v22 = cl.slice(-22);
  const p = v63.filter(x => x <= curRate).length / v63.length;   // 3개월 백분위
  // 추세: 최근(3거래일)과 2주가 '둘 다 같은 방향'일 때만 상승/하락. 엇갈리면(튀었다 내려옴 등) 횡보.
  const ref3 = (n > 3) ? cl[n-1-3] : cl[0];
  const ref2w = (n > 10) ? cl[n-1-10] : cl[0];
  const sUp = curRate > ref3*1.005, sDn = curRate < ref3*0.995;
  const mUp = curRate > ref2w*1.005, mDn = curRate < ref2w*0.995;
  const trend = (sUp && mUp) ? 0 : (sDn && mDn) ? 2 : 1;          // 0상승 1횡보 2하락
  const atHigh = curRate >= Math.max(...v63) - 0.3;
  let idx;
  if(atHigh && trend === 0) idx = 0;                             // 신고가·급등
  else { const lvl = p >= 0.66 ? 0 : (p <= 0.34 ? 2 : 1); idx = 1 + lvl*3 + trend; }
  const s = ADV[idx] || ADV[5];
  adv.textContent = s.adv;
  const r = s.calc === 'A' ? calcA(cl, n) : s.calc === 'B' ? calcB(v22) : calcC(v63, s.ref);
  hero.innerHTML = r ? heroHTML(r) : '';
}
function iconHTML(s){
  // 기업 로고(base64 맵에 있으면) -> 실패 시 이모지로 폴백.
  const u = s.tk && LOGO[s.tk];
  return u
    ? `<img class="feel-logo" src="${u}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='inline'"><span class="feel-ico" style="display:none">${s.ico}</span>`
    : `<span class="feel-ico">${s.ico}</span>`;
}
function rotCard(s){
  // 회전 카드 1칸: 종목 1주를 최근 7영업일 평균 환율 대비 손해/이득.
  const usd = stockPx[s.tk] || s.usd;
  let amt = '—', dir = 'flat';
  if(avg1w && curRate){
    const gap = Math.round((curRate - avg1w) * usd);
    const loss = gap > 0;
    amt = gap === 0 ? '평균과 같음' : won(Math.abs(gap)) + '원 ' + (loss ? '손해' : '이득');
    dir = gap === 0 ? 'flat' : (loss ? 'up' : 'down');
  }
  const top = `${iconHTML(s)}<span class="feel-name">${s.name}</span> <span class="feel-tag">${s.tk} · 1주 · $${usd}</span>`;
  return { top, amt, dir };
}
function renderFeel(dr){
  // dr = 어제 대비 정수 편차. 0이면 중립(회색 '–'), 곱은 표시 편차와 정확히 맞음.
  const flat = (dr === 0), up = dr > 0, sign = up ? '▲' : '▼';
  const traveler = (activeSet === 'traveler');
  const investor = (activeSet === 'investor' && avg1w && curRate);
  // 제목: 여행자=지금 실제 금액 / 투자자=한 주 평균 대비 / 그 외=어제 대비 변화
  const title = document.getElementById('feelBox').querySelector('h3');
  let investorTitle = '';
  if(investor){
    const d = curRate - avg1w;
    investorTitle = d > 0 ? '지금 환전하면 이만큼 손해예요 (최근 1주 평균 기준)'
      : d < 0 ? '지금 환전하면 이만큼 이득이에요 (최근 1주 평균 기준)'
      : '지금 환율은 최근 1주 평균과 비슷해요';
  }
  title.textContent = traveler ? '지금 환율로 이만큼 들어요'
    : investor ? investorTitle
    : (flat ? '어제와 거의 같아요' : (up ? '어제보다 이만큼 더 들어요' : '어제보다 이만큼 덜 들어요'));
  document.getElementById('feelList').innerHTML = ITEM_SETS[activeSet].map(it => {
    if(it.rot){   // 회전 카드(개별주 5종 4초 순환)
      const rc = rotCard(it.rot[rotI % it.rot.length]);
      return `<div class="feel-cell" id="rotCell"><div class="feel-top">${rc.top}</div><div class="feel-amt ${rc.dir}">${rc.amt}</div></div>`;
    }
    let amt, dir;
    if(traveler){
      const total = won(Math.round((curRate||0) * it.usd)) + '원';
      const chg = flat ? '' : ` <span class="feel-chg ${up?'up':'down'}">(어제 ${sign}${won(Math.abs(dr)*it.usd)}원)</span>`;
      amt = total + chg;   // 지금 실제 금액 + (어제 대비 변화)
      dir = 'total';
    } else if(investor){
      const gap = Math.round((curRate - avg1w) * it.usd);  // 최근 7영업일 평균 대비
      const loss = it.income ? (gap < 0) : (gap > 0);       // 사는 항목 비쌈=손해 / 배당금은 반대
      if(gap === 0){ amt = '평균과 같음'; dir = 'flat'; }
      else { amt = won(Math.abs(gap)) + '원 ' + (loss ? '손해' : '이득'); dir = loss ? 'up' : 'down'; }
    } else {
      // 비용 항목: 환율↑=빨강(더 든다). 수입(배당금): 환율↑=초록(더 받는다) -> 색 반전.
      const good = it.income ? up : !up;
      dir = flat ? 'flat' : (good ? 'down' : 'up');
      amt = flat ? '–' : sign + won(Math.abs(dr)*it.usd) + '원';
    }
    return `
    <div class="feel-cell">
      <div class="feel-top"><span class="feel-ico">${it.ico}</span><span class="feel-name">${it.name}</span> <span class="feel-tag">${it.tag} · $${it.usd}</span></div>
      <div class="feel-amt ${dir}">${amt}</div>
    </div>`;
  }).join('');
}

function sharePage(btn){
  const url = location.href;
  const flash = (msg, ms) => { btn.textContent = msg; setTimeout(()=>btn.textContent='친구에게 공유하기', ms); };
  // file:// 이나 localhost 경로는 친구가 못 여는 주소 -> 안내만.
  if(!/^https?:\/\//i.test(url)){
    flash('호스팅하면 공유돼요 (지금은 내 PC 파일)', 2600);
    return;
  }
  if(navigator.share){ navigator.share({title:document.title, url}).catch(()=>{}); }
  else if(navigator.clipboard){ navigator.clipboard.writeText(url).then(()=>flash('링크 복사됨 ✓', 1500)); }
}

function wonKo(n){  // 35000 -> "3만 5천", 8000 -> "8천" (round to 1,000)
  n = Math.round(n/1000)*1000;
  const man = Math.floor(n/10000), cheon = (n%10000)/1000;
  let s = '';
  if(man) s += man + '만 ';
  if(cheon) s += cheon + '천 ';
  return (s.trim() || '0') + '원';
}

const ANCHOR_USD = 1000;  // 부제 "$N 바꾸면" 기준 금액
function renderBaseline(rateNow, rates){
  // 이중 기준: 장기(3개월 평균 대비) + 단기(1개월 흐름에서의 위치).
  const el = document.getElementById('verdictSub');
  const el2 = document.getElementById('verdictSub2');
  if(!el) return;
  const pick = n => { const k = Object.keys(rates).sort(); return k.slice(-n).map(dt => rates[dt].KRW); };

  // 장기(3개월 ≈ 63영업일): 평균 대비 + $1,000 체감
  const v90 = pick(63);
  if(v90.length >= 5){
    const avg = v90.reduce((a,b)=>a+b,0)/v90.length, dr = Math.round(rateNow - avg);
    if(dr === 0){ el.textContent = '최근 3개월 평균과 거의 같아요'; }
    else {
      const extra = wonKo(Math.abs(dr)*ANCHOR_USD), usd = '$' + ANCHOR_USD.toLocaleString('en-US');
      el.textContent = dr > 0
        ? `최근 3개월 평균보다 ${won(dr)}원 비싸요 — ${usd} 바꾸면 ${extra} 더 나가요`
        : `최근 3개월 평균보다 ${won(-dr)}원 싸요 — ${usd} 바꾸면 ${extra} 덜 나가요`;
    }
  }
  // 단기(1개월): 한 달 고점 대비 얼마나 눌렸나
  if(el2){
    const v30 = pick(22);
    if(v30.length >= 4){
      const hi = Math.max(...v30), lo = Math.min(...v30), pos = (rateNow-lo)/((hi-lo)||1);
      const down = won(Math.round(hi - rateNow));
      let t, c;
      if(pos <= 0.35){ t = `단, 최근 한 달 고점보다 ${down}원 내려와 단기적으론 살만한 편이에요`; c = 'down'; }
      else if(pos >= 0.65){ t = '게다가 최근 한 달 중에서도 높은 편이라 단기 부담도 있어요'; c = 'up'; }
      else { t = `최근 한 달 고점보다 ${down}원 내려온 중간 수준이에요`; c = 'flat'; }
      el2.textContent = t; el2.className = 'verdict-sub2 ' + c;
    } else { el2.textContent = ''; }
  }
}

function renderGauge(rateNow, rates){
  // 종합 매력도: [1주,1달,3개월] 백분위를 페르소나 가중치로 결합. 낮을수록 쌈=추천.
  const wrap = document.getElementById('gaugeWrap');
  const pctIn = n => {
    const k = Object.keys(rates).sort();
    const vals = k.slice(-n).map(dt => rates[dt].KRW);
    if(vals.length < 2) return null;
    return vals.filter(v => v <= rateNow).length / vals.length;  // 1에 가까울수록 비쌈
  };
  const p7 = pctIn(7), p30 = pctIn(22), p90 = pctIn(63);  // 7/22/63 영업일
  if(p90 == null) return;
  const w = SET_W[activeSet] || SET_W.student;
  const a7 = (p7 != null) ? p7 : (p30 != null ? p30 : p90);   // 짧은 창 없으면 다음 창으로 대체
  const a30 = (p30 != null) ? p30 : p90;
  const pct = w.w[0]*a7 + w.w[1]*a30 + w.w[2]*p90;            // [1주,1달,3개월] 가중(합=1)
  const labels = w.labels || ['평소보다 낮음','약간 낮음','평소 수준','약간 높음','평소보다 높음'];
  const colors = ['#1f9d57','#79c267','#b58900','#e2873a','#e0383e'];
  // 백분위(pct)를 게이지 위치(disp)로 비선형 보간: 가운데(연초록~주황)를 넓게,
  // 빨강/찐초록은 양 끝 극단(상·하위 10%)에서만 나오도록. 바 크기는 그대로 균등.
  const remap = p => { const xs=[0,0.10,0.30,0.70,0.90,1], ys=[0,0.20,0.40,0.60,0.80,1];
    for(let i=1;i<xs.length;i++){ if(p<=xs[i]){ const t=(p-xs[i-1])/(xs[i]-xs[i-1]); return ys[i-1]+t*(ys[i]-ys[i-1]); } } return 1; };
  const disp = remap(pct);
  const zone = Math.min(4, Math.floor(disp*5));
  const lab = document.getElementById('gaugeLabel');
  lab.textContent = labels[zone];
  lab.style.color = colors[zone];
  const mini = document.getElementById('gaugeMini');
  if(mini){ mini.textContent = labels[zone]; mini.style.color = colors[zone]; }
  document.getElementById('gaugePtr').style.left = (disp*100).toFixed(1) + '%';
  wrap.style.display = 'block';
}

function renderChart(rateNow, rates, days){
  // 선택 기간 라인 + 평균선, 평균 위(비쌈)=빨강/아래(쌈)=초록 구간, 현재 점.
  days = days || 63;
  const keys = Object.keys(rates).sort().slice(-days);  // 최근 N 영업일(점)
  if(keys.length < 2) return;
  const vals = keys.map(k => rates[k].KRW);
  const min = Math.min(...vals), max = Math.max(...vals), span = (max-min) || 1;
  // 데이터는 가운데 띠[yTop, yTop+yBand]에만 -> 위/아래 여백에 라벨이 들어가 선을 안 가림.
  const W = 320, H = 96, padL = 8, padR = 30, yTop = 20, yBand = 56;  // 왼쪽 여백↓(평균은 우측)
  const avg = vals.reduce((a,b)=>a+b,0)/vals.length;
  const X = i => padL + (vals.length>1 ? i/(vals.length-1) : 0) * (W-padL-padR);
  const Y = v => yTop + yBand*(1-(v-min)/span);
  const avgY = Y(avg), above = rateNow >= avg;
  const line = vals.map((v,i)=>`${i?'L':'M'}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ');
  // 점 라벨(겹침/잘림 방지로 SVG 텍스트 대신 % 오버레이). 가장자리는 안쪽 정렬.
  const mk = (i,v,cls,lab) => {
    const xp = X(i)/W*100, yp = Y(v)/H*100;
    const e = xp < 16 ? ' mk-el' : (xp > 84 ? ' mk-er' : '');
    return `<div class="mk ${cls}${e}" style="left:${xp.toFixed(1)}%;top:${yp.toFixed(1)}%">`
         + `<span class="mk-dot"></span>` + (lab ? `<span class="mk-lab">${lab}</span>` : '') + `</div>`;
  };
  const iHi = vals.indexOf(max), iLo = vals.indexOf(min), iNow = vals.length-1;
  // 최고/최저는 위치로 자명 -> 숫자만. 지금은 히어로에 크게 있으니 점만.
  let marks = mk(iHi,max,'mk-hi',won(max)) + mk(iLo,min,'mk-lo',won(min));
  if(iNow!==iHi && iNow!==iLo) marks += mk(iNow,rateNow,'mk-now','');
  marks += `<div class="mk mk-avg" style="right:1%;top:${(avgY/H*100).toFixed(1)}%"><span class="mk-lab">평균 ${won(avg)}</span></div>`;
  document.getElementById('chart').innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="chart-svg">
      <rect x="0" y="0" width="${W}" height="${avgY.toFixed(1)}" fill="var(--up-bg)"/>
      <rect x="0" y="${avgY.toFixed(1)}" width="${W}" height="${(H-avgY).toFixed(1)}" fill="var(--down-bg)"/>
      <line x1="0" y1="${avgY.toFixed(1)}" x2="${W}" y2="${avgY.toFixed(1)}" stroke="#9aa3ad" stroke-width="1" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>
      <path d="${line}" fill="none" stroke="var(--ink)" stroke-width="2" vector-effect="non-scaling-stroke"/>
    </svg>` + marks;
  const lab = document.getElementById('chartNow');
  lab.textContent = above ? '평균 위 (비쌈)' : '평균 아래 (쌈)';
  lab.style.color = above ? 'var(--up)' : 'var(--down)';
  const dr = Math.round(rateNow - avg);
  document.getElementById('chartCap').textContent =
    dr === 0 ? '지금은 이 기간 평균과 거의 같아요'
      : `지금은 이 기간 평균보다 ${dr>0?'▲':'▼'}${won(Math.abs(dr))}원 ${dr>0?'비싼':'싼'} 편이에요`;
  document.getElementById('chartWrap').style.display = 'block';
}

async function loadViews(){
  // Abacus hit counter: one key for the running total, one per day for "today"(KST).
  const today = new Date(Date.now() + 9*3600000).toISOString().slice(0,10);
  try{
    const [t, dd] = await Promise.all([
      fetch(`https://abacus.jasoncameron.dev/hit/__VIEWS_NS__/total`).then(r=>r.json()),
      fetch(`https://abacus.jasoncameron.dev/hit/__VIEWS_NS__/${today}`).then(r=>r.json())
    ]);
    document.getElementById('vTotal').textContent = (t.value||0).toLocaleString('ko-KR');
    document.getElementById('vToday').textContent = (dd.value||0).toLocaleString('ko-KR');
  }catch(e){
    document.getElementById('views').style.display = 'none';
  }
}

async function loadRate(){
  // Same-origin rate.json (네이버 매매기준율, 서버가 주기적으로 갱신). No key, no CORS.
  try{
    const r = await fetch('./rate.json', {cache:'no-store'});
    if(!r.ok) throw new Error('http '+r.status);
    const j = await r.json();
    const rates = {};
    (j.series||[]).forEach(p => { if(p && p.date) rates[p.date] = {KRW: p.close}; });
    const today = j.rate, yest = (j.prev != null ? j.prev : null);
    const _l7 = Object.keys(rates).sort().slice(-7).map(k => rates[k].KRW);  // 최근 7영업일
    avg1w = _l7.length ? _l7.reduce((a,b)=>a+b,0)/_l7.length : null;
    if(j.stocks){   // 전일 종가($): SPY는 고정 카드, 나머지는 회전 카드가 stockPx에서 조회.
      stockPx = j.stocks;
      ITEM_SETS.investor.forEach(it => { if(it.stock && j.stocks[it.stock]) it.usd = j.stocks[it.stock]; });
    }
    render(today, yest);
    renderBaseline(today, rates);
    renderGauge(today, rates);
    chartRates = rates; chartNow = today;
    renderChart(today, rates, chartDays);
    renderTip();
    const srcName = j.source === 'naver' ? '네이버 매매기준율' : 'ECB 기준';
    document.getElementById('rateSrc').textContent = srcName + (j.asof ? ' · ' + j.asof : '');
  }catch(e){
    document.getElementById('rateMeta').textContent = '환율을 못 불러와 저장된 값으로 표시해요';
    document.getElementById('rateSrc').textContent = '저장값';
    render(RATE_FALLBACK, null);
  }
}
// 환전 매력도 ⓘ: 지금 선택된 탭(페르소나)의 기준만 설명.
const GAUGE_WHY_TITLE = {
  student: '유학생은 왜 이렇게 볼까?',
  investor: '투자자는 왜 이렇게 볼까?',
  traveler: '여행자는 왜 이렇게 볼까?',
};
const GAUGE_WHY = {
  student: '유학생은 생활비를 매달 반복해서 환전한다. 큰 금액을 한 번에 바꿀 필요 없이 여러 달에 나눠 살 수 있어 당장 급하지 않다. 이번 달은 생활비만 바꾸고, 환율이 더 내려가면 나중에 더 환전하면 된다. 그래서 세 경우 중 가장 길게(장기) 봤을 때 지금이 비싼 시기인지를 더 크게 본다.',
  investor: '투자자는 사고 싶은 주식이 생긴 시점에 맞춰 그때 환전한다. 환율만 보는 게 아니라 주가도 함께 봐야 해서, 마음에 들 때 바로 환전하고 별로면 매수를 미루기도 한다. 오래 환율만 기다리지는 않는다. 그래서 세 경우 중 가장 짧게(단기) 봤을 때 지금이 평소보다 싼지를 더 크게 본다.',
  traveler: '여행자는 출국 날짜가 정해져 있고, 그 전에 정해둔 금액을 꼭 환전해야 한다. 무한정 미룰 수는 없지만, 출국 전까지는 그나마 나은 날을 고를 수 있다. 그래서 유학생과 투자자의 중간(중기) 기간을 기준으로 지금이 살 만한 수준인지를 본다.',
};
// 용어 클릭 -> 뜻 바텀시트. 다른 곳 누르면 닫힘.
document.addEventListener('click', e => {
  const sheet = document.getElementById('defSheet'), bd = document.getElementById('defBackdrop');
  const t = e.target.closest('.term, .impact-info, .about-dog, .gauge-info');
  if(t){
    let term, def;
    if(t.classList.contains('gauge-info')){
      term = GAUGE_WHY_TITLE[activeSet] || GAUGE_WHY_TITLE.student;
      def = GAUGE_WHY[activeSet] || GAUGE_WHY.student;
    } else { term = t.dataset.term; def = t.dataset.def; }
    sheet.querySelector('.def-term').textContent = term;
    sheet.querySelector('.def-text').textContent = def;
    sheet.classList.add('on'); bd.classList.add('on');
    e.stopPropagation();
    return;
  }
  if(e.target.closest('.def-sheet')) return;  // 시트 내부 클릭은 유지
  sheet.classList.remove('on'); bd.classList.remove('on');
});

// 차트 기간 토글 (1주/1개월/3개월).
const _ctabs = document.getElementById('chartTabs');
if(_ctabs) _ctabs.addEventListener('click', e => {
  const b = e.target.closest('.ctab');
  if(!b) return;
  chartDays = +b.dataset.d;
  document.querySelectorAll('#chartTabs .ctab').forEach(t => t.classList.toggle('active', t === b));
  if(chartRates) renderChart(chartNow, chartRates, chartDays);
});

// 품목군 토글: 탭 클릭 시 활성 세트만 바꿔 재렌더(환율 재요청 없음).
document.getElementById('tabs').addEventListener('click', e => {
  const b = e.target.closest('.tab');
  if(!b) return;
  activeSet = b.dataset.set;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === b));
  if(lastDelta != null) renderFeel(lastDelta);
  if(chartRates) renderGauge(chartNow, chartRates);   // 관점 바뀌면 매력도도 재계산
  const tip = document.getElementById('tipBox');       // 여행자(일회성)는 '매달 환전' 팁 숨김
  if(tip) tip.style.display = (activeSet === 'traveler') ? 'none' : '';
});

// 경제 일정 D-Day — 보는 위치와 무관하게 '한국시간(KST) 오늘' 기준으로 고정.
(function(){
  const kstToday = new Date(Date.now() + 9*3600000).toISOString().slice(0,10);  // KST 날짜
  const diff = (a,b) => Math.round((Date.parse(a+'T00:00:00Z') - Date.parse(b+'T00:00:00Z'))/86400000);
  document.querySelectorAll('.cal-row').forEach(row => {
    const el = row.querySelector('.cal-dday'), date = row.dataset.date;
    if(!date || isNaN(Date.parse(date+'T00:00:00Z'))){ el.textContent = ''; return; }
    const days = diff(date, kstToday);
    if(days < 0){ el.textContent = '지남'; el.classList.add('past'); }
    else if(days === 0){ el.textContent = 'D-DAY'; el.classList.add('today'); }
    else { el.textContent = 'D-' + days; }
  });
})();

// 투자자 탭 회전 카드: 4초마다 다음 종목으로(페이드). 다른 탭이면 대기.
function tickRot(){
  if(activeSet !== 'investor') return;
  const cell = document.getElementById('rotCell');
  const item = ITEM_SETS.investor.find(x => x.rot);
  if(!cell || !item) return;
  rotI = (rotI + 1) % item.rot.length;
  const rc = rotCard(item.rot[rotI]);
  cell.style.opacity = '0';
  setTimeout(() => {
    cell.querySelector('.feel-top').innerHTML = rc.top;
    const a = cell.querySelector('.feel-amt');
    a.textContent = rc.amt; a.className = 'feel-amt ' + rc.dir;
    cell.style.opacity = '1';
  }, 220);
}
setInterval(tickRot, 3500);

loadRate();
loadViews();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()

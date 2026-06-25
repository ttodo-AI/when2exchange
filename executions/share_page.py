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
GA_ID = "G-4KP3H2RZEB"  # Google Analytics 4 측정 ID (빈/placeholder면 스크립트 생략)
# Google Form for reader feedback — replace with the real form link once created.
FEEDBACK_URL = "https://forms.gle/a35emZKoRhYQF1N86"

# Instagram 계정 링크 — 실제 핸들 확정되면 교체(마케팅 채팅서 계정 생성 후).
INSTAGRAM_URL = "https://www.instagram.com/when2exchange/"
# Cloudflare Worker 실시간 환율 프록시 URL(worker/rate-proxy.js 배포 후 채우기).
# 비워두면 기존처럼 rate.json(빌드 시점 값)만 사용. 채우면 페이지 열 때마다 라이브로 덮어씀.
RATE_PROXY_URL = "https://when2exchange-rate.gmljw0407.workers.dev/"
# 헤더의 강아지를 누르면 뜨는 자기소개. 자유롭게 교체하세요.
# 세 페르소나 공통 마무리(서버비·공유·피드백). 아래 ABOUT 각 본문 끝에 붙는다.
_ABOUT_CLOSING = (
    "\n\n어차피 제가 쓸 거라 서버 운영비는 계속 나가는데, 혼자만 쓰기 아까워서 슬쩍 "
    "공유해 봅니다! 고환율 시대에 이 페이지가 여러분의 시간도 조금이나마 아껴드렸으면 "
    "좋겠습니다. 피드백 있으면 언제나 환영입니다 🐾"
)
# 페르소나별 "왜 만들었게" — 셋 다 제작자(나)의 실제 경험. 탭 따라 바뀜.
ABOUT = {
    "student": (
        "🐶 멍멍! 제 주인은 매달 환전하느라 머리가 터지는 유학생이랍니다.\n\n"
        "매월 수천 달러씩 환전해야 하는데, 환율이 10원만 올라도 손이 벌벌 떨리더라고요. "
        "'어제 환전 더 해둘걸…' 후회하는 매일을 보내고 있어요. 😢\n\n"
        "매일 아침 눈뜨자마자 환율 앱부터 확인하고 관련 기사 찾다가, 타이밍을 놓쳐서 "
        "나중에 보면 또 많이 올라있고… 시간과 돈을 아끼려고 이 환전 타이밍 분석 페이지를 "
        "직접 만들었답니다!\n\n"
        "환율이 1원 오르면 실제로 나에게 어떤 변화가 있는지를 (내 월세나 커피값으로) "
        "한눈에 보고 싶었거든요." + _ABOUT_CLOSING
    ),
    "investor": (
        "🐶 멍멍! 제 주인은 25년 4월, 브로드컴 3주로 해외주식을 시작했대요.\n\n"
        "그때 환율이 비싸서 '좀 떨어지면 더 환전해야지' 하고 기다렸는데… "
        "환율은 떨어졌지만 주식은 그새 훨씬 크게 올라버려 추가구매를 못했어요. 둘 다 놓친 거죠. 😢\n\n"
        "그때 깨달았대요. 주식 살 땐 환율을 무작정 기다리는 게 아니라, 필요한 시점 근처에서 "
        "'그나마 가장 쌀 때' 빨리 들어가야 한다는 걸요.\n\n"
        "저처럼 환율 때문에 매수 타이밍을 고민하는 분들을 위해 이 페이지를 만들었어요." + _ABOUT_CLOSING
    ),
    "traveler": (
        "🐶 멍멍! 제 주인은 미국에 놀러 오는 친구 때문에 이 부분을 만들었대요.\n\n"
        "'미국 물가 비싸다는데 얼마나 비싼 거야? 지금 환전해도 돼? 지금이 비싼 편이야?' — "
        "여행을 앞두면 이런 걸 짧은 시간에 감 잡기가 어렵잖아요.\n\n"
        "그 친구한테 '지금 환율이 비싼지 싼지, 환전해도 될 타이밍인지'를 한눈에 보여주고 "
        "싶어서 여행자 관점도 넣었어요." + _ABOUT_CLOSING
    ),
}
# 강아지 머리 위 말풍선 (클릭 유도).
DOG_BUBBLE = "왜 만들었개? 🐶"
# Short status badge text per verdict class (color comes from CSS).
BADGE_KR = {"good": "환전 추천", "mid": "지금은 보통", "bad": "환전 비추천"}

from dotenv import load_dotenv

LABEL_KR = {
    "GOOD": ("지금 환전하기 좋아요", "달러가 최근 3개월 중 싼 편이에요", "good", "🟢"),
    "NEUTRAL": ("지금은 보통이에요", "최근 3개월 중 중간 수준이에요", "mid", "🟡"),
    "BAD": ("지금은 환전하기 아까워요", "달러가 최근 3개월 중 비싼 편이에요", "bad", "🔴"),
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
    # 파일명에 타임스탬프(YYYY-MM-DD_HHMM)가 있어 사전식 max = 최신. mtime은 git 체크아웃에서 뒤섞임.
    matches = glob.glob(os.path.join("output", pattern))
    return max(matches) if matches else None


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


def ga_head() -> str:
    # GA4 gtag 스니펫(<head>용). GA_ID가 placeholder/빈 값이면 빈 문자열.
    if not GA_ID or "XXXX" in GA_ID:
        return ""
    return (
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={GA_ID}"></script>'
        '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
        f'gtag("js",new Date());gtag("config","{GA_ID}");</script>'
    )


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
                    '<div class="tldr-meta">📅 __TLDR_DATE__ 기준 · 매일 오전 업데이트</div>'
                    '<div class="tldr-cap">30초 요약</div>'
                    f'<ul class="tldr-list">{items}</ul></div></section>'
                )
            blocks = []
            for i, f in enumerate(fj["factors"], 1):
                bullets = "".join(f"<li>{annotate(esc(strip_cite(b)))}</li>" for b in f.get("bullets", [])[:2])
                links = [s for s in f.get("sources", []) if s.get("link")]
                srcs = "".join(
                    f'<a class="src-chip" href="{esc(s["link"])}" target="_blank" rel="noopener" '
                    f'title="{esc(s.get("title",""))}">{n}</a>'
                    for n, s in enumerate(links, 1)
                )
                src_html = (f'<div class="factor-src"><span class="src-lab">📰 관련 기사</span>{srcs}</div>'
                            if srcs else "")
                impact = int(f.get("impact") or 0)
                gauge = "".join(
                    f'<span class="ig-seg{" on" if k < impact else ""}"></span>'
                    for k in range(5)
                )
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
                    f'<span class="impact" title="오늘 영향도 {impact}/5">{gauge}</span>{info_html}</div>'
                    f'<div class="factor-line">{esc(f.get("headline",""))}</div>'
                    f'<ul class="factor-bullets">{bullets}</ul>'
                    f'{src_html}'
                    '</div>'
                )
            note = '<div class="sec-note impact-note">영향도 막대는 그날 뉴스 분석을 토대로 한 AI 추정이에요.</div>'
            news_html = note + "\n".join(blocks)
            news_title = "오늘 환율을 움직인 요인 Top 4"

    if why_html is None:  # no factor file -> annotate the fallback why
        why_html = annotate(emphasize(why))

    # 환율 영향 일정: 이번주/다음주 그룹, 지난 일정=결과·예정=시나리오. (D-Day는 클라이언트 계산)
    cal_section = ""
    cal_file = find_latest("calendar-*.json")
    if cal_file:
        try:
            cj = json.load(open(cal_file, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cj = None
        if cj and cj.get("events"):
            wd = ["월", "화", "수", "목", "금", "토", "일"]
            kst_today = datetime.now(timezone(timedelta(hours=9))).date()
            this_sun = kst_today + timedelta(days=(6 - kst_today.weekday()))   # 이번 주 일요일

            def _evd(e):
                try:
                    return datetime.fromisoformat(e.get("date", "")).date()
                except (ValueError, TypeError):
                    return None

            def _render_ev(ev):
                ed = _evd(ev)
                disp = f"{ed.month}/{ed.day}({wd[ed.weekday()]})" if ed else ev.get("date", "")
                t = (ev.get("time") or "").strip()
                when = disp + (f" {t}" if t else "")
                stars = "★" * int(ev.get("importance", 1))
                has_result = bool((ev.get("result") or "").strip())   # 지난 일정에 실제 결과가 채워졌나
                res_badge = ('<span class="cal-resbadge"><span class="rb-ico">📊</span>결과</span>'
                             if has_result else '')
                if ed and ed < kst_today:          # 이미 발표 -> 실제 결과
                    res = esc((ev.get("result") or "").strip())
                    detail = (f'<div class="cal-result"><span class="cal-rico">📊</span><span>{res}</span></div>' if res
                              else f'<div class="cal-why">{esc(ev.get("why",""))}</div>')
                else:                              # 예정 -> 왜 + 시나리오
                    scn = "".join(
                        f'<div class="cal-scn"><span class="cal-dot">·</span>'
                        f'<b>{esc(s.get("cond",""))}</b> {esc(s.get("effect",""))}</div>'
                        for s in ev.get("scenarios", []) if s.get("effect")
                    )
                    evwhy = esc(ev.get("why", ""))
                    detail = (f'<div class="cal-why">{evwhy}</div>' if evwhy else "") + scn
                return (
                    f'<details class="cal-item{" has-result" if has_result else ""}">'
                    f'<summary class="cal-row" data-date="{esc(ev.get("date",""))}">'
                    f'<div class="cal-body">'
                    f'<div class="cal-head">{res_badge}<span class="cal-dday">·</span>'
                    f'<span class="cal-date">{esc(when)}</span>'
                    f'<span class="cal-star">{stars}</span></div>'
                    f'<div class="cal-name">{esc(ev.get("name",""))}</div>'
                    f'<div class="cal-impact">{esc(ev.get("summary",""))}</div>'
                    f'</div>'
                    f'<span class="cal-caret">▾</span>'
                    f'</summary><div class="cal-detail">{detail}</div></details>'
                )

            evs = sorted((e for e in cj["events"] if _evd(e)), key=lambda e: e["date"])
            this_week = [e for e in evs if _evd(e) <= this_sun]
            next_week = [e for e in evs if _evd(e) > this_sun]
            groups = ""
            if this_week:
                groups += '<div class="cal-wk">이번주</div>' + "\n".join(_render_ev(e) for e in this_week)
            if next_week:
                groups += ('<details class="cal-nextwk"><summary class="cal-wk cal-wk-toggle">'
                           '<span>다음주 일정</span><span class="cal-caret">▾</span></summary>'
                           + "\n".join(_render_ev(e) for e in next_week) + '</details>')
            guide_raw = (cj.get("guide", "") or "").strip()
            if guide_raw:
                # 메인 한 문장(첫 문장) + 서브 문장은 가운데점 부연으로 분리.
                parts = [p.strip() for p in re.split(r"(?<=다\.)\s+", guide_raw) if p.strip()]
                lead = esc(parts[0]) if parts else ""
                subs = "".join(
                    f'<div class="cg-sub"><span class="cal-dot">·</span>{esc(p)}</div>'
                    for p in parts[1:]
                )
                guide_html = (f'<div class="cal-guide"><div class="cg-lead">'
                              f'<span class="cg-ico">💡</span><span>{lead}</span></div>{subs}</div>')
            else:
                guide_html = ""
            cal_section = (
                '<section><h3 class="sec">환율 영향 일정</h3>'
                + guide_html + groups
                + '</section>'
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
            rate_html, chg_html = "", ""
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
                            chg_html = f'<span class="arc-chg {cc}">{ar}{abs(round(c))}</span>'
                        else:
                            chg_html = '<span class="arc-chg flat">유지</span>'   # 전일과 같음
                    except (TypeError, ValueError):
                        pass
            rows.append(
                f'<a class="arc-row" href="{esc(e["file"])}">'
                f'<span class="arc-date">{esc(disp)}</span>'
                f'<span class="arc-rate">{rate_html}</span>'
                f'<span class="arc-chg-col">{chg_html}</span>'
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
        .replace("__INSTAGRAM_URL__", INSTAGRAM_URL)
        .replace("__RATE_PROXY_URL__", RATE_PROXY_URL)
        .replace("__ABOUT_JSON__", json.dumps(ABOUT, ensure_ascii=False))
        .replace("__BUBBLE__", esc(DOG_BUBBLE))
        .replace("__HEADLINE__", esc(headline))
        .replace("__SUBTITLE__", esc(subtitle))
        .replace("__RATE__", esc(str(rate)))
        .replace("__RATE_NUM__", str(rate_num))
        .replace("__WHY__", why_html)
        .replace("__MONTHLY__", f"{monthly_usd:,.0f}")
        .replace("__MONTHLY_NUM__", str(int(monthly_usd)))
        .replace("__TLDR__", tldr_html)
        .replace("__TLDR_DATE__", esc(gen_disp))
        .replace("__NEWS__", news_html)
        .replace("__NEWS_TITLE__", esc(news_title))
        .replace("__CALENDAR_SECTION__", cal_section)
        .replace("__ARCHIVE_SECTION__", arc_section)
        .replace("__DATE__", esc(gen_disp))
        .replace("__PUBLISHED__", esc(published))
        .replace("__VIEWS_NS__", VIEWS_NS)
        .replace("__GA__", ga_head())
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
    "종전양해각서": "전쟁을 끝내기로 한 합의 문서. 지정학 위험이 줄면 안전자산(달러) 수요가 빠지고 위험자산·신흥국 통화엔 우호적일 수 있어요.",
    "양해각서": "두 나라·기관이 합의 내용을 적어둔 약속 문서(MOU). 법적 강제력은 약하지만 협력 의사를 보여줘요.",
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
                f'{esc(term)}</span>'
            )
            html = html[:idx] + token + html[idx + len(term):]
        for i, span in enumerate(spans):
            html = html.replace(f"\x00{i}\x00", span)
        return html

    return annotate


SHARE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
__GA__
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
        line-height:1.6; -webkit-font-smoothing:antialiased; font-variant-numeric:tabular-nums;
        word-break:keep-all; overflow-wrap:break-word; }   /* 한글 단어 단위 줄바꿈 + 긴 단어만 예외적으로 끊어 넘침 방지 */
  .wrap{ max-width:480px; margin:0 auto; padding:18px 16px 48px; }
  .top{ padding:6px 2px 0; display:flex; align-items:flex-end; gap:6px; }
  .top-text{ flex:1 1 auto; min-width:0; }
  .ey{ font-size:13px; color:var(--muted); font-weight:600; }
  .head{ font-size:17px; font-weight:800; letter-spacing:-.03em; line-height:1.32; margin:8px 0 0; word-break:keep-all; white-space:nowrap; }
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
  .hero-right{ flex:none; display:flex; flex-direction:column; align-items:flex-end; gap:3px; padding-top:2px; text-align:right; }
  .gauge-mini{ font-size:14.5px; font-weight:800; max-width:160px; text-align:right; line-height:1.15; }
  .hero-label{ font-size:12.5px; color:var(--muted); white-space:nowrap; }
  .hero-rate{ font-size:34px; font-weight:800; letter-spacing:-.02em; line-height:1.05; margin:2px 0 0 -2px; white-space:nowrap; }
  .hero-chips{ display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
  .chip{ display:inline-block; font-size:12px; font-weight:700; padding:4px 9px;
         border-radius:8px; background:#f1f2f5; color:var(--muted); }
  .chip.up{ background:var(--up-bg); color:var(--up); }
  .chip.down{ background:var(--down-bg); color:var(--down); }
  .hero-meta{ font-size:11px; color:var(--muted); margin-top:8px; }
  .wknd-note{ margin-top:14px; padding:10px 12px; background:#f3f5f8; border-radius:10px;
              font-size:12px; line-height:1.55; color:#5a6373; }
  .wknd-note b{ color:var(--ink); font-weight:700; }
  .live-dot{ display:inline-block; width:6px; height:6px; border-radius:50%; background:var(--down);
             margin-right:5px; vertical-align:middle; }
  .meta-sep{ margin:0 5px; color:#c8ccd4; }
  .chip.flat{ background:#eceef1; color:var(--muted); }
  .feel-amt.flat{ color:var(--muted); }
  .verdict{ margin-top:16px; padding-top:14px; border-top:1px solid var(--line); }
  .verdict-head{ font-size:16px; font-weight:800; letter-spacing:-.01em; }
  .verdict-sub{ font-size:13px; color:var(--muted); margin-top:2px; }
  .verdict-sub2{ font-size:12.5px; color:var(--muted); margin-top:4px; font-weight:600; }
  .verdict-sub2.up{ color:var(--up); } .verdict-sub2.down{ color:var(--down); }
  .chart-tabs{ display:flex; gap:3px; background:#eceef1; border-radius:9px; padding:3px; margin:10px 0 4px; }
  .ctab{ flex:1; border:none; background:transparent; padding:6px 4px; border-radius:7px;
         font-size:11.5px; font-weight:700; color:var(--muted); cursor:pointer; }
  .ctab.active{ background:var(--card); color:var(--ink); box-shadow:0 1px 3px rgba(20,30,60,.1); }
  .gauge-wrap{ background:var(--card); border:1px solid var(--line); border-radius:14px;
               padding:14px 16px; margin-top:12px; }
  .gauge-top{ display:flex; align-items:baseline; justify-content:space-between; margin-bottom:9px; }
  .gauge-cap{ font-size:13px; font-weight:800; }
  .gauge-label{ font-size:13px; font-weight:800; }
  .gauge-plain{ font-size:12px; font-weight:700; color:var(--muted); }
  .gauge-bar{ position:relative; height:12px; border-radius:6px; margin-top:34px;
              background:linear-gradient(90deg,#1f9d57 0%,#79c267 27%,#d9b441 50%,#e2873a 73%,#e0383e 100%); }
  .gauge-ptr{ position:absolute; top:-5px; width:3px; height:22px; border-radius:2px;
              background:var(--ink); transform:translateX(-50%); transition:left .4s; box-shadow:0 0 0 2px #fff; }
  .gauge-ptr-tag{ position:absolute; bottom:calc(100% + 11px); transform:translateX(-50%); white-space:nowrap;
                  font-size:11px; font-weight:800; color:#fff; background:var(--ink); padding:3px 8px;
                  border-radius:7px; transition:left .4s; }
  .gauge-ptr-tag::after{ content:''; position:absolute; left:50%; top:100%; transform:translateX(-50%);
                         border:4px solid transparent; border-top-color:var(--ink); }
  .gauge-scale{ display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-top:7px; }
  .gauge-basis{ font-size:12px; font-weight:700; color:var(--muted); margin-top:8px; text-align:center; }
  .chart-wrap{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; margin-top:12px; }
  #chart{ position:relative; height:140px; margin-top:0; touch-action:none; cursor:pointer;
          user-select:none; -webkit-user-select:none; -webkit-tap-highlight-color:transparent; }
  .cross-line{ position:absolute; top:0; bottom:0; width:1px; background:#6b7280; transform:translateX(-50%); pointer-events:none; }
  .cross-dot{ position:absolute; width:9px; height:9px; border-radius:50%; background:var(--brand);
              border:2px solid #fff; transform:translate(-50%,-50%); pointer-events:none; box-shadow:0 0 0 4px rgba(59,91,219,.15); }
  .cross-lab{ position:absolute; top:0; white-space:nowrap; background:var(--ink); color:#fff;
              font-size:11px; font-weight:800; padding:3px 7px; border-radius:7px; pointer-events:none; }
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
  .cap-delta{ font-weight:800; }
  .cap-delta.up{ color:var(--up); } .cap-delta.down{ color:var(--down); }
  .tldr{ background:var(--brand-bg); border-radius:12px; padding:13px 16px; margin-top:12px; }
  .tldr-meta{ font-size:11px; color:var(--muted); font-weight:600; margin-bottom:6px; }
.tldr-cap{ font-size:11.5px; font-weight:800; color:var(--brand); letter-spacing:.02em; margin-bottom:5px; }
  .tldr-list{ margin:0; padding-left:17px; }
  .tldr-list li{ font-size:13.5px; line-height:1.68; margin:6px 0; color:#2b313d; }
  .impact{ margin-left:auto; display:inline-flex; gap:2px; align-items:center; }
  .ig-seg{ width:5px; height:11px; border-radius:1.5px; background:#e3e6ec; }
  .ig-seg.on{ background:var(--brand); }
  .impact-info, .gauge-info{ flex:none; margin-left:6px; width:18px; height:18px; border:none; border-radius:50%;
                background:#eceef1; color:var(--muted); font-size:12px; cursor:pointer; vertical-align:middle; padding:0;
                display:inline-flex; align-items:center; justify-content:center; }
  .impact-info:active, .gauge-info:active{ background:#dfe2e7; }
  .sec-note{ font-size:11.5px; color:var(--muted); margin:-2px 2px 10px; }
  .impact-note{ margin:12px 2px 8px; line-height:1.4; }
  .cal-guide{ background:var(--brand-bg); border-radius:13px; padding:15px 17px; margin-bottom:16px;
              font-size:13px; line-height:1.64; color:#2b313d; word-break:keep-all; letter-spacing:-.01em; }
  .cg-lead{ display:flex; gap:6px; font-weight:700; }
  .cg-ico{ flex:none; }                                  /* 💡는 매달고 텍스트는 한 칼럼으로 왼쪽 정렬 */
  .cg-sub{ margin-top:7px; padding-left:13px; text-indent:-13px; color:#41485a; }
  .cal-item{ background:#f6f7f9; border:1px solid var(--line); border-radius:13px; margin-bottom:13px;
             transition:border-color .15s, background .15s, opacity .15s; }
  .cal-item:not([open]):hover{ border-color:#cdd2da; background:#f1f3f6; }
  .cal-item:not([open]):active{ background:#e9ecf1; }
  .cal-item[open]{ background:#fff; margin-bottom:9px; }   /* 펼치면 카드 간격 살짝 콤팩트(스크롤 피로↓) */
  .cal-item.past:not([open]):not(.has-result){ opacity:.6; }   /* 결과 없는 과거: 많이 흐리게 */
  .cal-item.past.has-result:not([open]){ opacity:.82; }         /* 결과 있는 과거: 살짝만 가라앉힘(배지로 클릭 유도) */
  .cal-row{ display:flex; align-items:center; gap:10px; padding:14px 15px; cursor:pointer; list-style:none; }
  .cal-row::-webkit-details-marker{ display:none; }
  .cal-body{ flex:1; min-width:0; }
  .cal-head{ display:flex; align-items:center; gap:7px; margin-bottom:7px; }
  .cal-dday{ flex:none; display:inline-flex; align-items:center; justify-content:center; min-width:46px;
             height:21px; line-height:1; font-size:11px; font-weight:800;
             color:var(--brand); background:var(--brand-bg); border-radius:7px; padding:0 6px; }
  .cal-dday.today{ color:#fff; background:var(--brand); }
  .cal-resbadge{ flex:none; display:inline-flex; align-items:center; gap:4px; height:21px;
                 font-size:11px; font-weight:800; color:var(--brand); background:var(--brand-bg);
                 border-radius:7px; padding:0 8px; }
  .rb-ico{ font-size:.92em; line-height:1; }
  .cal-date{ font-size:12px; color:var(--muted); white-space:nowrap; line-height:21px; }
  .cal-caret{ flex:none; width:22px; height:22px; border-radius:50%;
              display:inline-flex; align-items:center; justify-content:center; font-size:11px;
              color:var(--muted); background:#eceef1; transition:transform .2s, background .2s; }
  .cal-item[open] .cal-caret{ transform:rotate(180deg); color:#fff; background:var(--brand); }
  .cal-name{ font-size:13.5px; font-weight:700; letter-spacing:-.02em; margin-bottom:5px;
             word-break:keep-all; }
  .cal-star{ color:#d99a0b; font-size:12.5px; letter-spacing:1px; }
  .cal-impact{ font-size:12.5px; color:#41485a; line-height:1.5; }
  .cal-detail{ padding:2px 14px 13px; }
  .cal-why{ font-size:13px; color:#2b313d; line-height:1.62; }
  .cal-scn{ font-size:12.5px; color:#41485a; margin-top:6px; line-height:1.55;
            padding-left:13px; text-indent:-13px; }
  .cal-dot{ color:var(--brand); font-weight:800; margin-right:5px; }
  .cal-result{ display:flex; gap:6px; font-size:13px; color:#2b313d; line-height:1.6; }
  .cal-rico{ flex:none; }                                /* 📊는 매달고 결과 텍스트는 한 칼럼으로(줄바꿈 정렬) */
  .cal-wk{ font-size:14.5px; font-weight:800; color:var(--ink); margin:22px 0 9px; letter-spacing:-.01em; }
  .cal-wk:first-of-type{ margin-top:4px; }
  .cal-wk-toggle{ cursor:pointer; list-style:none; display:flex; align-items:center; line-height:1;
                  justify-content:space-between; gap:8px; background:#fff; border:1px solid var(--line);
                  border-radius:12px; padding:15px 16px; margin:8px 0 13px; transition:background .15s, border-color .15s; }
  .cal-wk-toggle::-webkit-details-marker{ display:none; }
  .cal-wk-toggle:hover{ background:#f6f8fb; border-color:#cdd6ea; }
  .cal-wk-toggle:active{ background:#eef2f8; }
  .cal-wk-toggle .cal-caret{ color:var(--brand); background:var(--brand-bg); }   /* 닫혔을 때도 블루(클릭 유도) */
  .cal-nextwk[open] > .cal-wk-toggle{ margin-bottom:9px; }
  .cal-nextwk[open] > .cal-wk-toggle .cal-caret{ transform:rotate(180deg); color:#fff; background:var(--brand); }
  .cal-scn b{ color:var(--ink); }
  .arc-row{ display:flex; align-items:center; gap:9px; padding:9px 0; border-top:1px solid var(--line);
            text-decoration:none; color:inherit; }
  .arc-row:first-of-type{ border-top:none; }
  .arc-date{ flex:none; font-size:12.5px; font-weight:700; color:var(--muted); min-width:38px; }
  .arc-rate{ flex:none; font-size:12.5px; font-weight:800; min-width:56px; text-align:right; }
  .arc-chg-col{ flex:none; min-width:44px; }
  .arc-chg{ font-weight:700; font-size:11px; }
  .arc-chg.up{ color:var(--up); } .arc-chg.down{ color:var(--down); } .arc-chg.flat{ color:var(--muted); }
  .arc-head{ flex:1; min-width:0; font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sec{ font-size:14px; font-weight:800; margin:26px 2px 11px; letter-spacing:-.01em;
        display:flex; align-items:center; gap:7px; }
  .sec::before{ content:""; width:3px; height:14px; border-radius:2px; background:var(--brand); }
  .persona{ margin-top:14px; }
  .persona-q{ font-size:12.5px; color:var(--ink); font-weight:700; margin:0 2px 6px; }
  .persona-q-sub{ font-size:11.5px; font-weight:600; color:var(--brand); }
  .tabs{ display:flex; gap:3px; background:#eceef1; border-radius:10px; padding:3px; margin-bottom:10px; }
  .tab{ flex:1; display:flex; align-items:center; justify-content:center; border:none;
        background:transparent; padding:10px 6px; border-radius:8px;
        font-size:13px; font-weight:700; color:var(--muted); cursor:pointer; white-space:nowrap; }
  .tab.active{ background:var(--card); color:var(--ink); box-shadow:0 1px 3px rgba(20,30,60,.12); }
  .verdict-now{ display:flex; align-items:baseline; gap:8px; margin:12px 2px 0; flex-wrap:wrap; }
  .vn-text{ font-size:20px; font-weight:800; letter-spacing:-.02em; line-height:1.2; }
  .vn-tag{ font-size:12px; font-weight:800; color:var(--brand); background:var(--brand-bg); padding:3px 9px; border-radius:999px; }
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
  .feel-tot{ white-space:nowrap; }
  .feel-chg{ font-size:11px; font-weight:600; white-space:nowrap; }
  .feel-chg.up{ color:var(--up); } .feel-chg.down{ color:var(--down); }
  .prose{ font-size:14.5px; line-height:1.72; color:#2b313d; margin:0;
          word-break:keep-all; text-wrap:pretty; }
  .prose .hl{ font-weight:700; text-decoration:underline; text-underline-offset:3px;
              text-decoration-thickness:1.5px; text-decoration-color:rgba(59,91,219,.55); }
  .term{ border-bottom:1.5px dashed var(--brand); padding-bottom:.5px; cursor:pointer; white-space:nowrap; }
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
  .tip{ background:var(--brand-bg); border-radius:13px; padding:17px 16px 18px; margin-top:11px; }
  .th-label{ font-size:12px; color:var(--muted); font-weight:700; }
  .th-big{ font-size:25px; font-weight:800; letter-spacing:-.02em; margin:4px 0 7px; line-height:1.18; }
  .th-big.save{ color:var(--down); } .th-big.cost{ color:var(--up); }
  .th-tail{ font-size:.76em; font-weight:700; }                 /* 서술어는 숫자의 76%로 완급 */
  .th-sub{ font-size:12px; color:#9aa0ab; line-height:1.5; }
  .th-ref{ color:#9aa0ab; }                                     /* 기준값=연한 회색(서브) */
  .th-today{ color:var(--ink); font-weight:700; }               /* 오늘=진하게 */
  .th-sep{ margin:0 6px; color:#c8ccd4; }
  .th-delta{ font-weight:700; margin-left:3px; }
  .th-delta.up{ color:var(--up); } .th-delta.down{ color:var(--down); }
  .tip-foot{ font-size:12.5px; color:#41485a; margin-top:12px; line-height:1.5; }
  .factor{ padding:14px 0; border-top:1px solid var(--line); }
  .factor:first-of-type{ border-top:none; padding-top:2px; }
  .factor-head{ display:flex; align-items:center; gap:8px; font-size:14.5px; font-weight:800; }
  .rank{ flex:none; width:20px; height:20px; border-radius:6px; background:var(--brand); color:#fff;
         font-size:12px; font-weight:800; display:flex; align-items:center; justify-content:center; }
  .factor-name{ flex:1; min-width:0; }                 /* 영향도+ⓘ를 우측으로 칼정렬 */
  .factor-line{ font-size:13px; color:var(--brand); margin:7px 0 9px; font-weight:700;
                line-height:1.5; word-break:keep-all; }
  .factor-bullets{ margin:0; padding-left:17px; }
  .factor-bullets li{ font-size:13.5px; line-height:1.62; margin:7px 0; color:#2b313d; }
  .factor-src{ margin-top:11px; display:flex; align-items:center; gap:7px; flex-wrap:wrap; }
  .src-lab{ font-size:11.5px; color:var(--muted); font-weight:700; }
  .src-chip{ display:inline-flex; align-items:center; justify-content:center; min-width:32px; height:31px;
             padding:0 9px; border:1px solid var(--line); border-radius:999px; background:#f5f7fa;
             color:#41485a; font-size:12.5px; font-weight:700; text-decoration:none; }
  .src-chip:active{ background:#e9edf2; }
  .actions{ display:flex; gap:8px; margin-top:8px; }
  .btn{ flex:1; display:flex; align-items:center; justify-content:center; padding:13px; border-radius:12px;
        font-size:14px; font-weight:800; cursor:pointer; border:1px solid var(--line); text-decoration:none; }
  .btn-primary{ background:var(--brand); color:#fff; border-color:var(--brand); }
  .btn-secondary{ background:var(--card); color:var(--ink); }
  .btn-insta{ display:flex; align-items:center; justify-content:center; gap:8px; width:100%; margin-top:26px;
        padding:13px; border-radius:12px; font-size:14px; font-weight:800; color:#fff; text-decoration:none;
        border:none; background:linear-gradient(95deg,#7c3aed,#d6249f 45%,#fd5949 72%,#fdc468); }
  .btn-insta svg{ flex:none; }
  footer{ text-align:center; color:var(--muted); font-size:11.5px; margin-top:22px; line-height:1.8; letter-spacing:.01em; }
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
    <button class="about-dog" data-term="만든 사람" aria-label="만든 사람 소개" title="눌러서 소개 보기">
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
    <div class="persona-q">내 상황 고르면 딱 맞춰드릴개요 🐶</div>
    <div class="tabs" id="tabs">
      <button class="tab active" data-set="student">🎓 유학생</button>
      <button class="tab" data-set="investor">📈 투자자</button>
      <button class="tab" data-set="traveler">✈️ 여행자</button>
    </div>
  </div>

  <div class="verdict-now" id="verdictNow" style="display:none">
    <span class="vn-text" id="vnText"></span>
    <span class="vn-tag" id="vnTag"></span>
  </div>

  <section class="hero">
    <div class="hero-row">
      <div class="hero-left">
        <div class="hero-label">지금 원·달러 환율</div>
        <div class="hero-rate" id="rateNow">__RATE__</div>
      </div>
      <div class="hero-right">
        <span id="rateDelta" class="chip"></span>
        <div id="rateMeta" class="hero-meta">실시간 …</div>
      </div>
    </div>
    <div class="wknd-note" id="wkndNote" style="display:none">
      📅 주말엔 외환시장이 쉬어 환율이 <b>금요일 종가</b>에서 멈춰 있어요. 새 거래가 없어 값이 안 바뀌고, <b>월요일 장이 열리면</b> 다시 움직여요.
    </div>
  </section>

  __TLDR__

  <section id="feelBox">
    <h3 class="sec">어제보다 이만큼 더 들어요</h3>
    <div id="feelList" class="feel-grid"><p class="muted">실시간 환율로 계산 중…</p></div>
  </section>

  <section class="gauge-wrap" id="gaugeWrap" style="display:none">
    <div class="gauge-top"><span class="gauge-cap">환전 매력도 <button class="gauge-info" id="gaugeInfo" aria-label="페르소나별 기준 설명">i</button></span><span id="gaugeLabel" class="gauge-label"></span></div>
    <div class="gauge-bar"><span class="gauge-ptr-tag" id="gaugePtrTag"></span><span class="gauge-ptr" id="gaugePtr"></span></div>
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

  <section id="tipBox">
    <h3 class="sec" id="tipTitle">매달 환전한다면</h3>
    <p class="prose" id="tipAdvice">한 번에 몰아 사기보다 나눠 사면, 환율이 출렁여도 평균 단가로 살 수 있어요.</p>
    <div class="tip">
      <div class="tip-hero" id="tipHero"></div>
    </div>
  </section>

  <section>
    <h3 class="sec">__NEWS_TITLE__</h3>
    __NEWS__
  </section>

  __ARCHIVE_SECTION__

  <a class="btn-insta" id="instaBtn" href="__INSTAGRAM_URL__" target="_blank" rel="noopener">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="5.5"/><circle cx="12" cy="12" r="4.1"/><circle cx="17.3" cy="6.7" r="1.1" fill="#fff" stroke="none"/></svg>
    매일 환율, 인스타로 받기
  </a>
  <div class="actions">
    <button id="shareBtn" class="btn btn-primary" onclick="sharePage(this)">친구에게 공유하기</button>
    <a class="btn btn-secondary" href="__FEEDBACK_URL__" target="_blank" rel="noopener">피드백 남기기</a>
  </div>

  <footer>
    <div class="views" id="views">조회 오늘 <b id="vToday">–</b> · 누적 <b id="vTotal">–</b></div>
    발행 __PUBLISHED__<br>환율 <span id="rateSrc">ECB</span><br>
    표시 환율은 시장 기준이라, 실제 환전가는 은행·앱 수수료로 조금 더 비싸요.<br>
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
const RATE_PROXY_URL = "__RATE_PROXY_URL__";  // Cloudflare Worker(실시간). 비면 rate.json만 사용
let rateAsof = '', rateLive = false;    // 화면 라벨용: 갱신 시각 / 라이브 여부
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
        { ico:'🚀', name:'스페이스X', tk:'SPCX', usd:300 },
        { ico:'🎮', name:'엔비디아', tk:'NVDA',  usd:180 },
        { ico:'🚗', name:'테슬라',  tk:'TSLA',  usd:400 },
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
// 농담 라벨 옆에 붙는 평이한 뜻(5단계 공통). 재미는 살리되 의미를 바로 이해하게.
const GAUGE_PLAIN = ['많이 싼 편', '싼 편', '중간', '비싼 편', '많이 비싼 편'];
let activeSet = 'student';
let lastDelta = null;   // 어제 대비 정수 편차 기억(탭 전환 시 재렌더용)
let curRate = null;     // 현재 환율(여행자 탭 실제 금액 계산용)
let avg1w = null;       // 최근 7영업일 평균(투자자 탭 손해/이득 기준)
let stockPx = {};       // 종목 전일 종가 맵 {TSLA:403, ...}
let rotI = 0;           // 회전 종목 인덱스
let chartRates = null, chartNow = null, chartDays = 63;  // 차트 기간 토글용(영업일 점 수)
let CG = null;  // 차트 탭 스크럽용 기하정보(점 좌표 역산)
const won = n => Math.round(n).toLocaleString('ko-KR');
// KST 기준 주말(토·일)이면 true. 주말엔 외환시장 휴장 -> 환율이 금요일 값에서 멈춤.
function isKstWeekend(){ const d = new Date(Date.now() + 9*3600000).getUTCDay(); return d === 0 || d === 6; }

function render(rateNow, rateYest){
  const elNow = document.getElementById('rateNow');
  const elDelta = document.getElementById('rateDelta');
  const elMeta = document.getElementById('rateMeta');
  const elBox = document.getElementById('feelBox');

  elNow.textContent = won(rateNow) + '원';

  if(rateYest == null){            // no day-over-day data: show rate only
    elDelta.textContent = '';
    elMeta.textContent = '';
    elBox.style.display = 'none';
    return;
  }
  const dr = Math.round(rateNow - rateYest);   // +면 달러 비싸짐(원화 약세)
  const up = dr > 0, flat = (dr === 0);
  elDelta.textContent = flat ? '어제와 비슷' : (up?'▲':'▼') + won(Math.abs(dr)) + '원';  // '어제' 빼고 ▲N원
  elDelta.className = 'chip ' + (flat ? 'flat' : (up?'up':'down'));
  const head = rateLive ? '실시간' : (rateAsof ? rateAsof + ' 기준' : '저장된 값');
  const dot = rateLive ? '<span class="live-dot"></span>' : '';
  elMeta.innerHTML = dot + head + '<span class="meta-sep">·</span>어제 ' + won(rateYest) + '원';

  // 주말: 외환시장 휴장 -> 금요일 값 고정. 칩·메타를 바꾸고 아래에 '이유' 안내 노출.
  const wknd = isKstWeekend();
  const wn = document.getElementById('wkndNote');
  if(wn) wn.style.display = wknd ? 'block' : 'none';
  if(wknd){
    elDelta.textContent = '주말 휴장'; elDelta.className = 'chip flat';
    elMeta.innerHTML = '금요일 종가 기준';
  }

  curRate = rateNow;
  lastDelta = dr;
  renderFeel(dr);   // 제목·셀은 renderFeel이 페르소나별로 처리
}

// 상황 진단(공통, 사실): 수준 × 추세. 모든 페르소나 동일.
const DIAG_LVL = ['최근 3개월 중 비싼 편', '최근 3개월 중 중간 수준', '최근 3개월 중 싼 편']; // lvl 0/1/2
const DIAG_TREND = ['이고, 오르는 중이에요.', '이고, 큰 움직임은 없어요.', '이고, 내려오는 중이에요.']; // trend 0/1/2
const DIAG_HIGH = '지금은 최근 3개월 중 가장 비싼 수준이에요.';
// 행동 조언(페르소나별): [비쌈, 보통, 쌈]. 같은 상황이라도 누구냐에 따라 다르게.
const ACT = {
  student: [
    '급하지 않으니 이번 달 쓸 만큼만 바꾸고, 더 내려가면 그때 더 환전하세요.',
    '하던 대로 매달 조금씩 나눠 바꾸면 충분해요.',
    '쌀 때라 이번 달 것에 더해 조금 더 당겨 담아둬도 좋아요.',
  ],
  investor: [
    '환율만 보고 무한정 기다리지 마세요. 살 종목이 정해졌다면 한 번에 말고 나눠서 환전·매수하세요.',
    '매수 시점에 맞춰 나눠 환전하면 환율 부담을 줄일 수 있어요.',
    '환율도 싼 편이라 환전과 매수 타이밍을 같이 잡기 좋아요.',
  ],
  traveler: [
    '출국까지 여유가 있으면 며칠 더 보고 나은 날에 나눠 사고, 임박했으면 그냥 나눠 사세요.',
    '출국 전까지 한 번에 말고 몇 번 나눠 바꾸면 평균 단가가 안정돼요.',
    '싼 편이라 출국에 쓸 돈을 지금 미리 환전해둬도 좋아요.',
  ],
};
const ACT_HIGH = {
  student: '지금 큰돈을 한 번에 고정하면 위험해요. 이번 달 꼭 필요한 만큼만, 나머진 나눠서요.',
  investor: '환율이 정점이라 큰 금액을 한 번에 고정하긴 부담돼요. 환전도 매수도 나눠서 들어가세요.',
  traveler: '환율이 정점이라 한 번에 말고 나눠서, 출국 전 더 나은 날을 노려보세요.',
};
const TIP_TITLE = { student: '매달 환전한다면', investor: '주식 살 돈을 환전한다면', traveler: '여행 갈 돈을 환전한다면' };
const heroHTML = r => `<div class="th-label">${r.label}</div><div class="th-big ${r.dir}">${r.big}</div><div class="th-sub">${r.sub}</div>`;
// 서브 한 줄: 기준값(연회색) · 오늘(진하게) + 오늘이 기준보다 얼마 비싼/싼지 미니 델타.
function subLine(refLabel, ref, today, todayLabel, noDelta){
  // noDelta: 큰 글자가 이미 환율차(▲N원)를 보여주는 경우(calcC) 서브 델타 생략(중복 방지).
  const d = today - ref, a = Math.abs(d);
  const arrow = d > 0 ? '▲' : (d < 0 ? '▼' : '');
  const cls = d > 0 ? 'up' : (d < 0 ? 'down' : '');
  const delta = (!noDelta && a >= 1) ? ` <span class="th-delta ${cls}">(${arrow}${won(a)}원)</span>` : '';
  return `${refLabel} <span class="th-ref">${won(ref)}원</span><span class="th-sep">·</span>`
       + `${todayLabel || '오늘'} <b class="th-today">${won(today)}원</b>${delta}`;
}
function calcA(cl, n){  // 매주 $250×4 분할 vs 오늘 $1,000 일괄
  const weekly = [curRate]; [5,10,15].forEach(o => { const i = n-1-o; if(i>=0) weekly.push(cl[i]); });
  if(weekly.length < 2) return null;
  const avg = Math.round(weekly.reduce((a,b)=>a+b,0)/weekly.length), today = Math.round(curRate);
  const d = today - avg, amt = Math.round(Math.abs(d)*1000);
  let big, dir;
  if(amt < 100){ big = '거의 같았어요'; dir = ''; }
  else if(d > 0){ big = won(amt)+'원<span class="th-tail"> 아꼈어요</span>'; dir = 'save'; }
  else { big = won(amt)+'원<span class="th-tail"> 더 들었어요</span>'; dir = 'cost'; }
  return { label:'매주 $250씩 4주 나눠 샀다면', big, dir, sub:subLine('나눠 사기 평균', avg, today, '오늘 한 번에') };
}
function calcAvg(v, refLabel){  // 오늘 vs (전달받은 기간) 평균. $1,000 환전 시 '원' 차이.
  if(v.length < 5) return null;
  const avg = Math.round(v.reduce((a,b)=>a+b,0)/v.length), today = Math.round(curRate);
  const d = today - avg, amt = Math.round(Math.abs(d)*1000);
  let big, dir;
  if(amt < 100){ big = '평균과 비슷해요'; dir = ''; }
  else if(d > 0){ big = won(amt)+'원<span class="th-tail"> 더 들어요</span>'; dir = 'cost'; }
  else { big = won(amt)+'원<span class="th-tail"> 덜 들어요</span>'; dir = 'save'; }
  return { label:refLabel+' 환율로 $1,000 바꿀 때보다', big, dir, sub:subLine(refLabel, avg, today) };
}
function renderTip(){
  // 상황 진단(공통) + 행동 조언(페르소나별) + 페르소나 시간축에 맞는 예시 계산. 전부 실측.
  const adv = document.getElementById('tipAdvice'), hero = document.getElementById('tipHero');
  if(!adv || !hero || !chartRates || !curRate) return;
  const cl = Object.keys(chartRates).sort().map(k => chartRates[k].KRW), n = cl.length;
  if(n < 5) return;
  const v63 = cl.slice(-63), v22 = cl.slice(-22);
  const p = v63.filter(x => x <= curRate).length / v63.length;   // 3개월 백분위
  // 추세: 최근(3거래일)과 2주가 '둘 다 같은 방향'일 때만 상승/하락. 엇갈리면 횡보.
  const ref3 = (n > 3) ? cl[n-1-3] : cl[0];
  const ref2w = (n > 10) ? cl[n-1-10] : cl[0];
  const sUp = curRate > ref3*1.005, sDn = curRate < ref3*0.995;
  const mUp = curRate > ref2w*1.005, mDn = curRate < ref2w*0.995;
  const trend = (sUp && mUp) ? 0 : (sDn && mDn) ? 2 : 1;          // 0상승 1횡보 2하락
  const atHigh = curRate >= Math.max(...v63) - 0.3;
  const lvl = p >= 0.66 ? 0 : (p <= 0.34 ? 2 : 1);               // 0비쌈 1보통 2쌈
  const set = ACT[activeSet] ? activeSet : 'student';

  // 진단(공통) + 행동(페르소나)
  let diag, act;
  if(atHigh && trend === 0){ diag = DIAG_HIGH; act = ACT_HIGH[set]; }
  else { diag = '지금은 ' + DIAG_LVL[lvl] + DIAG_TREND[trend]; act = ACT[set][lvl]; }
  adv.textContent = diag + ' ' + act;

  // 제목(페르소나)
  const ttl = document.getElementById('tipTitle');
  if(ttl) ttl.textContent = TIP_TITLE[set] || TIP_TITLE.student;

  // 예시 계산(페르소나 시간축, 모두 $1,000 기준 '원' 차이):
  // 유학생=최근 3개월 평균(장기) / 여행자=한 달 평균(중기) / 투자자=분할 vs 일괄(단기)
  let r;
  if(set === 'student') r = calcAvg(v63, '최근 3개월 평균');
  else if(set === 'traveler') r = calcAvg(v22, '한 달 평균');
  else r = calcA(cl, n);
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
      const total = '<span class="feel-tot">' + won(Math.round((curRate||0) * it.usd)) + '원</span>';
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
  track('share_click');
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
  const labels = w.labels || ['최근 3개월 중 낮음','약간 낮음','중간 수준','약간 높음','최근 3개월 중 높음'];
  const colors = ['#1f9d57','#79c267','#b58900','#e2873a','#e0383e'];
  // 백분위(pct)를 게이지 위치(disp)로 비선형 보간: 가운데(연초록~주황)를 넓게,
  // 빨강/찐초록은 양 끝 극단(상·하위 10%)에서만 나오도록. 바 크기는 그대로 균등.
  const remap = p => { const xs=[0,0.10,0.30,0.70,0.90,1], ys=[0,0.20,0.40,0.60,0.80,1];
    for(let i=1;i<xs.length;i++){ if(p<=xs[i]){ const t=(p-xs[i-1])/(xs[i]-xs[i-1]); return ys[i-1]+t*(ys[i]-ys[i-1]); } } return 1; };
  const disp = remap(pct);
  const zone = Math.min(4, Math.floor(disp*5));
  const lab = document.getElementById('gaugeLabel');
  lab.textContent = labels[zone];                       // 헤더엔 농담 라벨만
  lab.style.color = colors[zone];
  document.getElementById('gaugePtr').style.left = (disp*100).toFixed(1) + '%';
  const ptag = document.getElementById('gaugePtrTag'); // 평이한 뜻(중간/비싼 편…)은 바늘 위 배지로
  if(ptag){ ptag.textContent = GAUGE_PLAIN[zone]; ptag.style.left = (disp*100).toFixed(1) + '%'; }
  wrap.style.display = 'block';

  // 판정(헤드라인 답): 게이지와 '같은 zone'에서 도출 -> 실시간·일관·설명가능.
  const vn = document.getElementById('verdictNow');
  if(vn){
    const vIdx = zone <= 1 ? 0 : (zone === 2 ? 1 : 2);   // 0좋음 1보통 2아까움
    const VTXT = ['🟢 지금 환전하기 좋아요', '🟡 지금은 보통이에요', '🔴 지금은 환전이 아까워요'];
    const VCOL = ['var(--good-fg)', 'var(--mid-fg)', 'var(--bad-fg)'];
    const NM = { student:'유학생', investor:'투자자', traveler:'여행자' };
    const vt = document.getElementById('vnText'), vtag = document.getElementById('vnTag');
    vt.textContent = VTXT[vIdx]; vt.style.color = VCOL[vIdx];
    vtag.textContent = (NM[activeSet] || '유학생') + ' 기준';
    vn.style.display = 'flex';
  }
}

function renderChart(rateNow, rates, days){
  // 선택 기간 라인 + 평균선, 평균 위(비쌈)=빨강/아래(쌈)=초록 구간, 현재 점.
  days = days || 63;
  const keys = Object.keys(rates).sort().slice(-days);  // 최근 N 영업일(점)
  if(keys.length < 2) return;
  const vals = keys.map(k => rates[k].KRW);
  const min = Math.min(...vals), max = Math.max(...vals), span = (max-min) || 1;
  // 데이터는 가운데 띠[yTop, yTop+yBand]에만 -> 위/아래 여백에 라벨이 들어가 선을 안 가림.
  const W = 320, H = 96, padL = 8, padR = 30, yTop = 26, yBand = 54;  // 상단 여백↑(최고점 숫자 숨통)
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
  if(iNow!==iHi && iNow!==iLo) marks += mk(iNow,vals[iNow],'mk-now','');  // 점은 선 끝(마지막 종가)에 붙임
  marks += `<div class="mk mk-avg" style="right:1%;top:${(avgY/H*100).toFixed(1)}%"><span class="mk-lab">평균 ${won(avg)}</span></div>`;
  document.getElementById('chart').innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="chart-svg">
      <rect x="0" y="0" width="${W}" height="${avgY.toFixed(1)}" fill="var(--up-bg)"/>
      <rect x="0" y="${avgY.toFixed(1)}" width="${W}" height="${(H-avgY).toFixed(1)}" fill="var(--down-bg)"/>
      <line x1="0" y1="${avgY.toFixed(1)}" x2="${W}" y2="${avgY.toFixed(1)}" stroke="#9aa3ad" stroke-width="1" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>
      <path d="${line}" fill="none" stroke="var(--ink)" stroke-width="2" vector-effect="non-scaling-stroke"/>
    </svg>` + marks
    + `<div class="cross" id="chartCross" style="display:none"><div class="cross-line"></div><div class="cross-dot"></div><div class="cross-lab"></div></div>`;
  CG = { keys, vals, W, H, padL, padR, yTop, yBand, min, span };
  const lab = document.getElementById('chartNow');
  lab.textContent = above ? '평균 위 (비쌈)' : '평균 아래 (쌈)';
  lab.style.color = above ? 'var(--up)' : 'var(--down)';
  const dr = Math.round(rateNow - avg);
  document.getElementById('chartCap').innerHTML =
    dr === 0 ? '지금은 이 기간 평균과 거의 같아요'
      : `지금은 이 기간 평균보다 <span class="cap-delta ${dr>0?'up':'down'}">${dr>0?'▲':'▼'} ${won(Math.abs(dr))}원</span> ${dr>0?'비싼':'싼'} 편이에요`;
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

function shortAsof(s){
  // "2026-06-12 14:58 KST" -> "오늘 14:58" (시각 없으면 '')
  const m = s ? String(s).match(/(\d{1,2}:\d{2})/) : null;
  return m ? '오늘 ' + m[1] : '';
}
async function loadRate(){
  // 1) 정적 rate.json: 과거 시계열·종목·폴백값(빌드 2회/일).
  let rates = {}, today = RATE_FALLBACK, yest = null, srcName = 'ECB 기준';
  try{
    const r = await fetch('./rate.json', {cache:'no-store'});
    if(!r.ok) throw new Error('http '+r.status);
    const j = await r.json();
    (j.series||[]).forEach(p => { if(p && p.date) rates[p.date] = {KRW: p.close}; });
    today = j.rate; yest = (j.prev != null ? j.prev : null);
    if(j.stocks){   // 전일 종가($): SPY는 고정 카드, 나머지는 회전 카드가 stockPx에서 조회.
      stockPx = j.stocks;
      ITEM_SETS.investor.forEach(it => { if(it.stock && j.stocks[it.stock]) it.usd = j.stocks[it.stock]; });
    }
    srcName = j.source === 'naver' ? '네이버 매매기준율' : 'ECB 기준';
    rateAsof = shortAsof(j.asof); rateLive = false;
  }catch(e){
    document.getElementById('rateMeta').textContent = '환율을 못 불러와 저장된 값으로 표시해요';
    document.getElementById('rateSrc').textContent = '저장값';
    render(RATE_FALLBACK, null);
    return;
  }
  // 2) Worker 라이브 환율: 있으면 '현재값'만 덮어씀. 실패하면 위 정적값 그대로.
  if(RATE_PROXY_URL){
    try{
      const lr = await fetch(RATE_PROXY_URL, {cache:'no-store'});
      if(lr.ok){
        const lj = await lr.json();
        if(lj && lj.rate){
          today = lj.rate;
          if(lj.prev != null) yest = lj.prev;
          rateLive = true; rateAsof = shortAsof(lj.asof);
          if(lj.source === 'naver') srcName = '네이버 매매기준율';
        }
      }
    }catch(e){ /* 라이브 실패 → 정적값 유지 */ }
  }
  // 차트·게이지·크로스헤어의 '오늘' 점을 빌드시점 종가가 아니라 현재 라이브값(today)으로 덮는다
  // (series 마지막 날짜 = 오늘). 이래야 그래프의 현재 점이 상단 박스 현재값과 일치한다.
  { const _sk = Object.keys(rates).sort();
    if(_sk.length && today != null) rates[_sk[_sk.length-1]] = {KRW: today}; }
  // 3) 렌더. 현재값(today)이 차트의 '현재 점'과 환전 매력도 게이지에 그대로 반영됨.
  const _l7 = Object.keys(rates).sort().slice(-7).map(k => rates[k].KRW);  // 최근 7영업일
  avg1w = _l7.length ? _l7.reduce((a,b)=>a+b,0)/_l7.length : null;
  render(today, yest);
  renderBaseline(today, rates);
  renderGauge(today, rates);
  chartRates = rates; chartNow = today;
  renderChart(today, rates, chartDays);
  renderTip();
  document.getElementById('rateSrc').textContent = srcName;
}
// 환전 매력도 ⓘ: 지금 선택된 탭(페르소나)의 기준만 설명.
const ABOUT = __ABOUT_JSON__;   // 페르소나별 "왜 만들었게"(셋 다 제작자 실제 경험)
const GAUGE_WHY_TITLE = {
  student: '유학생은 왜 이렇게 볼까?',
  investor: '투자자는 왜 이렇게 볼까?',
  traveler: '여행자는 왜 이렇게 볼까?',
};
const GAUGE_WHY = {
  student: '유학생은 생활비를 매달 반복해서 환전한다. 큰 금액을 한 번에 바꿀 필요 없이 여러 달에 나눠 살 수 있어 당장 급하지 않다. 이번 달은 생활비만 바꾸고, 환율이 더 내려가면 나중에 더 환전하면 된다. 그래서 세 경우 중 가장 길게(장기) 봤을 때 지금이 비싼 시기인지를 더 크게 본다.',
  investor: '투자자는 사고 싶은 주식이 생긴 시점에 맞춰 그때 환전한다. 환율만 보는 게 아니라 주가도 함께 봐야 해서, 마음에 들 때 바로 환전하고 별로면 매수를 미루기도 한다. 오래 환율만 기다리지는 않는다. 그래서 세 경우 중 가장 짧게(단기) 봤을 때 지금이 최근 흐름보다 싼지를 더 크게 본다.',
  traveler: '여행자는 출국 날짜가 정해져 있고, 그 전에 정해둔 금액을 꼭 환전해야 한다. 무한정 미룰 수는 없지만, 출국 전까지는 그나마 나은 날을 고를 수 있다. 그래서 유학생과 투자자의 중간(중기) 기간을 기준으로 지금이 살 만한 수준인지를 본다.',
};
// GA4 커스텀 이벤트(gtag 없으면 무시).
function track(name, params){ try{ if(window.gtag) gtag('event', name, params || {}); }catch(e){} }

// 용어 클릭 -> 뜻 바텀시트. 다른 곳 누르면 닫힘.
document.addEventListener('click', e => {
  const sheet = document.getElementById('defSheet'), bd = document.getElementById('defBackdrop');
  const t = e.target.closest('.term, .impact-info, .about-dog, .gauge-info');
  if(t){
    let term, def;
    if(t.classList.contains('gauge-info')){
      term = GAUGE_WHY_TITLE[activeSet] || GAUGE_WHY_TITLE.student;
      def = GAUGE_WHY[activeSet] || GAUGE_WHY.student;
    } else if(t.classList.contains('about-dog')){
      term = '만든 사람';
      def = ABOUT[activeSet] || ABOUT.student;   // 현재 탭의 '왜 만들었게'
    } else if(t.classList.contains('term')){ term = '🔍 ' + t.dataset.term; def = t.dataset.def; }
    else { term = t.dataset.term; def = t.dataset.def; }
    track(t.classList.contains('about-dog') ? 'open_about'
        : t.classList.contains('gauge-info') ? 'gauge_info'
        : t.classList.contains('impact-info') ? 'impact_info' : 'term_click', {label: term});
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
  track('chart_period', {period: b.textContent.trim()});
  document.querySelectorAll('#chartTabs .ctab').forEach(t => t.classList.toggle('active', t === b));
  if(chartRates) renderChart(chartNow, chartRates, chartDays);
});

// 차트 탭/드래그 → 그 시점 종가 보기(크로스헤어). 종가 기준이라 라벨은 날짜+환율.
(function(){
  const el = document.getElementById('chart');
  if(!el) return;
  let pressing = false;
  function show(ev){
    if(!CG) return;
    const box = el.getBoundingClientRect();
    let fx = (ev.clientX - box.left) / box.width;
    fx = Math.min(1, Math.max(0, fx));
    const n = CG.vals.length, denom = (CG.W - CG.padL - CG.padR) || 1;
    let i = Math.round((fx*CG.W - CG.padL) / denom * (n-1));
    i = Math.min(n-1, Math.max(0, i));
    const v = CG.vals[i];
    const xp = (CG.padL + (n>1 ? i/(n-1) : 0) * denom) / CG.W * 100;
    const yp = (CG.yTop + CG.yBand * (1 - (v - CG.min) / (CG.span || 1))) / CG.H * 100;
    const cross = document.getElementById('chartCross');
    if(!cross) return;
    cross.style.display = 'block';
    cross.querySelector('.cross-line').style.left = xp.toFixed(1) + '%';
    const dot = cross.querySelector('.cross-dot');
    dot.style.left = xp.toFixed(1) + '%'; dot.style.top = yp.toFixed(1) + '%';
    const lab = cross.querySelector('.cross-lab');
    const p = CG.keys[i].split('-');
    lab.textContent = (+p[1]) + '/' + (+p[2]) + ' · ' + won(v);
    lab.style.left = xp.toFixed(1) + '%';
    lab.style.transform = xp < 14 ? 'translateX(0)' : (xp > 86 ? 'translateX(-100%)' : 'translateX(-50%)');
    ev.preventDefault();
  }
  el.addEventListener('pointerdown', e => { pressing = true; try{ el.setPointerCapture(e.pointerId); }catch(_){} show(e); });
  el.addEventListener('pointermove', e => { if(pressing) show(e); });
  function hide(){ pressing = false; const c = document.getElementById('chartCross'); if(c) c.style.display = 'none'; }
  window.addEventListener('pointerup', hide);
  window.addEventListener('pointercancel', hide);
})();

// 품목군 토글: 탭 클릭 시 활성 세트만 바꿔 재렌더(환율 재요청 없음).
document.getElementById('tabs').addEventListener('click', e => {
  const b = e.target.closest('.tab');
  if(!b) return;
  activeSet = b.dataset.set;
  track('select_persona', {persona: activeSet});
  try{ localStorage.setItem('w2e_persona', activeSet); }catch(e){}   // 다음 방문에 기억
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === b));
  if(lastDelta != null) renderFeel(lastDelta);
  if(chartRates) renderGauge(chartNow, chartRates);   // 관점 바뀌면 매력도도 재계산
  renderTip();                                         // 관점 바뀌면 조언·제목·예시도 재계산
  flashPersona();                                      // "바뀌었다" 체감(살짝 깜빡)
});
// 페르소나 전환 모션: 바뀐 영역이 살짝 떠오르며 페이드인(1안) + '○○ 기준' 배지 톡(2안). 투박한 깜빡 X.
function flashPersona(){
  ['verdictNow','tipBox','gaugeWrap'].forEach(id => {
    const el = document.getElementById(id);
    if(el && el.animate) el.animate(
      [{opacity:.45, transform:'translateY(5px)'}, {opacity:1, transform:'translateY(0)'}],
      {duration:300, easing:'cubic-bezier(.2,.7,.2,1)'});
  });
  const tag = document.getElementById('vnTag');
  if(tag && tag.animate) tag.animate(
    [{transform:'scale(1.14)'}, {transform:'scale(1)'}],
    {duration:320, easing:'ease-out'});
}

// 경제 일정 D-Day — 보는 위치와 무관하게 '한국시간(KST) 오늘' 기준으로 고정.
(function(){
  const kstToday = new Date(Date.now() + 9*3600000).toISOString().slice(0,10);  // KST 날짜
  const diff = (a,b) => Math.round((Date.parse(a+'T00:00:00Z') - Date.parse(b+'T00:00:00Z'))/86400000);
  document.querySelectorAll('.cal-row').forEach(row => {
    const el = row.querySelector('.cal-dday'), date = row.dataset.date;
    const item = row.closest('.cal-item');
    if(!date || isNaN(Date.parse(date+'T00:00:00Z'))){ el.style.display = 'none'; return; }
    const days = diff(date, kstToday);
    if(days < 0){ el.style.display = 'none'; if(item) item.classList.add('past'); }  // 과거: 배지 빼고 카드 흐리게
    else if(days === 0){ el.textContent = 'D-DAY'; el.classList.add('today'); }
    else { el.textContent = 'D-' + days; }
  });
})();

// 일정 펼침·피드백 클릭 추적.
document.querySelectorAll('.cal-item').forEach(d => d.addEventListener('toggle', () => {
  if(d.open) track('calendar_open', {event: ((d.querySelector('.cal-name')||{}).textContent || '').trim()});
}));
const _fb = document.querySelector('a[href*="forms.gle"]');
if(_fb) _fb.addEventListener('click', () => track('feedback_click'));
const _ig = document.getElementById('instaBtn');
if(_ig) _ig.addEventListener('click', () => track('instagram_click'));

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

// 진입 시 페르소나 적용: 공유링크 ?p=investor 우선, 없으면 지난 방문 기억값. (loadRate 전에 = 첫 렌더부터 맞춤)
(function(){
  const ok = ['student','investor','traveler'];
  let saved = null; try{ saved = localStorage.getItem('w2e_persona'); }catch(e){}
  const q = new URLSearchParams(location.search).get('p');
  const pick = ok.includes(q) ? q : (ok.includes(saved) ? saved : null);
  if(pick){
    activeSet = pick;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.set === pick));
  }
})();

loadRate();
loadViews();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""build_site — run the pipeline and publish the share page into site/ with archive.

Modes (cost control):
  full  : Scout -> factor_analysis -> rewrite-why -> econ_calendar -> share_page
  light : (no Scout) -> share_page  (rate+verdict refresh only; reuses latest
          factors/calendar). Free — never touches Firecrawl, so a credit/news
          outage can't blank the daily update.

Outputs:
  site/index.html          latest page (always current)
  site/d/YYYY-MM-DD.html   that day's snapshot (last build of the day wins)
  site/archive.json        manifest of past days -> drives the '지난 브리핑' list

Standalone. Run directly:
    python executions/build_site.py --mode full
    python executions/build_site.py --mode light
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = os.path.join(ROOT, "site")
DDIR = os.path.join(SITE, "d")
ARCH = os.path.join(SITE, "archive.json")

BADGE = {"good": "환전 추천", "mid": "지금은 보통", "bad": "환전 비추천"}
CLS = {"GOOD": "good", "NEUTRAL": "mid", "BAD": "bad"}


def run(name, script, extra=None, soft=False):
    cmd = [sys.executable, os.path.join(HERE, script)] + (extra or [])
    print(f"\n{'='*56}\n  {name}\n{'='*56}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        if soft:  # non-critical stage: warn and keep building
            print(f"\n⚠️ {name} failed (exit {r.returncode}) — 건너뛰고 계속.", file=sys.stderr)
            return
        sys.exit(f"\nbuild stopped: {name} failed (exit {r.returncode}).")


def latest(pattern):
    # 파일명 타임스탬프 기준 사전식 max(=최신). mtime은 git 체크아웃 후 신뢰 불가.
    m = glob.glob(os.path.join(ROOT, "output", pattern))
    return max(m) if m else None


# 표지·캐러셀과 동일 기준의 desync 방지(제목 환율 숫자 금지).
_RATE_NUM_RE = re.compile(r"\d[\d,]*\s*원|\b1[0-9]{3}\b|\d{3,}\s*선")


def gate(force=False):
    """웹 배포 전 검증(share_page 직전). card_title·요약 ↔ 실제 환율 + 엔진 checks 상속.
    캐러셀(carousel.validate_facts)·엔진(factor_analysis.validate_briefing)과 같은 기준."""
    ff = latest("factors-*.json")
    if not ff:
        return
    fj = load(ff, {})
    rj = load(os.path.join(SITE, "rate.json"), {})
    blocks, warns = [], []
    title = (fj.get("card_title") or "").strip()
    text = " ".join([title, " ".join(fj.get("tldr") or []), fj.get("overall_why") or ""])
    if _RATE_NUM_RE.search(title):
        blocks.append(f"card_title에 환율 숫자 → 박스와 desync: '{title}'")
    ck = fj.get("checks") or {}
    blocks += [f"[엔진] {b}" for b in (ck.get("blocks") or [])]
    warns += [f"[엔진] {w}" for w in (ck.get("warnings") or [])]
    empties = [f.get("name", "?") for f in (fj.get("factors") or [])
               if not (f.get("sources") or [])]
    if empties:
        blocks.append(f"근거 기사 0개 요인: {', '.join(empties)} (환각 위험)")
    r, p = rj.get("rate"), rj.get("prev")
    if isinstance(r, (int, float)) and isinstance(p, (int, float)):
        actual = r - p
        for m in re.finditer(r"(\d+)\s*원\s*(?:넘게|이상|가까이|가량)?\s*"
                             r"(하락|내려|내린|떨어|급락|상승|올라|오른|급등)", text):
            if abs(int(m.group(1)) - abs(actual)) > 3:
                blocks.append(f"'{m.group(1)}원 {m.group(2)}' ↔ 실제 전일대비 {actual:+.1f}원")
    for w in warns:
        print("  ⚠ " + w, file=sys.stderr)
    if blocks:
        print("=" * 56, file=sys.stderr)
        print("❌ 웹 배포 검증 실패 — 배포 중단!", file=sys.stderr)
        for b in blocks:
            print("  • " + b, file=sys.stderr)
        print("=" * 56, file=sys.stderr)
        if not force:
            sys.exit("→ 제목/텍스트 수정(또는 factor_analysis 재생성) 후 다시. 강행: --force")
        print("⚠️ --force: 검증 실패에도 배포 강행.", file=sys.stderr)


def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def day_close_chg(rj, day):
    """'지난 브리핑' 배지용: 그날의 *실제 종가*와 *직전 거래일 대비 등락*을 rate.json series에서 도출.
    빌드시점 값(rj.rate/fluctuations)이 아니라 종가 시계열을 써서 배지가 그날 실제 종가와 일치한다.
    직전 거래일은 series에서 day보다 앞선 마지막 날 → 주말·휴장은 series에 없어 자동으로 건너뛴다.
    series에 그날이 아직 없으면(장중) 현재가(rj.rate)로 폴백."""
    series = [s for s in (rj.get("series") or []) if s.get("date") and s.get("close") is not None]
    by_date = {s["date"]: s["close"] for s in series}
    close = by_date.get(day, rj.get("rate"))
    prev = next((by_date[d] for d in sorted(by_date, reverse=True) if d < day), None)
    chg = round(close - prev, 1) if isinstance(close, (int, float)) and isinstance(prev, (int, float)) else None
    return (round(close, 1) if isinstance(close, (int, float)) else close), chg


def main():
    p = argparse.ArgumentParser(description="Build & publish the share page into site/.")
    p.add_argument("--mode", choices=["full", "light"], default="full")
    p.add_argument("--query", default=None, help="Override the Scout search query.")
    p.add_argument("--refresh-headline", action="store_true",
                   help="그날 제목을 최신 factors로 강제 재생성(동결 무시). 평소엔 첫 퍼블리시 제목 유지.")
    p.add_argument("--force", action="store_true",
                   help="검증 실패에도 배포 강행(주의 — 박스/제목 모순 가능).")
    args = p.parse_args()

    os.makedirs(DDIR, exist_ok=True)
    scout_extra = ["--query", args.query] if args.query else None

    run("Rate (site/rate.json)", "rate_fetch.py")

    if args.mode == "full":
        # Scout feeds only the timing-verdict context; factor_analysis/calendar
        # do their own searches. So a Scout hiccup must NOT kill the build.
        run("Scout", "exchange_rate_watcher.py", scout_extra, soft=True)
        run("Factor analysis", "factor_analysis.py")
        run("Rewrite why + tldr", "factor_analysis.py", ["--rewrite-why"])
        # 캘린더는 보조 단계 — LLM 출력 파싱 등으로 한 번 실패해도 직전 calendar-*.json을 재사용하고
        # 빌드를 계속한다(데일리 업데이트가 통째로 막히지 않게). soft=True.
        run("Econ calendar", "econ_calendar.py", soft=True)
    # light: Scout 생략. factor_analysis를 안 돌려 Scout 결과를 쓰지도 않으므로
    # (요인/일정은 직전 full 산출물 재사용) Firecrawl 호출은 순수 낭비였음.

    # 배포 전 검증 게이트 — 표지/캐러셀과 같은 기준(제목 desync·환율 모순·엔진 checks).
    gate(force=args.force)

    # Render the page into site/index.html with the '지난 브리핑' archive list.
    run("Share page", "share_page.py",
        ["--out", os.path.join(SITE, "index.html"), "--archive", ARCH])

    # Snapshot today's page (last build of the day wins).
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    day_rel = f"d/{today}.html"
    shutil.copyfile(os.path.join(SITE, "index.html"), os.path.join(DDIR, f"{today}.html"))

    # 주말(토·일)은 외환시장 휴장 → 독립 브리핑이 없어야 하므로 '지난 브리핑'에 새 항목을 만들지 않는다.
    # (메인 페이지는 위에서 이미 갱신됐고, 금요일 종가로 표시된다.)
    if datetime.now(timezone(timedelta(hours=9))).weekday() >= 5:
        print(f"\nBuilt site/index.html + {day_rel}  (mode={args.mode}) — 주말 휴장, '지난 브리핑' 미갱신.")
        print(f"Open: file:///{os.path.join(SITE, 'index.html').replace(os.sep, '/')}")
        return

    # Upsert today's entry into the archive manifest (shown from tomorrow on).
    # 제목(headline)은 '그날 첫 퍼블리시' 시점에 한 번 정해 고정(동결)한다.
    # 같은 날 이후 빌드(특히 light)는 환율만 갱신하고 제목은 그대로 유지 —
    # 그래서 날짜별 제목이 그날 분석에 고정되고, 인접 날짜가 같은 제목이 되는 일이 없다.
    # 강제로 다시 뽑으려면 --refresh-headline.
    arc = load(ARCH, [])
    prev = next((e for e in arc if isinstance(e, dict) and e.get("date") == today), None)

    if prev and prev.get("headline") and not args.refresh_headline:
        headline = prev["headline"]            # 동결: 그날 첫 제목 유지
    else:
        headline = ""                          # 그날 첫 퍼블리시(또는 강제 갱신) → 새로 생성
        ff = latest("factors-*.json")
        if ff:
            fj = load(ff, {})
            tl = fj.get("tldr") or []
            headline = (fj.get("card_title") or (tl[0] if tl else fj.get("overall_why", "")))[:30]
    rj = load(os.path.join(SITE, "rate.json"), {})
    day_rate, day_chg = day_close_chg(rj, today)   # 빌드시점 값이 아니라 그날 실제 종가/직전거래일 등락
    entry = {"date": today, "file": day_rel,
             "rate": day_rate, "chg": day_chg, "headline": headline}

    arc = [e for e in arc if isinstance(e, dict) and e.get("date") != today]
    arc.append(entry)
    arc.sort(key=lambda e: e.get("date", ""), reverse=True)
    with open(ARCH, "w", encoding="utf-8") as fh:
        json.dump(arc, fh, ensure_ascii=False, indent=2)

    print(f"\nBuilt site/index.html + {day_rel}  (mode={args.mode}, archive={len(arc)} days)")
    print(f"Open: file:///{os.path.join(SITE, 'index.html').replace(os.sep, '/')}")


if __name__ == "__main__":
    main()

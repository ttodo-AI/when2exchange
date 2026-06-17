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
        run("Econ calendar", "econ_calendar.py")
    # light: Scout 생략. factor_analysis를 안 돌려 Scout 결과를 쓰지도 않으므로
    # (요인/일정은 직전 full 산출물 재사용) Firecrawl 호출은 순수 낭비였음.

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

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


def run(name, script, extra=None):
    cmd = [sys.executable, os.path.join(HERE, script)] + (extra or [])
    print(f"\n{'='*56}\n  {name}\n{'='*56}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
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


def main():
    p = argparse.ArgumentParser(description="Build & publish the share page into site/.")
    p.add_argument("--mode", choices=["full", "light"], default="full")
    p.add_argument("--query", default=None, help="Override the Scout search query.")
    args = p.parse_args()

    os.makedirs(DDIR, exist_ok=True)
    scout_extra = ["--query", args.query] if args.query else None

    run("Rate (site/rate.json)", "rate_fetch.py")

    if args.mode == "full":
        run("Scout", "exchange_rate_watcher.py", scout_extra)
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

    # Upsert today's entry into the archive manifest (shown from tomorrow on).
    # 그날 환율(매매기준율=종가) + 전일 대비 + 짧은 후크 제목.
    headline = ""
    ff = latest("factors-*.json")
    if ff:
        fj = load(ff, {})
        tl = fj.get("tldr") or []
        headline = (fj.get("card_title") or (tl[0] if tl else fj.get("overall_why", "")))[:30]
    rj = load(os.path.join(SITE, "rate.json"), {})
    entry = {"date": today, "file": day_rel,
             "rate": rj.get("rate"), "chg": rj.get("fluctuations"), "headline": headline}

    arc = [e for e in load(ARCH, []) if isinstance(e, dict) and e.get("date") != today]
    arc.append(entry)
    arc.sort(key=lambda e: e.get("date", ""), reverse=True)
    with open(ARCH, "w", encoding="utf-8") as fh:
        json.dump(arc, fh, ensure_ascii=False, indent=2)

    print(f"\nBuilt site/index.html + {day_rel}  (mode={args.mode}, archive={len(arc)} days)")
    print(f"Open: file:///{os.path.join(SITE, 'index.html').replace(os.sep, '/')}")


if __name__ == "__main__":
    main()

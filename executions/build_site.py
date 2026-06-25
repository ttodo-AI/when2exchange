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
from datetime import datetime, timezone, timedelta, date

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


# ── API 감축 가드 (full 하루 1회 / 캘린더 주간+트리거) ──────────────────────────
def today_factors_exist(today: date) -> bool:
    """오늘 날짜로 생성된 factors-*.json이 이미 있나 (full 중복 실행 방지)."""
    return any(f"factors-{today.isoformat()}" in os.path.basename(p)
               for p in glob.glob(os.path.join(ROOT, "output", "factors-*.json")))


def should_run_calendar(today: date):
    """econ_calendar를 돌릴지 결정 (주간+트리거). → (bool, 이유).
    매일 안 돌리되: 캘린더 없음 / 월요일(주 시작) / 3일 이상 묵음 / ★★★ 이벤트가
    어제~오늘이면(결과 채우기) 재생성. 그 외엔 최신 calendar 재사용(콜 절약)."""
    cfs = sorted(glob.glob(os.path.join(ROOT, "output", "calendar-*.json")))
    if not cfs:
        return True, "캘린더 없음"
    m = re.search(r"calendar-(\d{4}-\d{2}-\d{2})", os.path.basename(cfs[-1]))
    cal_date = m.group(1) if m else None
    if cal_date != today.isoformat():
        if today.weekday() == 0:
            return True, "주 시작(월요일)"
        try:
            if (today - date.fromisoformat(cal_date)).days >= 3:
                return True, f"{(today - date.fromisoformat(cal_date)).days}일 묵음"
        except (ValueError, TypeError):
            return True, "캘린더 날짜 파싱 실패"
    # ★★★ 이벤트가 방금(어제~오늘) 지났으면 결과를 채우러 1회 재생성
    try:
        for e in json.load(open(cfs[-1], encoding="utf-8")).get("events", []):
            try:
                d = (date.fromisoformat(e.get("date", "")) - today).days
            except ValueError:
                continue
            if int(e.get("importance", 0)) >= 3 and -1 <= d <= 0:
                return True, f"★★★ '{(e.get('name') or '')[:12]}' 방금 지남(결과 갱신)"
    except (OSError, json.JSONDecodeError):
        pass
    return False, "최신 calendar 재사용"


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
    # check A: 장중(마감 전)인데 '오늘 종가/마감' 단정 = 거짓 (carousel·factor_analysis와 동일)
    today = datetime.now(timezone(timedelta(hours=9))).date()
    asof = rj.get("asof", "")
    tm = re.search(r"(\d{1,2}):(\d{2})", asof or "")
    cur = rj.get("rate")
    if (today.weekday() < 5 and tm and (int(tm.group(1)), int(tm.group(2))) < (15, 30)
            and isinstance(cur, (int, float))):
        cur_i = int(cur + 0.5)
        for m in re.finditer(r"(\d[\d,]*)\s*원\s*(?:에|으로|선에서?)?\s*(마쳤|마감|종가)", text):
            try:
                n = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if abs(n - cur_i) > 2 or re.search(r"(어제|전일|지난|어젯|야간)",
                                               text[max(0, m.start() - 6):m.start()]):
                continue
            blocks.append(f"장중(마감 전, {asof})인데 오늘({cur_i:,}원) '{m.group(2)}' 단정 — '현재 ○○원'으로")
            break
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


def heal_latest_factors():
    """light가 재사용(또는 방금 생성)한 factors의 예보어·정밀 델타숫자를 코드로 중화하고
    checks를 재계산한다(API 0). full엔 멱등, light 재사용분 desync를 무인으로 해소 →
    게이트 하드실패(=사람이 수동으로 고치던 일)를 없앤다."""
    ff = latest("factors-*.json")
    if not ff:
        return
    fj = load(ff, None)
    if not isinstance(fj, dict):
        return
    try:
        from factor_analysis import sanitize_copy, validate_briefing
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ factors 자가치유 건너뜀(import 실패): {e}", file=sys.stderr)
        return
    rj = load(os.path.join(SITE, "rate.json"), {})
    cal = latest("calendar-*.json")
    events = (load(cal, {}) or {}).get("events", []) if cal else []
    today = datetime.now(timezone(timedelta(hours=9))).date()
    sanitize_copy(fj, rj, today)
    blocks, warns = validate_briefing(fj, rj, events, "", today)
    fj["checks"] = {"passed": not blocks, "blocks": blocks, "warnings": warns}
    with open(ff, "w", encoding="utf-8") as fh:
        json.dump(fj, fh, ensure_ascii=False, indent=2)
    print(f"🩹 factors 자가치유 적용({os.path.basename(ff)}) — 잔여 blocks={len(blocks)}", flush=True)


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
    p.add_argument("--force-full", action="store_true",
                   help="오늘 factors가 이미 있어도 factor_analysis 강제 재실행(큰 이벤트 후 갱신용).")
    args = p.parse_args()

    os.makedirs(DDIR, exist_ok=True)
    scout_extra = ["--query", args.query] if args.query else None
    today_d = datetime.now(timezone(timedelta(hours=9))).date()

    run("Rate (site/rate.json)", "rate_fetch.py")

    if args.mode == "full":
        # Scout feeds only the timing-verdict context; factor_analysis/calendar
        # do their own searches. So a Scout hiccup must NOT kill the build.
        run("Scout", "exchange_rate_watcher.py", scout_extra, soft=True)
        # #3 full 하루 1회: 오늘 factors가 이미 있으면 재분석 생략(중복 API 방지). 큰 이벤트
        # 후 갱신은 --force-full. (factor_analysis가 why/tldr/card_title까지 한 콜에 만들어
        #  #1 별도 rewrite-why 패스는 폐지 — 중복 콜이었음. light/refresh는 --rewrite-why 유지.)
        if today_factors_exist(today_d) and not args.force_full:
            print("\n⏭ Factor analysis 생략 — 오늘 factors 이미 있음(재사용). 강제: --force-full",
                  flush=True)
        else:
            run("Factor analysis", "factor_analysis.py")
        # #2 캘린더 주간+트리거: 매일 안 돌리고, 묵었거나 ★★★ 직후에만 재생성(콜 절약).
        do_cal, why_cal = should_run_calendar(today_d)
        if do_cal:
            run("Econ calendar", "econ_calendar.py", soft=True)   # 실패해도 직전 재사용
        else:
            print(f"\n⏭ Econ calendar 생략 — {why_cal}", flush=True)
    # light: Scout·factor·calendar 생략(직전 full 산출물 재사용). rate/verdict만 새로고침.

    # 예보어·델타숫자 자가치유(API 0) — light 재사용분 desync까지 무인 해소 후 게이트.
    heal_latest_factors()
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

    # 동결은 light 빌드에만 적용. full 빌드는 그날 새 분석(factors)을 했으므로 제목을 갱신한다 —
    # 큰 이벤트(FOMC 등)가 끝난 뒤 full이 돌면, 그 전 light가 박아둔 '관망/경계' 제목을
    # 실제 '결과' 제목으로 교체(예: 'FOMC 경계감' → '워시 매파 신호에 원화 약세').
    keep_frozen = (prev and prev.get("headline")
                   and not args.refresh_headline and args.mode != "full")
    if keep_frozen:
        headline = prev["headline"]            # 동결: 그날 제목 유지(light)
    else:
        headline = ""                          # full·강제갱신·첫 퍼블리시 → 새 factors로 생성
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
    # 과거 항목도 매 빌드마다 series '최종 종가'로 rate/chg 재도출 — 그날 장중에 스탬프된
    # 값이 종가로 갱신되도록(예: 6/17이 장중 1,516으로 굳었다가 종가 1,525.5로 정정).
    # 제목(headline)은 동결 유지, 숫자만 종가로 자가치유. series에 그날이 있을 때만.
    series_dates = {s["date"] for s in (rj.get("series") or []) if s.get("date")}
    for e in arc:
        if e.get("date") in series_dates and e.get("date") != today:
            r, c = day_close_chg(rj, e["date"])
            if r is not None:
                e["rate"], e["chg"] = r, c
    arc.sort(key=lambda e: e.get("date", ""), reverse=True)
    with open(ARCH, "w", encoding="utf-8") as fh:
        json.dump(arc, fh, ensure_ascii=False, indent=2)

    print(f"\nBuilt site/index.html + {day_rel}  (mode={args.mode}, archive={len(arc)} days)")
    print(f"Open: file:///{os.path.join(SITE, 'index.html').replace(os.sep, '/')}")


if __name__ == "__main__":
    main()

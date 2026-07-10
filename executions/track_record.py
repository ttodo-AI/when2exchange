#!/usr/bin/env python3
"""track_record — 모트(해자)의 원재료: 앱이 매일 준 '환전 신호'를 환율과 함께 영구 기록.

핵심 원칙: engine.ts(앱이 실제로 보여주는 판정)와 **100% 같은 공식**으로 신호를 계산해
append-only 로그(output/track_record.jsonl)에 남긴다. 신호는 환율 이력의 결정적 함수라
과거분은 fx_history로 재구성(백필)할 수 있지만, "그날 실제로 보여준 신호"의 불변 기록이
신뢰의 핵심이라 매일 한 줄씩 쌓는다.

- 결과물(절약·적중률)은 여기서 계산하지 않는다 → 미래참조 차단(정직). 나중에 fx_history와
  조인해 별도로 계산한다.
- 페르소나 셋(student/investor/traveler) + 공통(base) 신호를 모두 기록.

데이터: output/fx_history.json(2004~) + site/rate.json series(최근). 둘을 병합해 최신까지.
실행: python executions/track_record.py            # 전체 백필(재실행해도 동일 = 결정적)
      python executions/track_record.py --today    # 오늘 한 줄만 append(파이프라인용)
출력: output/track_record.jsonl (한 줄 = 하루)
"""
import json
import math
import os
import sys

# 모트 기록 시작일 — 앱이 라이브로 신호를 공개하기 시작한 날(첫 배포·아카이브 시작).
# 이 날 이전은 백필하지 않는다(사후 재구성 = 모트의 신뢰 훼손). 이전 데이터는 게이지의
# 3개월 percentile 계산용 '이력'으로만 쓴다.
START_DATE = "2026-06-12"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FX = os.path.join(ROOT, "output", "fx_history.json")
RATE = os.path.join(ROOT, "site", "rate.json")
OUT = os.path.join(ROOT, "output", "track_record.jsonl")

# ── engine.ts 이식 (가중치/공식 동일) ────────────────────────────────────
# 게이지 가중치 = [1주, 1달, 3개월]. 합=1.
SET_W = {
    "student": [0.1, 0.3, 0.6],
    "traveler": [0.3, 0.5, 0.2],
    "investor": [0.5, 0.35, 0.15],
    "base": [0.3, 0.4, 0.3],  # 공통(중립 블렌드)
}
GAUGE_PLAIN = ["많이 싼 편", "싼 편", "중간", "비싼 편", "많이 비싼 편"]


def pct_in(closes, n, rate_now):
    v = closes[-n:] if n <= len(closes) else closes[:]
    if len(v) < 2:
        return None
    return sum(1 for x in v if x <= rate_now) / len(v)


def remap(p):
    xs = [0, 0.1, 0.3, 0.7, 0.9, 1]
    ys = [0, 0.2, 0.4, 0.6, 0.8, 1]
    for i in range(1, len(xs)):
        if p <= xs[i]:
            t = (p - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return 1.0


def compute_gauge(closes, rate_now, persona):
    p7 = pct_in(closes, 7, rate_now)
    p30 = pct_in(closes, 22, rate_now)
    p90 = pct_in(closes, 63, rate_now)
    if p90 is None:
        return None
    w = SET_W[persona]
    a7 = p7 if p7 is not None else (p30 if p30 is not None else p90)
    a30 = p30 if p30 is not None else p90
    pct = w[0] * a7 + w[1] * a30 + w[2] * p90
    disp = remap(pct)
    zone = min(4, int(math.floor(disp * 5)))
    return {"pct": round(pct, 4), "disp": round(disp, 4), "zone": zone, "plain": GAUGE_PLAIN[zone]}


def verdict_from_zone(zone):
    idx = 0 if zone <= 1 else (1 if zone == 2 else 2)
    return ["GOOD", "NEUTRAL", "BAD"][idx]


def avg_last(closes, n):
    v = closes[-n:] if n <= len(closes) else closes[:]
    return round(sum(v) / len(v), 1) if v else None


# ── 데이터 병합 + fx_history 일별 갱신(장기 연속성) ──────────────────────
def load_series():
    """fx_history(장기) + rate.json series(최근 자가치유 종가)를 합치고, 그 결과를
    fx_history.json에 되써서 **시계열을 항상 연속**으로 유지한다. 매일 CI에서 돌므로
    rate.json의 60일 창이 fx_history 끝과 멀어져 생기는 '빈 날' 문제를 원천 차단한다.
    fx_history는 종가 캐시(가변)이고, 불변 기록은 track_record.jsonl이다."""
    fx = json.load(open(FX, encoding="utf-8"))
    dates, rates = list(fx["dates"]), list(fx["rates"])
    series = []
    try:
        r = json.load(open(RATE, encoding="utf-8"))
        series = sorted(
            (s["date"], float(s["close"]))
            for s in r.get("series", [])
            if s.get("date") and s.get("close") is not None
        )
    except FileNotFoundError:
        pass
    if series:
        cut = series[0][0]  # series 시작일 — 그 이전은 fx_history 유지, 이후는 series가 권위
        keep_d, keep_r = [], []
        for d, c in zip(dates, rates):
            if d < cut:
                keep_d.append(d)
                keep_r.append(c)
        for d, c in series:
            keep_d.append(d)
            keep_r.append(c)
        if (keep_d, keep_r) != (dates, rates):
            json.dump({"dates": keep_d, "rates": keep_r}, open(FX, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"fx_history 갱신: {len(dates)}→{len(keep_d)}일 (→{keep_d[-1]})")
        dates, rates = keep_d, keep_r
    return dates, rates


def build_record(dates, rates, i, min_history=63):
    if i + 1 < min_history:
        return None
    closes = rates[: i + 1]
    rate_now = rates[i]
    base = compute_gauge(closes, rate_now, "base")
    if base is None:
        return None
    rec = {
        "date": dates[i],
        "rate": round(rate_now, 1),
        "base": {"zone": base["zone"], "verdict": verdict_from_zone(base["zone"]), "gauge_pct": base["pct"]},
        "persona": {},
        "baseline": {"w1": avg_last(closes, 7), "m1": avg_last(closes, 22), "m3": avg_last(closes, 63)},
    }
    for p in ("student", "investor", "traveler"):
        g = compute_gauge(closes, rate_now, p)
        rec["persona"][p] = {"zone": g["zone"], "verdict": verdict_from_zone(g["zone"]), "gauge_pct": g["pct"]}
    return rec


def main():
    today_only = "--today" in sys.argv
    dates, rates = load_series()

    if today_only:
        rec = build_record(dates, rates, len(dates) - 1)
        if rec is None:
            print("신호 계산 불가(이력 부족)", file=sys.stderr)
            return 1
        existing = set()
        if os.path.exists(OUT):
            with open(OUT, encoding="utf-8") as f:
                existing = {json.loads(l)["date"] for l in f if l.strip()}
        if rec["date"] in existing:
            print(f"이미 기록됨: {rec['date']} (스킵)")
            return 0
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"append: {rec['date']} | base={rec['base']['verdict']} | 환율 {rec['rate']}")
        return 0

    # START_DATE부터 백필(이전은 기록 안 함; 게이지 이력으로만 사용). 결정적.
    records = []
    for i in range(len(dates)):
        if dates[i] < START_DATE:
            continue
        rec = build_record(dates, rates, i)
        if rec is not None:
            records.append(rec)
    with open(OUT, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 요약
    from collections import Counter
    dist = Counter(r["base"]["verdict"] for r in records)
    last = records[-1]
    print(f"백필 완료: {len(records)}일 → output/track_record.jsonl")
    print(f"기간: {records[0]['date']} ~ {last['date']}")
    print(f"공통(base) 신호 분포: GOOD {dist['GOOD']} · NEUTRAL {dist['NEUTRAL']} · BAD {dist['BAD']}")
    print(f"최신({last['date']}) 환율 {last['rate']} → 공통 {last['base']['verdict']} / "
          f"유학생 {last['persona']['student']['verdict']} / "
          f"투자자 {last['persona']['investor']['verdict']} / "
          f"여행자 {last['persona']['traveler']['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""track_savings — 모트의 '증명': 신호대로 환전했으면 얼마 아꼈나.

적중률(맞췄나)이 아니라 **비용 비교**로 본다(사용자 요청):
  · 스마트: 빨간불(BAD) 피하고 초록·노란불(GOOD·NEUTRAL) 날에 나눠 환전 → 평균 단가
  · 무신호: 빨간불 아무 날에나 한 번에 환전 → 평균(전형) / 고점(최악) 단가
  · 차이 × 환전 금액 = 절약액

입력: output/track_record.jsonl (track_record.py가 매일 누적)
실행: python executions/track_savings.py            # 공통(base) 신호 기준
      python executions/track_savings.py --persona student
출력: 사람이 읽는 비교 리포트(stdout)

주의(정직성): 표본이 작을 땐 단정하지 않는다. 이건 백테스트(과거 전략검증)가 아니라
'시작일 이후 실제로 쌓인' 라이브 기록에 대한 사후 비교다. 데이터가 쌓일수록 강해진다.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "output", "track_record.jsonl")


def won(n):
    return f"{round(n):,}"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서도 이모지 출력
    except Exception:
        pass
    persona = None
    if "--persona" in sys.argv:
        persona = sys.argv[sys.argv.index("--persona") + 1]

    if not os.path.exists(LOG):
        print("track_record.jsonl이 없어요. 먼저 python executions/track_record.py 실행하세요.", file=sys.stderr)
        return 1
    rows = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]
    if not rows:
        print("기록이 비어 있어요.", file=sys.stderr)
        return 1

    label = persona or "base"

    def verdict(r):
        return (r["persona"][persona] if persona else r["base"])["verdict"]

    buckets = {"GOOD": [], "NEUTRAL": [], "BAD": []}
    for r in rows:
        buckets[verdict(r)].append(r["rate"])

    smart_rates = buckets["GOOD"] + buckets["NEUTRAL"]   # 빨강 회피 + 분할
    bad_rates = buckets["BAD"]

    n_g, n_n, n_b = len(buckets["GOOD"]), len(buckets["NEUTRAL"]), len(buckets["BAD"])
    period = f"{rows[0]['date']} ~ {rows[-1]['date']} ({len(rows)}일)"

    print(f"📊 신호별 비용 비교  ·  {period}  ·  기준: {label}")
    print(f"   🟢 GOOD {n_g}일 · 🟡 NEUTRAL {n_n}일 · 🔴 BAD {n_b}일\n")

    if not smart_rates:
        print("아직 초록·노란불 날이 없어 비교할 수 없어요(전부 빨강). 더 쌓이면 가능해요.")
        return 0
    if not bad_rates:
        print("아직 빨간불 날이 없어 비교할 대상이 없어요. 더 쌓이면 가능해요.")
        return 0

    smart_avg = sum(smart_rates) / len(smart_rates)
    bad_avg = sum(bad_rates) / len(bad_rates)
    bad_worst = max(bad_rates)
    gap_avg = bad_avg - smart_avg
    gap_worst = bad_worst - smart_avg

    print(f"  스마트(빨강 회피·초록/노랑 분할)  평균 {won(smart_avg)}원")
    print(f"  무신호(빨강 아무 날 한 번에)      평균 {won(bad_avg)}원  / 고점 {won(bad_worst)}원")
    print(f"  → 단가 차이: 평균 대비 {won(gap_avg)}원 싸게 / 고점 대비 {won(gap_worst)}원 싸게\n")

    print("  💰 환전 금액별 절약액 (스마트가 이만큼 덜 냄)")
    print(f"     {'금액':>8} | {'무신호 평균 대비':>16} | {'무신호 고점 대비':>16}")
    for usd in (500, 1500, 3000):
        s_avg = gap_avg * usd
        s_worst = gap_worst * usd
        print(f"     {'$'+format(usd, ','):>8} | {won(s_avg)+'원':>16} | {won(s_worst)+'원':>16}")

    print(f"\n  한 줄: 빨간불 피하고 노란불에 나눠 환전하면, $3,000 기준 "
          f"약 {won(gap_avg*3000)}~{won(gap_worst*3000)}원 아껴요.")
    if len(rows) < 30:
        print(f"  ⚠️ 표본 {len(rows)}일로 아직 작아요 — 매일 쌓이며 신뢰도가 올라가요(단정 금지).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Track record — 환전 신호 로깅 (모트의 원재료)

## 목적
앱이 매일 준 **환전 신호(판정)** 를 그날 환율과 함께 영구 기록한다. 이게
docs/MARKETING.md §3의 모트(해자) = **"정직하게 증명된 환전 절약 track record"** 의
원재료다. 신호는 휘발되므로(브라우저에서 즉석 계산) 남기지 않으면 "우리가 그날 뭐라
했는지"를 증명할 수 없다.

## 핵심 원칙 (정직성 = 신뢰)
- **앱과 같은 공식**: `miniapp/src/lib/engine.ts`의 computeGauge/verdictFromZone를
  그대로 이식해 계산한다(신호가 화면과 100% 일치해야 함).
- **결과(절약·적중률)는 로깅하지 않는다**: 미래참조 차단. 나중에 fx_history와 조인해
  별도로 계산한다(§6 인과정직).
- **append-only·불변**: 한 번 기록한 줄은 수정하지 않는다. 알고리즘을 바꿔도 과거 줄은
  그대로 둔다(사후조작 방지).
- **페르소나 셋 다 + 공통**: student/investor/traveler는 게이지 가중치가 달라 판정이
  갈릴 수 있어 각각 기록. 공통(base)은 중립 블렌드.

## 시작일 (중요)
**`START_DATE = 2026-06-12`** — 앱이 라이브로 신호를 공개하기 시작한 날(첫 배포·아카이브
시작). **이 날 이전은 절대 백필하지 않는다.** 2005년부터 21년치를 사후 재구성하면
"알고리즘을 결과 보고 맞춘 것 아니냐"는 의심을 받아 모트의 신뢰가 깨진다. 시작일부터
앞으로만 쌓아야 베낄 수 없는 진짜 해자가 된다. (21년치는 `backtest.py`의 "전략 검증"
전용으로만 쓰고 라이브 기록과 분리.) START_DATE 이전 데이터는 게이지의 3개월 percentile
계산용 '이력'으로만 사용한다.

## 실행 스크립트
`executions/track_record.py`

- 시작일부터 백필(결정적, 재실행해도 동일):
  ```
  python executions/track_record.py
  ```
- 오늘 한 줄만 append(파이프라인용):
  ```
  python executions/track_record.py --today
  ```

## 입력 / 출력
- 입력: `output/fx_history.json`(2004~) + `site/rate.json`의 series(최근) → 병합해 최신까지.
- 출력: `output/track_record.jsonl` (한 줄 = 하루)
  ```json
  {"date":"2026-06-29","rate":1543,
   "base":{"zone":3,"verdict":"BAD","gauge_pct":0.8684},
   "persona":{"student":{...},"investor":{...},"traveler":{...}},
   "baseline":{"w1":1539.1,"m1":1528.5,"m3":1501.7}}
  ```

## 좋은 결과의 모습
- START_DATE(2026-06-12)부터 현재까지 결정적으로 생성(현재 12일, 매일 +1).
- `--today`는 이미 기록된 날이면 스킵(중복 방지).
- 신호 분포·최신 판정이 합리적(고환율 구간 = BAD 우세).

## 다음 단계(별도)
1. **파이프라인 연결**: `publish.yml` 빌드 후 `--today` 호출 → 매일 자동 append.
2. **결과 분석**: track_record + fx_history 조인 → 누적 절약(적립 시뮬) + 적중률 계산
   (모트의 "증명"). `executions/backtest.py`의 라이브 버전.

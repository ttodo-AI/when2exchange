#!/usr/bin/env python3
"""rate_fetch — fetch the current USD/KRW rate into site/rate.json (same-origin).

Primary: Naver 매매기준율 (실시간 갱신 + 현찰 살 때/송금 보낼 때 + 90일 시세).
Fallback: ECB daily (frankfurter). The share page reads this JSON from its own
origin, so no API key and no browser CORS.

Standalone. Run directly:
    python executions/rate_fetch.py
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "site", "rate.json")
KST = timezone(timedelta(hours=9))


def _get(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def from_naver(days: int = 60) -> dict:  # 60 = Naver's max pageSize (~3 months of business days)
    url = f"https://api.stock.naver.com/marketindex/exchange/FX_USDKRW/prices?page=1&pageSize={days}"
    rows = json.loads(_get(url))
    if not isinstance(rows, list) or not rows:
        raise ValueError("naver: empty response")
    top = rows[0]
    series = []
    for r in rows:
        c, d = _num(r.get("closePrice")), r.get("localTradedAt")
        if c and d:
            series.append({"date": d, "close": c})
    series.sort(key=lambda x: x["date"])
    cur = _num(top.get("closePrice"))
    if not cur:
        raise ValueError("naver: no current price")
    return {
        "source": "naver",
        "rate": cur,
        "prev": _num(rows[1].get("closePrice")) if len(rows) > 1 else None,
        "fluctuations": _num(top.get("fluctuations")),       # 전일 대비
        "send": _num(top.get("sendValue")),                  # 송금 보낼 때
        "cashBuy": _num(top.get("cashBuyValue")),            # 현찰 살 때
        "series": series,
    }


def from_ecb(days: int = 100) -> dict:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    url = f"https://api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols=KRW"
    j = json.loads(_get(url))
    rates = j.get("rates", {})
    series = [{"date": d, "close": rates[d]["KRW"]} for d in sorted(rates)]
    if not series:
        raise ValueError("ecb: no data")
    cur = series[-1]["close"]
    prev = series[-2]["close"] if len(series) > 1 else None
    return {
        "source": "ecb", "rate": cur, "prev": prev,
        "fluctuations": round(cur - prev, 2) if (cur and prev) else None,
        "send": None, "cashBuy": None, "series": series,
    }


def _yahoo_close(ticker: str) -> float:
    # 전일(가장 최근) 일별 종가. 실시간 아님.
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=7d"
    res = json.loads(_get(url))["chart"]["result"][0]
    closes = [c for c in (res["indicators"]["quote"][0].get("close") or []) if c is not None]
    if closes:
        return round(closes[-1])
    return round(res["meta"]["regularMarketPrice"])


def fetch_stocks() -> dict:
    # 투자자 탭 '1주' 가격: S&P500 ETF(SPY 고정) + 회전 개별주 5개. 실패 항목은 생략.
    out = {}
    for key, tk in (("SP500", "SPY"), ("TSLA", "TSLA"), ("NVDA", "NVDA"),
                    ("AMZN", "AMZN"), ("GOOGL", "GOOGL"), ("PLTR", "PLTR")):
        try:
            out[key] = _yahoo_close(tk)
        except Exception as exc:
            print(f"stock {tk} failed ({exc})", file=sys.stderr)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch USD/KRW into site/rate.json.")
    p.add_argument("--out", default=DEFAULT_OUT)
    args = p.parse_args()

    try:
        data = from_naver()
    except Exception as exc:
        print(f"naver failed ({exc}); falling back to ECB", file=sys.stderr)
        try:
            data = from_ecb()
        except Exception as exc2:
            sys.exit(f"error: both rate sources failed: {exc2}")

    data["stocks"] = fetch_stocks()
    data["asof"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"rate.json <- {data['source']} | {data['rate']}원 · asof {data['asof']} -> {args.out}")


if __name__ == "__main__":
    main()

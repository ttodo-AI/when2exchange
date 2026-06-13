// rate-proxy — Cloudflare Worker that returns the live USD/KRW 매매기준율 with CORS.
//
// Why: 네이버 API는 브라우저에서 직접 못 부른다(CORS 차단 + Referer 필요). 이 Worker가
// 대신 호출하고 CORS 헤더를 붙여 돌려주면, 정적 사이트(GitHub Pages)도 페이지 열 때마다
// 실시간 환율을 가져올 수 있다. 60초 캐시로 네이버 호출/지연을 최소화.
//
// 배포(택1):
//   1) 대시보드: dash.cloudflare.com → Workers & Pages → Create → 이 파일 내용 붙여넣기 → Deploy
//   2) wrangler: `npx wrangler deploy worker/rate-proxy.js --name when2exchange-rate`
// 배포 후 나오는 URL(예: https://when2exchange-rate.<계정>.workers.dev)을 share_page.py의
// RATE_PROXY_URL에 넣으면 끝.

const NAVER = "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW/prices?page=1&pageSize=2";

// 허용할 출처(본인 사이트만 허락하려면 특정 도메인으로 바꿔도 됨). "*" = 어디서나.
const ALLOW_ORIGIN = "*";

const CORS = {
  "Access-Control-Allow-Origin": ALLOW_ORIGIN,
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function num(s) {
  if (s == null) return null;
  const n = parseFloat(String(s).replace(/,/g, ""));
  return Number.isFinite(n) ? n : null;
}

export default {
  async fetch(request) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS });
    }
    try {
      const res = await fetch(NAVER, {
        headers: {
          "User-Agent": "Mozilla/5.0",
          "Referer": "https://finance.naver.com/",
          "Accept": "application/json",
        },
        // Cloudflare 엣지 캐시 60초 — 네이버 호출 줄이고 응답 빠르게.
        cf: { cacheTtl: 60, cacheEverything: true },
      });
      if (!res.ok) throw new Error("naver http " + res.status);
      const rows = await res.json();
      if (!Array.isArray(rows) || rows.length === 0) throw new Error("naver empty");

      const top = rows[0];
      const rate = num(top.closePrice);
      if (rate == null) throw new Error("no current price");
      const prev = rows.length > 1 ? num(rows[1].closePrice) : null;

      const body = JSON.stringify({
        source: "naver",
        rate,
        prev,
        fluctuations: num(top.fluctuations),       // 전일 대비
        send: num(top.sendValue),                  // 송금 보낼 때
        cashBuy: num(top.cashBuyValue),            // 현찰 살 때
        asof: new Date(Date.now() + 9 * 3600 * 1000) // KST
          .toISOString().slice(0, 16).replace("T", " ") + " KST",
      });
      return new Response(body, {
        headers: {
          ...CORS,
          "Content-Type": "application/json; charset=utf-8",
          // 브라우저/엣지에 60초 캐시 허용.
          "Cache-Control": "public, max-age=60",
        },
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: String(err) }), {
        status: 502,
        headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" },
      });
    }
  },
};

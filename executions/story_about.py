#!/usr/bin/env python3
"""
@when2exchange 인스타 '왜 만들었나' 스토리(하이라이트 저장용) PNG 생성기.

페르소나별(유학생/투자자/여행자) "왜 만들었게"(site/index.html ABOUT = 제작자 실제 경험)를
3컷 스토리로 재구성해, 웹사이트와 동일한 팔레트로 1080x1920 PNG를 Playwright로 렌더한다.
- 첫 컷 첫 줄만 강아지 목소리, 이후는 제작자 1인칭.
- 세이프존: 상 250 / 옆 90 / 하 250 (IG 스토리 UI 가림 회피).

표준 라이브러리 + playwright만 사용. 결과는 stdout, 에러는 stderr.
  python executions/story_about.py [--persona student|investor|traveler|all] [--guides]
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 사이트 마스코트(슈나우저) 인라인 SVG — carousel.py와 동일(투명 배경)
DOG_SVG = (
    '<svg class="dog" viewBox="0 0 72 48" role="img">'
    '<rect x="16" y="30" width="4.6" height="12" rx="2.3" fill="#7f8893"/>'
    '<rect x="24" y="30" width="4.6" height="12" rx="2.3" fill="#8b94a0"/>'
    '<rect x="41" y="30" width="4.6" height="12" rx="2.3" fill="#7f8893"/>'
    '<rect x="49" y="30" width="4.6" height="12" rx="2.3" fill="#8b94a0"/>'
    '<rect x="11" y="16" width="46" height="18" rx="9" fill="#9aa3ad"/>'
    '<rect x="46" y="7" width="21" height="21" rx="7" fill="#9aa3ad"/>'
    '<path d="M48 9 q1 -7 7 -5 l-1 8 z" fill="#7f8893"/>'
    '<path d="M53 12 q5 -2 9 1 q-4 0 -9 2 z" fill="#d3d8de"/>'
    '<circle cx="58" cy="16.5" r="1.8" fill="#1c2230"/>'
    '<path d="M60 19 h8 v4 q0 6 -5 7 q-4 -1 -3 -7 z" fill="#d3d8de"/>'
    '<circle cx="67" cy="20" r="1.9" fill="#1c2230"/></svg>'
)

# ── 스토리 카피 (제작자 실제 경험 = site/index.html ABOUT 기반, 스토리용으로 응축) ──
# kicker=상단 태그, dog=첫 컷 첫 줄(강아지), big=헤드라인, sub=본문(1인칭), cta=마지막 컷 행동
STORIES = {
    "student": {
        "tag": "🎓 유학생 · 왜 만들었개",
        "frames": [
            {
                "dog": "멍멍! 제 주인 얘기 들려드릴개요 🐶",
                "big": '저는 매달 수천 달러를 <span class="kw">환전</span>하는 유학생이에요',
                "sub": "이 페이지, 사실 제가 쓰려고 만들었어요.",
            },
            {
                "big": '환율이 <span class="hot">10원</span>만 올라도 손이 떨렸어요',
                "sub": "“어제 더 해둘걸…” 아침마다 환율 앱이랑 기사를 뒤지다, 정작 타이밍은 놓치기 일쑤였죠.",
            },
            {
                "big": '<span class="kw">“지금 환전해도 될까?”</span><br>30초면 답을 보게 만들었어요',
                "sub": "환율 1원이 내 월세·커피값으로 얼만지까지요.<br>혼자 쓰긴 아까워서 슬쩍 공유해요 🐾",
                "cta": "👉 팔로우하고 매일 환율 정보 받아보기",
            },
        ],
    },
    "investor": {
        "tag": "📈 투자자 · 왜 만들었개",
        "frames": [
            {
                "dog": "멍멍! 제 주인 얘기 들려드릴개요 🐶",
                "big": '25년 4월, <span class="kw">브로드컴 3주</span>로 미국주식을 시작했어요',
                "sub": "그때 환율이 비싸서, 떨어지면 더 사야지 하고 기다렸죠.",
            },
            {
                "big": '환율은 내렸는데,<br>주식이 더 올라 <span class="hot">결국 못 샀어요</span>',
                "sub": "추가로 사려던 주식이 그새 100% 상승. 환율만 보다 매수 타이밍을 통째로 날렸죠.",
            },
            {
                "big": '<span class="kw">“지금이 그나마 쌀 때일까?”</span><br>한눈에 볼 수 있게 만들었어요',
                "sub": "환율만 무작정 기다리다, 매수 타이밍 놓치지 말자구요!",
                "cta": "👉 팔로우하고 매일 환율 정보 받아보기",
            },
        ],
    },
    "traveler": {
        "tag": "✈️ 여행자 · 왜 만들었개",
        "frames": [
            {
                "dog": "멍멍! 제 주인 얘기 들려드릴개요 🐶",
                "big": '미국 놀러 오는 <span class="kw">친구</span>가 물었어요',
                "sub": "“환전 지금 해도 돼? 지금이 비싼 편이야?”",
            },
            {
                "big": '근데 이거, 여행 앞두고<br>단기간에 <span class="hot">감잡기 어렵잖아요</span>',
                "sub": "환율이 비싼지 싼지, 시시각각 바뀌는데 일일이 챙겨보기도 번거롭고요.",
            },
            {
                "big": '<span class="kw">“지금이 비싼 편일까?”</span><br>한눈에 볼 수 있게 만들었어요',
                "sub": "출국 전, 그나마 나은 날을 고를 수 있게요.",
                "cta": "👉 팔로우하고 매일 환율 정보 받아보기",
            },
        ],
    },
}

HEAD = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:'Pretendard',sans-serif;-webkit-font-smoothing:antialiased}
  /* 웹 팔레트: --bg #f4f5f7 / --ink #14171f / --muted #6b7280 / --brand #3b5bdb */
  /* 세이프존: 상250 / 옆90 / 하250 */
  .page{width:1080px;height:1920px;background:#f4f5f7;padding:250px 90px;
        display:flex;flex-direction:column;justify-content:center;color:#14171f;position:relative}
  svg.dog{width:auto;flex:none}

  /* 진행 점 (1/3·2/3·3/3) — 상단 */
  .dots{position:absolute;top:170px;left:90px;right:90px;display:flex;gap:10px}
  .dots i{flex:1;height:7px;border-radius:4px;background:#d7dbe2}
  .dots i.on{background:#3b5bdb}

  .kicker{position:absolute;top:250px;left:90px;display:inline-flex;font-size:30px;font-weight:800;
          color:#3b5bdb;background:#eef1fd;border-radius:999px;padding:14px 26px;letter-spacing:-.01em}

  .dogline{display:flex;align-items:center;gap:16px;margin:0 0 30px;
           font-size:34px;font-weight:700;color:#6b7280;letter-spacing:-.02em}
  .dogline svg.dog{height:48px}

  .mid{display:flex;flex-direction:column}
  .big{font-size:76px;font-weight:900;letter-spacing:-.04em;line-height:1.28;
       word-break:keep-all;color:#14171f}
  .big .kw{color:#3b5bdb;white-space:nowrap}
  .big .hot{color:#e0383e}
  .sub{margin-top:34px;font-size:40px;font-weight:600;line-height:1.55;
       letter-spacing:-.02em;color:#41485a;word-break:keep-all}

  .cta{align-self:flex-start;margin-top:44px;font-size:34px;font-weight:800;
       color:#fff;background:#3b5bdb;border-radius:999px;padding:22px 36px;letter-spacing:-.01em}

  .foot{position:absolute;bottom:190px;left:90px;display:flex;align-items:center;gap:16px;
        color:#9aa1ad;font-size:28px;font-weight:700;letter-spacing:-.01em}
  .foot svg.dog{height:40px}

  /* --guides */
  .guides{position:absolute;inset:0;pointer-events:none;z-index:99}
  .guides i{position:absolute;background:rgba(197,51,58,.13)}
  .guides .gt{top:0;left:0;right:0;height:250px}
  .guides .gb{bottom:0;left:0;right:0;height:250px}
  .guides .safe{position:absolute;top:250px;bottom:250px;left:90px;right:90px;
                border:3px dashed rgba(31,122,71,.7);border-radius:8px}
</style></head><body>"""
FOOT = "</body></html>"
GUIDES = ('<div class="guides"><i class="gt"></i><i class="gb"></i>'
          '<div class="safe"></div></div>')


def frame_html(tag, fr, idx, total, guides=False):
    dots = "".join(f'<i class="{"on" if i <= idx else ""}"></i>' for i in range(total))
    dogline = (f'<div class="dogline">{DOG_SVG}<span>{fr["dog"]}</span></div>'
               if fr.get("dog") else "")
    cta = f'<div class="cta">{fr["cta"]}</div>' if fr.get("cta") else ""
    page = (
        f'<div class="dots">{dots}</div>'
        f'<div class="kicker">{tag}</div>'
        f'<div class="mid">{dogline}<div class="big">{fr["big"]}</div>'
        f'<div class="sub">{fr["sub"]}</div>{cta}</div>'
        f'<div class="foot">{DOG_SVG}<span>환전타이밍 · when2exchange</span></div>'
    )
    return HEAD + '<div class="page">' + page + (GUIDES if guides else "") + "</div>" + FOOT


# ── 하이라이트 커버 (IG가 원으로 크롭 → 가운데 아이콘만, 셋 다 동일 배경 = 한 세트) ──
COVER = {"student": "🎓", "investor": "📈", "traveler": "✈️"}


def cover_html(emoji):
    return (HEAD + '<div style="width:1080px;height:1080px;background:#eef1fd;'
            'display:flex;align-items:center;justify-content:center;'
            f'font-size:520px;line-height:1">{emoji}</div>' + FOOT)


def render(html, out_path, scale=2, w=1080, h=1920):
    from playwright.sync_api import sync_playwright
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=scale)
        pg.set_content(html, wait_until="networkidle")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(out_path), clip={"x": 0, "y": 0, "width": w, "height": h})
        b.close()
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", default="student",
                    choices=["student", "investor", "traveler", "all"])
    ap.add_argument("--out", default=str(ROOT / "output" / "stories"))
    ap.add_argument("--guides", action="store_true")
    ap.add_argument("--covers", action="store_true", help="하이라이트 커버(1080x1080)만 생성")
    args = ap.parse_args()

    if args.covers:
        cov_dir = Path(args.out) / "covers"
        for key, emoji in COVER.items():
            p = render(cover_html(emoji), cov_dir / f"{key}.png", w=1080, h=1080)
            print(f"커버 {key}: {p}")
        return

    keys = list(STORIES) if args.persona == "all" else [args.persona]
    for key in keys:
        story = STORIES.get(key)
        if not story:
            print(f"⚠️ '{key}' 스토리 카피 미작성 — 유학생 확정 후 추가 예정.", file=sys.stderr)
            continue
        out_dir = Path(args.out) / key
        total = len(story["frames"])
        for i, fr in enumerate(story["frames"]):
            p = render(frame_html(story["tag"], fr, i, total), out_dir / f"{i+1:02d}.png")
            print(f"{key} {i+1}/{total}: {p}")
        if args.guides:
            g = render(frame_html(story["tag"], story["frames"][0], 0, total, guides=True),
                       out_dir / "01-guides.png")
            print(f"세이프존 가이드: {g}")


if __name__ == "__main__":
    main()

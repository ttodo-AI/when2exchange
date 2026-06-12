#!/usr/bin/env python3
"""Scorer — rate the Scout's news results for audience fit and content potential.

Reads a Scout (exchange_rate_watcher) output JSON, asks Claude to score each
article 1-10 on relevance and content potential for a target audience, with one
line of reasoning per score, then saves a ranked JSON file.

Standalone script. Run directly:
    python executions/scorer.py
    python executions/scorer.py --in output/krw-exchange-rate-2026-06-04_2208.json
    python executions/scorer.py --audience "Korean 20s interested in saving money"
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

DEFAULT_AUDIENCE = (
    "Korean readers (20s-30s) interested in personal finance, smart spending, "
    "currency exchange, and money-saving tips, following an Instagram/blog account."
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Score Scout news results for audience fit.")
    p.add_argument(
        "--in",
        dest="infile",
        default=None,
        help="Scout output JSON to score (default: latest output/krw-exchange-rate-*.json).",
    )
    p.add_argument("--audience", default=DEFAULT_AUDIENCE, help="Target audience description.")
    p.add_argument("--out", default=None, help="Output path (default output/scored-<ts>.json).")
    p.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="Claude model id (default claude-haiku-4-5-20251001).",
    )
    return p.parse_args()


def find_latest_scout() -> str:
    matches = glob.glob(os.path.join("output", "krw-exchange-rate-*.json"))
    if not matches:
        sys.exit("error: no Scout output found in output/. Run the watcher first.")
    return max(matches, key=os.path.getmtime)


def extract_json_array(text: str):
    """Pull a JSON array out of the model's reply, tolerating code fences/prose."""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def score_articles(client, model: str, audience: str, articles):
    listing = "\n".join(
        f"[{i}] {a.get('title','(untitled)')} :: {(a.get('summary') or '')[:280]}"
        for i, a in enumerate(articles)
    )
    prompt = (
        "You score news articles as potential social-media content for this "
        f"audience:\n{audience}\n\n"
        "For EACH article below, give two integer scores from 1 (poor) to 10 "
        "(excellent):\n"
        "- relevance: how relevant the topic is to this audience\n"
        "- content_potential: how strong an engaging post/story it could make "
        "(hook, shareability, usefulness)\n\n"
        "Return ONLY a JSON array, one object per article, in the same order, "
        'each shaped exactly: {"index": <int>, "relevance": <int>, '
        '"relevance_reason": "<one line>", "content_potential": <int>, '
        '"content_reason": "<one line>"}. No prose, no code fence.\n\n'
        f"ARTICLES:\n{listing}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    scores = extract_json_array(text)
    if scores is None:
        sys.exit(f"error: could not parse scores from model reply:\n{text[:500]}")
    return scores


def main() -> None:
    args = parse_args()
    load_dotenv()

    for stream in (sys.stdout, sys.stderr):  # UTF-8 regardless of console locale
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("error: ANTHROPIC_API_KEY is not set (add it to .env).")

    infile = args.infile or find_latest_scout()
    try:
        with open(infile, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"error: could not read Scout file {infile}: {exc}")

    articles = data.get("articles", [])
    if not articles:
        sys.exit(f"error: no articles in {infile}.")

    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("error: anthropic not installed. Run: pip install -r requirements.txt")

    client = Anthropic(api_key=api_key)
    try:
        raw_scores = score_articles(client, args.model, args.audience, articles)
    except Exception as exc:
        sys.exit(f"error: scoring failed: {exc}")

    by_index = {s.get("index"): s for s in raw_scores if isinstance(s, dict)}
    scored = []
    for i, a in enumerate(articles):
        s = by_index.get(i, {})
        rel = s.get("relevance")
        pot = s.get("content_potential")
        total = (rel + pot) if isinstance(rel, int) and isinstance(pot, int) else -1
        scored.append({
            "title": a.get("title"),
            "link": a.get("link"),
            "date": a.get("date"),
            "relevance": rel,
            "relevance_reason": s.get("relevance_reason"),
            "content_potential": pot,
            "content_reason": s.get("content_reason"),
            "total": total,
        })
    scored.sort(key=lambda x: x["total"], reverse=True)

    now = datetime.now(timezone.utc)
    out_path = args.out or os.path.join("output", f"scored-{now.strftime('%Y-%m-%d_%H%M')}.json")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    payload = {
        "source_file": infile,
        "audience": args.audience,
        "scored_at": now.isoformat(),
        "count": len(scored),
        "articles": scored,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"Scored {len(scored)} article(s) from {infile}")
    print(f"Saved to {out_path}\n")
    print("Ranked (relevance + content potential):\n")
    for rank, a in enumerate(scored, 1):
        rel = a["relevance"] if a["relevance"] is not None else "?"
        pot = a["content_potential"] if a["content_potential"] is not None else "?"
        print(f"{rank}. [{a['total'] if a['total'] >= 0 else '?'}/20] {a['title']}")
        print(f"   relevance {rel}/10 — {a['relevance_reason'] or ''}")
        print(f"   content   {pot}/10 — {a['content_reason'] or ''}")
        if a["link"]:
            print(f"   {a['link']}")
        print()


if __name__ == "__main__":
    main()

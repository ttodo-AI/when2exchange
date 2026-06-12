#!/usr/bin/env python3
"""Editor — turn the top scored results into content briefs.

Reads a Scorer output JSON, takes the top N (default 3) articles, and asks Claude
to write a content brief for each with four parts: hook, point, example, format.
Saves the briefs to a Markdown file.

Standalone script. Run directly:
    python executions/editor.py
    python executions/editor.py --in output/scored-2026-06-04_2216.json --top 3
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Write content briefs from top scored results.")
    p.add_argument(
        "--in",
        dest="infile",
        default=None,
        help="Scorer output JSON (default: latest output/scored-*.json).",
    )
    p.add_argument("--top", type=int, default=3, help="How many top articles to brief (default 3).")
    p.add_argument("--audience", default=None, help="Override the audience (default: read from the scored file).")
    p.add_argument("--out", default=None, help="Output path (default output/briefs-<ts>.md).")
    p.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="Claude model id (default claude-haiku-4-5-20251001).",
    )
    return p.parse_args()


def find_latest_scored() -> str:
    matches = glob.glob(os.path.join("output", "scored-*.json"))
    if not matches:
        sys.exit("error: no Scorer output found in output/. Run the scorer first.")
    return max(matches, key=os.path.getmtime)


def extract_json_array(text: str):
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def write_briefs(client, model: str, audience: str, articles):
    listing = "\n".join(
        f"[{i}] {a.get('title','(untitled)')}\n"
        f"     why it scored well: {a.get('content_reason') or a.get('relevance_reason') or ''}\n"
        f"     link: {a.get('link') or ''}"
        for i, a in enumerate(articles)
    )
    prompt = (
        "You are a content editor. For the following news articles, write a short "
        "content brief for each, aimed at this audience:\n"
        f"{audience}\n\n"
        "Each brief has exactly four parts:\n"
        "- hook: a scroll-stopping opening line (in Korean, punchy)\n"
        "- point: the single core takeaway the post should land (1-2 sentences)\n"
        "- example: a concrete number, comparison, or scenario to include\n"
        "- format: the best format and why (e.g. Reel, carousel, blog post)\n\n"
        "Return ONLY a JSON array, one object per article in the same order, each "
        'shaped exactly: {"index": <int>, "hook": "...", "point": "...", '
        '"example": "...", "format": "..."}. No prose, no code fence.\n\n'
        f"ARTICLES:\n{listing}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    briefs = extract_json_array(text)
    if briefs is None:
        sys.exit(f"error: could not parse briefs from model reply:\n{text[:500]}")
    return briefs


def render_markdown(audience: str, source: str, when: str, articles, briefs_by_index) -> str:
    lines = [
        "# Content Briefs",
        "",
        f"- **Audience:** {audience}",
        f"- **Source (scored file):** `{source}`",
        f"- **Generated:** {when}",
        "",
    ]
    for rank, a in enumerate(articles, 1):
        b = briefs_by_index.get(rank - 1, {})
        score = a.get("total")
        lines += [
            f"## {rank}. {a.get('title','(untitled)')}",
            "",
            f"_Score: {score if score is not None else '?'}/20_  ·  [source]({a.get('link','')})",
            "",
            f"- **Hook:** {b.get('hook','—')}",
            f"- **Point:** {b.get('point','—')}",
            f"- **Example:** {b.get('example','—')}",
            f"- **Format:** {b.get('format','—')}",
            "",
        ]
    return "\n".join(lines)


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

    infile = args.infile or find_latest_scored()
    try:
        with open(infile, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"error: could not read Scorer file {infile}: {exc}")

    all_articles = data.get("articles", [])
    if not all_articles:
        sys.exit(f"error: no articles in {infile}.")

    # Scorer already sorts by total desc, but re-sort defensively.
    all_articles.sort(key=lambda x: x.get("total", -1), reverse=True)
    top = all_articles[: max(1, args.top)]
    audience = args.audience or data.get("audience", "(audience unspecified)")

    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("error: anthropic not installed. Run: pip install -r requirements.txt")

    client = Anthropic(api_key=api_key)
    try:
        raw_briefs = write_briefs(client, args.model, audience, top)
    except Exception as exc:
        sys.exit(f"error: brief generation failed: {exc}")

    briefs_by_index = {b.get("index"): b for b in raw_briefs if isinstance(b, dict)}

    now = datetime.now(timezone.utc)
    when = now.isoformat()
    out_path = args.out or os.path.join("output", f"briefs-{now.strftime('%Y-%m-%d_%H%M')}.md")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    md = render_markdown(audience, infile, when, top, briefs_by_index)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(md)

    # JSON sidecar so downstream stages (the Writer) can consume briefs structurally.
    json_path = os.path.splitext(out_path)[0] + ".json"
    structured = []
    for rank, a in enumerate(top):
        b = briefs_by_index.get(rank, {})
        structured.append({
            "title": a.get("title"),
            "link": a.get("link"),
            "total": a.get("total"),
            "hook": b.get("hook"),
            "point": b.get("point"),
            "example": b.get("example"),
            "format": b.get("format"),
        })
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"source_file": infile, "audience": audience, "generated_at": when, "briefs": structured},
            fh, ensure_ascii=False, indent=2,
        )

    print(f"Wrote {len(top)} content brief(s) from {infile}")
    print(f"Saved to {out_path} (and {json_path})\n")
    print(md)


if __name__ == "__main__":
    main()

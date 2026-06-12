#!/usr/bin/env python3
"""Writer — draft a LinkedIn post from each brief, in the user's voice.

Reads the Editor's briefs (JSON) and a file of the user's real writing samples,
then asks Claude to draft a copy-paste-ready LinkedIn post per brief that mimics
that voice. Saves the drafts to a file.

Standalone script. Run directly:
    python executions/writer.py
    python executions/writer.py --in output/briefs-2026-06-04_2223.json --voice voice/voice-samples.md
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

PLACEHOLDER_MARK = "[PLACEHOLDER"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Draft LinkedIn posts from briefs, in the user's voice.")
    p.add_argument(
        "--in",
        dest="infile",
        default=None,
        help="Editor briefs JSON (default: latest output/briefs-*.json).",
    )
    p.add_argument(
        "--voice",
        default=os.path.join("voice", "voice-samples.md"),
        help="File with the user's real writing samples (default voice/voice-samples.md).",
    )
    p.add_argument("--out", default=None, help="Output path (default output/drafts-<ts>.md).")
    p.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Claude model id (default claude-sonnet-4-6 — voice matching benefits from a stronger model).",
    )
    return p.parse_args()


def find_latest_briefs() -> str:
    matches = glob.glob(os.path.join("output", "briefs-*.json"))
    if not matches:
        sys.exit("error: no Editor briefs JSON found in output/. Run the editor first.")
    return max(matches, key=os.path.getmtime)


def load_voice(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        sys.exit(f"error: could not read voice samples {path}: {exc}")
    # Take everything under a "## Samples" heading if present, else the whole file.
    m = re.search(r"##\s*Samples\s*(.+)", raw, re.IGNORECASE | re.DOTALL)
    samples = (m.group(1) if m else raw).strip()
    # Strip HTML comments.
    samples = re.sub(r"<!--.*?-->", "", samples, flags=re.DOTALL).strip()
    if not samples or PLACEHOLDER_MARK in samples:
        sys.exit(
            f"error: no real writing samples in {path}. Paste 2-3 of your own "
            "short paragraphs under the '## Samples' heading, then re-run."
        )
    return samples


def draft_posts(client, model: str, voice: str, briefs):
    blocks = "\n\n".join(
        f"[{i}] TITLE: {b.get('title','')}\n"
        f"    HOOK: {b.get('hook','')}\n"
        f"    POINT: {b.get('point','')}\n"
        f"    EXAMPLE: {b.get('example','')}\n"
        f"    FORMAT: {b.get('format','')}"
        for i, b in enumerate(briefs)
    )
    prompt = (
        "You are a ghostwriter. Below are REAL writing samples from the author. "
        "Study their voice: tone, sentence length, rhythm, formatting habits, "
        "emoji/hashtag use, and the LANGUAGE they write in. Then draft a LinkedIn "
        "post for each content brief, written so the author could copy-paste it "
        "straight to LinkedIn with no editing.\n\n"
        "Rules:\n"
        "- Match the author's voice and language exactly; do not invent a new style.\n"
        "- LinkedIn-ready: plain text, real line breaks between short paragraphs, "
        "no markdown headings/bold/bullets-with-asterisks. Light emoji only if the "
        "samples use them. A few relevant hashtags at the end are fine.\n"
        "- Use the brief's hook, point, and example; the format is guidance, not "
        "literal (it's a single post).\n"
        "- Keep it tight (roughly 120-220 words).\n\n"
        f"AUTHOR'S VOICE SAMPLES:\n\"\"\"\n{voice}\n\"\"\"\n\n"
        f"CONTENT BRIEFS:\n{blocks}\n\n"
        "Return ONLY a JSON array, one object per brief in the same order, each "
        'shaped exactly: {"index": <int>, "post": "<the full post text, with \\n '
        'line breaks>"}. No prose, no code fence.'
    )
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        sys.exit(f"error: could not parse drafts from model reply:\n{text[:500]}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        sys.exit(f"error: drafts JSON did not parse ({exc}):\n{text[:500]}")


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

    voice = load_voice(args.voice)

    infile = args.infile or find_latest_briefs()
    try:
        with open(infile, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"error: could not read briefs file {infile}: {exc}")

    briefs = data.get("briefs", [])
    if not briefs:
        sys.exit(f"error: no briefs in {infile}.")

    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("error: anthropic not installed. Run: pip install -r requirements.txt")

    client = Anthropic(api_key=api_key)
    try:
        raw_drafts = draft_posts(client, args.model, voice, briefs)
    except Exception as exc:
        sys.exit(f"error: drafting failed: {exc}")

    posts_by_index = {d.get("index"): d.get("post", "") for d in raw_drafts if isinstance(d, dict)}

    now = datetime.now(timezone.utc)
    out_path = args.out or os.path.join("output", f"drafts-{now.strftime('%Y-%m-%d_%H%M')}.md")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    sections = [
        "# LinkedIn drafts",
        "",
        f"_Source briefs: `{infile}`  ·  generated {now.isoformat()}_",
        "",
        "Each post below is plain text — copy everything between the `---` rules.",
        "",
    ]
    for i, b in enumerate(briefs):
        post = posts_by_index.get(i, "(no draft returned)")
        sections += [f"## Draft {i + 1}: {b.get('title','')}", "", "---", post, "---", ""]
    md = "\n".join(sections)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(md)

    # JSON sidecar: full merged record per item (brief fields + the draft post),
    # so downstream tools (the dashboard) get everything from one file.
    json_path = os.path.splitext(out_path)[0] + ".json"
    records = []
    for i, b in enumerate(briefs):
        rec = dict(b)
        rec["post"] = posts_by_index.get(i, "")
        records.append(rec)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"source_file": infile, "generated_at": now.isoformat(), "drafts": records},
            fh, ensure_ascii=False, indent=2,
        )

    print(f"Drafted {len(briefs)} post(s) from {infile}")
    print(f"Saved to {out_path} (and {json_path})\n")
    print(md)


if __name__ == "__main__":
    main()

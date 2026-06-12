#!/usr/bin/env python3
"""Summarize text into bullet points using the Claude API.

Standalone script. Run directly:
    python executions/summarize.py --file notes.txt
    python executions/summarize.py --text "..." --bullets 3
"""
import argparse
import os
import sys

from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Summarize text into bullets via Claude.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="Path to a text/markdown file to summarize.")
    src.add_argument("--text", help="Raw text to summarize.")
    p.add_argument("--bullets", type=int, default=5, help="Number of bullets (default 5).")
    p.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="Claude model id (default claude-haiku-4-5-20251001).",
    )
    return p.parse_args()


def load_input(args: argparse.Namespace) -> str:
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError as exc:
            sys.exit(f"error: could not read {args.file}: {exc}")
    return args.text


def main() -> None:
    args = parse_args()
    load_dotenv()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("error: ANTHROPIC_API_KEY is not set (add it to .env).")

    content = load_input(args).strip()
    if not content:
        sys.exit("error: no input text to summarize.")

    # Imported here so a missing dependency surfaces with a clear message.
    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("error: anthropic not installed. Run: pip install -r requirements.txt")

    client = Anthropic(api_key=api_key)
    prompt = (
        f"Summarize the following text into exactly {args.bullets} concise "
        f"bullet points. Output only the bullets, one per line, each starting "
        f"with '- '.\n\n---\n{content}\n---"
    )

    try:
        resp = client.messages.create(
            model=args.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # network/API/auth errors
        sys.exit(f"error: Claude API call failed: {exc}")

    summary = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()

    print(summary)


if __name__ == "__main__":
    main()

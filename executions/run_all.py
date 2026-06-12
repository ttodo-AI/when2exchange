#!/usr/bin/env python3
"""run_all — run the full content pipeline end to end, then open the dashboard.

Stages: Scout -> Scorer -> Editor -> Writer -> Dashboard.
Each stage auto-picks the previous stage's most recent output file, so this just
runs them in order and stops if any stage fails.

Standalone script. Run directly:
    python executions/run_all.py
    python executions/run_all.py --query "원/달러 환율 전망 환전" --no-open
"""
import argparse
import glob
import os
import subprocess
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # project root; scripts use output/ relative to cwd

STAGES = [
    ("Scout", "exchange_rate_watcher.py"),
    ("Scorer", "scorer.py"),
    ("Editor", "editor.py"),
    ("Writer", "writer.py"),
    ("Dashboard", "dashboard.py"),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the full Scout->...->Dashboard pipeline.")
    p.add_argument("--query", default=None, help="Override the Scout's search query.")
    p.add_argument("--full", action="store_true", help="Pass --full to the Scout (scrape article bodies).")
    p.add_argument("--no-open", action="store_true", help="Do not open the dashboard at the end.")
    return p.parse_args()


def run_stage(name: str, script: str, extra=None) -> None:
    cmd = [sys.executable, os.path.join(HERE, script)] + (extra or [])
    print(f"\n{'=' * 60}\n  {name}  ({script})\n{'=' * 60}", flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        sys.exit(f"\nPipeline stopped: {name} failed (exit {result.returncode}).")


def main() -> None:
    args = parse_args()
    for name, script in STAGES:
        extra = []
        if name == "Scout":
            if args.query:
                extra += ["--query", args.query]
            if args.full:
                extra.append("--full")
        run_stage(name, script, extra)

    print(f"\n{'=' * 60}\n  Pipeline complete.\n{'=' * 60}", flush=True)
    if args.no_open:
        return
    dashboards = glob.glob(os.path.join(ROOT, "output", "dashboard-*.html"))
    if not dashboards:
        print("(no dashboard file found to open)")
        return
    latest = max(dashboards, key=os.path.getmtime)
    url = "file:///" + os.path.abspath(latest).replace(os.sep, "/")
    print(f"Opening {url}")
    webbrowser.open(url)


if __name__ == "__main__":
    main()

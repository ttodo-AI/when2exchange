# Workflow: summarize-text

## Purpose
Summarize a block of text (or a text file) into a few concise bullet points
using the Claude API.

## Inputs
- A path to a `.txt`/`.md` file, **or** raw text passed as an argument.
- `ANTHROPIC_API_KEY` in `.env`.

## Scripts to run
1. `python executions/summarize.py --file <path>`
   or
   `python executions/summarize.py --text "<the text>"`

   Optional flags:
   - `--bullets N`   number of bullet points (default 5)
   - `--model NAME`  Claude model id (default claude-haiku-4-5-20251001)

## What a good result looks like
- The script prints `N` bullet-point sentences capturing the main ideas.
- Exit code 0. On a missing key or API error it prints the reason to stderr
  and exits non-zero.

## Reporting
Relay the bullet points back to the user verbatim, and note which model and
source (file/text) were used.

# Workflow: draft-posts

## Purpose
Draft a copy-paste-ready **LinkedIn post** from each content brief (from the
Editor), written in the **user's own voice** learned from their real writing
samples. This is the final stage: Scout → Scorer → Editor → **Writer**.

## Inputs
- An Editor briefs JSON (`output/briefs-*.json`). By default the Writer
  auto-picks the most recent one.
- `voice/voice-samples.md` — 2-3 short, REAL samples of the user's writing under
  a `## Samples` heading. **Required.** The Writer refuses to run while the file
  still contains the `[PLACEHOLDER ...]` text.
- `ANTHROPIC_API_KEY` in `.env`.

## Scripts to run
```
python executions/writer.py
```
Optional flags:
- `--in <path>`     briefs JSON to use (default: latest `output/briefs-*.json`).
- `--voice <path>`  voice samples file (default `voice/voice-samples.md`).
- `--out <path>`    output file (default `output/drafts-<timestamp>.md`).
- `--model <id>`    Claude model (default `claude-sonnet-5`; voice matching
                    benefits from a stronger model than the other stages).

## Behavior
1. Loads the voice samples (errors out if missing or still placeholder).
2. Loads the briefs JSON.
3. Asks Claude to mimic the author's voice/language and draft one LinkedIn-ready
   post per brief (plain text, real line breaks, no markdown styling).
4. Saves a Markdown file where each post sits between `---` rules for clean
   copy-paste, and prints it.

## Notes
- The draft follows the **language of the voice samples**. If the samples are in
  English but the briefs are Korean (or vice-versa), expect the post in the
  samples' language — keep voice and audience language consistent.

## What a good result looks like
- A file with one LinkedIn-ready post per brief, in the user's voice, each
  copy-pasteable as-is.
- Exit code 0. Missing key / placeholder voice file / unreadable briefs /
  unparseable model reply exit non-zero with a clear message.

## Reporting
Report the briefs file used and where the drafts were saved, then show the drafts
so the user can copy them straight to LinkedIn.

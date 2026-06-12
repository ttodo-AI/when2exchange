#!/usr/bin/env python3
"""Dashboard — build a self-contained HTML view of the final scored + drafted output.

Merges the Writer's drafts JSON (score + link + brief + post) with the Scorer's
detail (relevance / content potential + reasons) and renders ONE standalone HTML
file: sortable, filterable, no external dependencies. Open it directly in a browser.

Standalone script. Run directly:
    python executions/dashboard.py
    python executions/dashboard.py --in output/drafts-2026-06-04_2238.json
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a standalone HTML dashboard.")
    p.add_argument("--in", dest="infile", default=None,
                   help="Writer drafts JSON (default: latest output/drafts-*.json).")
    p.add_argument("--scored", default=None,
                   help="Scorer JSON for the score breakdown (default: latest output/scored-*.json).")
    p.add_argument("--out", default=None,
                   help="Output HTML path (default output/dashboard-<ts>.html).")
    return p.parse_args()


def find_latest(pattern: str):
    matches = glob.glob(os.path.join("output", pattern))
    return max(matches, key=os.path.getmtime) if matches else None


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def embed_json(obj) -> str:
    """Serialize for safe inlining inside a <script> tag."""
    text = json.dumps(obj, ensure_ascii=False)
    text = text.replace("</", "<\\/")          # don't let "</script>" close the tag
    text = text.replace(chr(0x2028), "\\u2028")  # line separator: valid JSON, breaks JS
    text = text.replace(chr(0x2029), "\\u2029")  # paragraph separator
    return text


def main() -> None:
    args = parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    infile = args.infile or find_latest("drafts-*.json")
    if not infile:
        sys.exit("error: no Writer drafts JSON in output/. Run the writer first.")
    try:
        data = load_json(infile)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"error: could not read drafts file {infile}: {exc}")

    items = data.get("drafts", [])
    if not items:
        sys.exit(f"error: no drafts in {infile}.")

    # Enrich with the Scorer's relevance/content breakdown, matched by link.
    scored_file = args.scored or find_latest("scored-*.json")
    by_link = {}
    if scored_file and os.path.exists(scored_file):
        try:
            for a in load_json(scored_file).get("articles", []):
                if a.get("link"):
                    by_link[a["link"]] = a
        except (OSError, json.JSONDecodeError):
            pass

    merged = []
    for it in items:
        s = by_link.get(it.get("link"), {})
        merged.append({
            "title": it.get("title") or "(untitled)",
            "link": it.get("link") or "",
            "total": it.get("total"),
            "relevance": s.get("relevance"),
            "relevance_reason": s.get("relevance_reason"),
            "content_potential": s.get("content_potential"),
            "content_reason": s.get("content_reason"),
            "hook": it.get("hook"),
            "point": it.get("point"),
            "example": it.get("example"),
            "format": it.get("format"),
            "post": it.get("post") or "",
        })

    now = datetime.now(timezone.utc)
    out_path = args.out or os.path.join("output", f"dashboard-{now.strftime('%Y-%m-%d_%H%M')}.html")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    html = (
        HTML_TEMPLATE
        .replace("__DATA__", embed_json(merged))
        .replace("__SOURCE__", os.path.basename(infile))
        .replace("__GENERATED__", now.strftime("%Y-%m-%d %H:%M UTC"))
        .replace("__COUNT__", str(len(merged)))
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    print(f"Built dashboard for {len(merged)} item(s) from {infile}")
    print(f"Saved to {out_path}")
    print(f"Open it: file:///{os.path.abspath(out_path).replace(os.sep, '/')}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Content Dashboard</title>
<style>
  :root { --bg:#0f1115; --card:#1a1d24; --muted:#8b93a7; --fg:#e6e9f0;
          --line:#2a2f3a; --accent:#6ea8fe; --good:#3fb950; --mid:#d29922; --low:#6e7681; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Malgun Gothic",sans-serif; }
  header { padding:24px 20px 12px; border-bottom:1px solid var(--line); }
  h1 { margin:0 0 4px; font-size:20px; }
  .meta { color:var(--muted); font-size:13px; }
  .controls { display:flex; flex-wrap:wrap; gap:10px; align-items:center;
              padding:14px 20px; position:sticky; top:0; background:var(--bg);
              border-bottom:1px solid var(--line); z-index:5; }
  .controls input, .controls select { background:var(--card); color:var(--fg);
              border:1px solid var(--line); border-radius:8px; padding:8px 10px; font-size:14px; }
  #q { flex:1; min-width:180px; }
  .controls label { color:var(--muted); font-size:13px; }
  #count { color:var(--muted); font-size:13px; margin-left:auto; }
  main { padding:18px 20px 60px; display:grid; gap:16px;
         grid-template-columns:repeat(auto-fill,minmax(420px,1fr)); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:16px; }
  .badges { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px; }
  .badge { font-size:12px; padding:3px 8px; border-radius:999px; border:1px solid var(--line);
           color:var(--fg); white-space:nowrap; }
  .badge.total { font-weight:700; }
  .b-good { background:rgba(63,185,80,.15); border-color:var(--good); }
  .b-mid  { background:rgba(210,153,34,.15); border-color:var(--mid); }
  .b-low  { background:rgba(110,118,129,.15); border-color:var(--low); }
  .title { font-size:15px; font-weight:600; margin:2px 0 6px; line-height:1.4; }
  a.src { color:var(--accent); font-size:13px; text-decoration:none; }
  a.src:hover { text-decoration:underline; }
  .section { margin-top:12px; }
  .section h4 { margin:0 0 6px; font-size:12px; letter-spacing:.04em;
                text-transform:uppercase; color:var(--muted); }
  .kv { font-size:13.5px; line-height:1.5; margin:2px 0; }
  .kv b { color:var(--muted); font-weight:600; }
  .reason { color:var(--muted); font-size:12.5px; }
  .draft { white-space:pre-wrap; background:#11141a; border:1px solid var(--line);
           border-radius:8px; padding:12px; font-size:13.5px; line-height:1.6; margin-top:6px; }
  .copy { margin-top:8px; background:var(--accent); color:#06122b; border:none;
          border-radius:8px; padding:7px 12px; font-size:13px; font-weight:600; cursor:pointer; }
  .copy.done { background:var(--good); color:#03210c; }
  .empty { color:var(--muted); padding:40px 20px; text-align:center; }
</style>
</head>
<body>
<header>
  <h1>Content Dashboard</h1>
  <div class="meta">Source: __SOURCE__ &middot; Generated __GENERATED__ &middot; __COUNT__ items</div>
</header>
<div class="controls">
  <input id="q" type="search" placeholder="Filter by text (title / brief / draft)…">
  <label>Min total <input id="min" type="number" min="0" max="20" value="0" style="width:64px"></label>
  <label>Sort
    <select id="sort">
      <option value="total">Total score</option>
      <option value="relevance">Relevance</option>
      <option value="content_potential">Content potential</option>
      <option value="title">Title</option>
    </select>
  </label>
  <label><input id="dir" type="checkbox" checked> Desc</label>
  <span id="count"></span>
</div>
<main id="grid"></main>
<script>
const DATA = __DATA__;
const grid = document.getElementById('grid');
const q = document.getElementById('q');
const minEl = document.getElementById('min');
const sortEl = document.getElementById('sort');
const dirEl = document.getElementById('dir');
const countEl = document.getElementById('count');

function esc(s){ return (s==null?'':String(s)).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function band(v,max){ if(v==null) return 'b-low'; const r=v/max;
  return r>=0.8?'b-good':r>=0.6?'b-mid':'b-low'; }

function render(){
  const term = q.value.trim().toLowerCase();
  const min = Number(minEl.value)||0;
  const key = sortEl.value;
  const desc = dirEl.checked;
  let rows = DATA.filter(d => (d.total==null?0:d.total) >= min);
  if(term){
    rows = rows.filter(d => [d.title,d.hook,d.point,d.example,d.format,d.post]
      .some(x => (x||'').toLowerCase().includes(term)));
  }
  rows.sort((a,b)=>{
    let av=a[key], bv=b[key];
    if(key==='title'){ av=(av||'').toLowerCase(); bv=(bv||'').toLowerCase();
      return desc ? bv.localeCompare(av) : av.localeCompare(bv); }
    av=av==null?-1:av; bv=bv==null?-1:bv;
    return desc ? bv-av : av-bv;
  });
  countEl.textContent = rows.length + ' shown';
  if(!rows.length){ grid.innerHTML = '<div class="empty">No items match.</div>'; return; }
  grid.innerHTML = rows.map(d => `
    <div class="card">
      <div class="badges">
        <span class="badge total ${band(d.total,20)}">Total ${d.total==null?'?':d.total}/20</span>
        <span class="badge ${band(d.relevance,10)}">Relevance ${d.relevance==null?'?':d.relevance}/10</span>
        <span class="badge ${band(d.content_potential,10)}">Content ${d.content_potential==null?'?':d.content_potential}/10</span>
      </div>
      <div class="title">${esc(d.title)}</div>
      ${d.link?`<a class="src" href="${esc(d.link)}" target="_blank" rel="noopener">원문 보기 ↗</a>`:''}
      ${(d.relevance_reason||d.content_reason)?`<div class="section">
        ${d.relevance_reason?`<div class="reason">· 관련성: ${esc(d.relevance_reason)}</div>`:''}
        ${d.content_reason?`<div class="reason">· 콘텐츠: ${esc(d.content_reason)}</div>`:''}
      </div>`:''}
      <div class="section">
        <h4>Brief</h4>
        ${d.hook?`<div class="kv"><b>Hook</b> · ${esc(d.hook)}</div>`:''}
        ${d.point?`<div class="kv"><b>Point</b> · ${esc(d.point)}</div>`:''}
        ${d.example?`<div class="kv"><b>Example</b> · ${esc(d.example)}</div>`:''}
        ${d.format?`<div class="kv"><b>Format</b> · ${esc(d.format)}</div>`:''}
      </div>
      <div class="section">
        <h4>Draft</h4>
        <div class="draft">${esc(d.post)}</div>
        <button class="copy" data-post="${esc(d.post)}">Copy draft</button>
      </div>
    </div>`).join('');
}

grid.addEventListener('click', e => {
  const btn = e.target.closest('.copy');
  if(!btn) return;
  const text = btn.getAttribute('data-post')
    .replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"');
  navigator.clipboard.writeText(text).then(()=>{
    btn.textContent='Copied ✓'; btn.classList.add('done');
    setTimeout(()=>{ btn.textContent='Copy draft'; btn.classList.remove('done'); },1500);
  });
});
[q,minEl,sortEl,dirEl].forEach(el => el.addEventListener('input', render));
render();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()

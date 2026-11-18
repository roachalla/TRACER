"""SME / designer check list per lecturette (review only, free). Run from the project root.
  build_checklist.py
Collects, from work/site/textbook.json and work/mathpix/*.json: flagged or uncertain sentences, blind-reader stumbles,
and OCR corrections the vision pass was unsure about. Writes runs/<slug>/work/site/checklist.html and runs/index_checklist.html."""
import html, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNS = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs"
esc = html.escape
CSS = ("body{font:16px/1.6 system-ui;max-width:900px;margin:0 auto;padding:24px 16px;background:#fff;color:#1a1a1a}"
       "@media(prefers-color-scheme:dark){body{background:#151515;color:#eee}a{color:#8ab4ff}}"
       "h2{margin-top:28px;border-top:1px solid #8886;padding-top:14px}li{margin:6px 0}.q{font-family:Georgia,serif}.mut{color:#888;font-size:14px}"
       "code{font-size:13px;word-break:break-all}")
UNSURE = re.compile(r"uncertain|\?|unclear|guess|not sure|kept as", re.I)
summary = []
for d in sorted(RUNS.iterdir()) if RUNS.exists() else []:
    tb = d / "work/site/textbook.json"
    if not d.is_dir() or not tb.exists():
        continue
    T = json.load(open(tb, encoding="utf-8"))
    out, n_items = [], 0
    for r in T:
        items = []
        for s in r["sentences"]:
            if s["flags"] or s["uncertain"]:
                items.append(f"<li><b>Check sentence:</b> <span class=q>{esc(s['text'])}</span><br><span class=mut>"
                             f"{esc(' | '.join(s['flags']) or 'built on unresolved transcript text')}</span></li>")
        for x in r["stumbles"]:
            items.append(f"<li><b>Reader stumbled:</b> <code>{esc(x['quote'][:160])}</code><br><span class=mut>{esc(x['problem'])}</span></li>")
        mj = d / f"work/mathpix/c_{r['key']}.json"
        if mj.exists():
            for c in json.load(open(mj, encoding="utf-8")).get("fixed_changes", []):
                if UNSURE.search(c):
                    items.append(f"<li><b>Handwriting reading is a guess:</b> {esc(c)}</li>")
        if r.get("note") and r["note"] != "":
            items.append(f"<li class=mut>{esc(r['note'])}</li>")
        if items:
            out.append(f"<h2>{esc(r['heading'])} <span class=mut>(slide {r['key']})</span></h2><ul>{''.join(items)}</ul>")
            n_items += len(items)
    title = d.name.replace("_", " ")
    (d / "work/site/checklist.html").write_text(
        f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)} check list</title><style>{CSS}</style><h1>{esc(title)}: check list</h1>"
        f"<p class=mut>{n_items} items for the designer / SME. Review only; none of this appears in the student build.</p>"
        + "".join(out), encoding="utf8")
    summary.append((d.name, n_items))
(RUNS / "index_checklist.html").write_text(
    f"<!doctype html><meta charset=utf-8><title>Check lists</title><style>{CSS}</style><h1>Check lists</h1><ul>"
    + "".join(f"<li><a href='{n}/work/site/checklist.html'>{esc(n.replace('_', ' '))}</a> <span class=mut>{k} items</span></li>"
              for n, k in summary) + "</ul>", encoding="utf8")
for n, k in summary:
    print(f"{n[:40]:40} {k} items")

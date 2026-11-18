"""Index pages for a results folder. Free.
  build_index.py [RESULTS_DIR]     (default: <project>/runs)
Each sub-folder with work/site/ is one lecturette. They are grouped as
  W6_Z2_T1_...            -> Week 6 > Lecture 2 > lecturette 1
  ABCD123_09-15_T2_...    -> ABCD 123 > Session 09-15 > lecturette 2
  anything else           -> Lecturettes
(see scripts/course.py)
Writes index_review.html (review pages, flags, transcripts) and index_student.html (clean pages only) in RESULTS_DIR."""
import collections, html, json, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import course

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNS = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs"
tree = collections.defaultdict(lambda: collections.defaultdict(list))
n_total = 0
for d in sorted(RUNS.iterdir()) if RUNS.exists() else []:
    site = d / "work/site"
    if not site.is_dir():
        continue
    top, sub, t, title = course.place(d.name) or ("Lecturettes", "", 0, d.name)
    title = title.replace("_", " ")
    try:
        title = json.load(open(site / "structure.json", encoding="utf-8")).get("lecturette_title") or title
    except OSError:
        pass
    tree[top][sub].append(dict(t=t, title=title, slug=d.name, review=(site / "review_textbook.html").exists(),
                               final=(site / "final_textbook.html").exists(), page=(site / "review_draft.html").exists(),
                               check=(site / "checklist.html").exists(), tfinal=(site / "final_transcript.html").exists(),
                               treview=(site / "review_transcript.html").exists()))
    n_total += 1

CSS = (":root{--teal:#004D6C;--teal2:#006389;--cyan:#0D9EC4;--warm:#FBAE40;--ink:#1c2a31;--paper:#fff;--line:#cfe0e7;--mut:#5b6b73}"
       "@media(prefers-color-scheme:dark){:root{--teal:#0b6a8d;--teal2:#52bde0;--ink:#e6eef2;--paper:#10191e;--line:#2c4450;--mut:#a3b5be}}"
       "body{margin:0;font:18px/1.6 system-ui,'Segoe UI',Calibri,sans-serif;background:var(--paper);color:var(--ink)}"
       "h1{background:var(--teal);color:#fff;margin:0;padding:34px max(16px,calc((100% - 820px)/2)) 28px;border-bottom:6px solid var(--warm);font-size:32px}"
       "main{max-width:820px;margin:0 auto;padding:8px 16px 48px}"
       "h2{color:var(--teal2);margin-top:36px;border-bottom:2px solid var(--line);padding-bottom:4px}h3{margin:18px 0 4px}"
       "a{color:var(--teal2)}li{margin:5px 0}.mut{color:var(--mut);font-size:14px}")


PACK = {"final_textbook.html": "1 Main deliverables", "final_textbook_teaching.html": "1 Main deliverables",
        "final_transcript.html": "1 Main deliverables", "review_textbook.html": "2 Review resources",
        "review_transcript.html": "2 Review resources", "review_draft.html": "2 Review resources"}


def href(slug, name):
    """link to the sorted copy in the run's three folders when it exists, else the working copy"""
    from urllib.parse import quote
    d = PACK.get(name)
    path = f"{slug}/{d}/{name}" if d and (RUNS / slug / d / name).exists() else f"{slug}/work/site/{name}"
    return html.escape(quote(path), quote=True)  # folder names are user-chosen: percent-encode, then escape for the attribute


def build(student):
    out = [f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>{'Course text' if student else 'Review index'}</title><style>{CSS}</style>"
           f"<header><h1>{'Course text' if student else 'Review index'}</h1></header><main>"]
    for top in sorted(tree, key=lambda x: (not x.startswith("Week"), x)):
        out.append(f"<h2>{html.escape(top)}</h2>")
        for sub in sorted(tree[top]):
            if sub:
                out.append(f"<h3>{html.escape(sub)}</h3>")
            out.append("<ul>")
            for r in sorted(tree[top][sub], key=lambda r: (r["t"], r["slug"])):
                label = html.escape((f"{r['t']}. " if r["t"] else "") + r["title"])
                if student:
                    if r["final"]:
                        out.append(f"<li><a href='{href(r['slug'], 'final_textbook.html')}'>{label}</a></li>")
                    elif r["tfinal"]:
                        out.append(f"<li><a href='{href(r['slug'], 'final_transcript.html')}'>{label}</a> <span class=mut>(transcript)</span></li>")
                    continue
                links = []
                if r["treview"]:
                    links.append(f"<a href='{href(r['slug'], 'review_transcript.html')}'>transcript review</a>")
                if r["tfinal"]:
                    links.append(f"<a href='{href(r['slug'], 'final_transcript.html')}'>student transcript</a>")
                if r["review"]:
                    links.append(f"<a href='{href(r['slug'], 'review_textbook.html')}'>review</a>")
                if r["final"]:
                    links.append(f"<a href='{href(r['slug'], 'final_textbook.html')}'>student view</a>")
                if (RUNS / r["slug"] / "work/site/final_textbook_teaching.html").exists():
                    links.append(f"<a href='{href(r['slug'], 'final_textbook_teaching.html')}'>teaching edition</a>")
                if r["check"]:
                    links.append(f"<a href='{href(r['slug'], 'checklist.html')}'>check list</a>")
                if r["page"]:
                    links.append(f"<a href='{href(r['slug'], 'review_draft.html')}'>draft</a>")
                out.append(f"<li>{label} <span class=mut>- {' | '.join(links) if links else 'not processed yet'}</span></li>")
            out.append("</ul>")
    return "".join(out) + "</main>"


RUNS.mkdir(parents=True, exist_ok=True)
(RUNS / "index_review.html").write_text(build(False), encoding="utf8")
(RUNS / "index_student.html").write_text(build(True), encoding="utf8")
print(f"wrote index_review.html, index_student.html in {RUNS}; {n_total} lecturettes")

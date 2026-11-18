"""Sort a finished run into three folders a person can navigate. Free. Run from inside a run folder (last stage).
  1 Main deliverables     what students and instructors use: textbook (web, teaching edition, Word), transcript, captions
  2 Review resources      the review pages with flags, for the designer and SME
  3 Audit and logs        machine-readable records: every decision, check, spend line, plus a short summary
work/ stays as the pipeline's working area (re-runs read from it); these folders are refreshed copies."""
import collections, json, pathlib, re, shutil, sys

run, site = pathlib.Path("."), pathlib.Path("work/site")
OUT = set(sys.argv[sys.argv.index("--outputs") + 1].split(",")) if "--outputs" in sys.argv else {"textbook", "teaching", "word", "transcript"}
WANT = {"final_textbook.html": "textbook", "final_textbook_teaching.html": "teaching", "textbook.docx": "word",
        "textbook_teaching.docx": "word", "final_transcript.html": "transcript", "transcript.txt": "transcript", "captions.vtt": "transcript"}
FOLD = {"main": run / "1 Main deliverables", "review": run / "2 Review resources", "audit": run / "3 Audit and logs"}
MAIN = ["final_textbook.html", "final_textbook_teaching.html", "textbook.docx", "textbook_teaching.docx",
        "final_transcript.html", "transcript.txt", "captions.vtt"]
REVIEW = ["review_center.html", "review_todo.md", "review_textbook.html", "review_transcript.html", "review_draft.html"]
AUDIT = [site / "textbook.json", site / "structure.json", site / "teaching.json", site / "captions_to_check.json", site / "review_edits.json",
         site / "figure_sources.json",
         site / "redraw_candidates.json", run / "work/profile.json", run / "work/gaps/gaps.json", run / "work/gaps/resolved.json",
         run / "work/speakers/speakers.json", run / "work/llm_spend.jsonl", run / "work/frames_meta.json"]

TRANSCRIPT = ["final_transcript.html", "transcript.txt", "captions.vtt"]
_rv = site / "review_edits.json"
_R = json.load(open(_rv, encoding="utf8")) if _rv.exists() else {}
# raw speech recognition reads as fact once flags are hidden: the transcript and captions stay with the review material until a
# person approves them (apply_review.py --approve-transcript, or the professor's review being applied)
T_OK = bool(_R.get("transcript_approved") or _R.get("professor_reviewed"))

# Only what TRACER itself put in these folders is replaced. Anything else a person saves there (feedback files, notes, an edited
# Word file) is left alone. Each folder lists what TRACER wrote in .tracer_files; older runs without that list are cleaned by name.
KNOWN = set(MAIN) | set(REVIEW) | {p.name for p in AUDIT} | {"img", "slide_ocr_raw", "audit_summary.md", "README.txt"}


def clear(folder):
    folder.mkdir(exist_ok=True)
    mf = folder / ".tracer_files"
    names = set(json.loads(mf.read_text(encoding="utf8"))) if mf.exists() else KNOWN
    for n in names:
        p = folder / n
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    return []


written = {k: clear(f) for k, f in FOLD.items()}


def copy_pages(names, key):
    dst, n = FOLD[key], 0
    for name in names:
        p = site / name
        if not p.exists():
            continue
        if p.suffix == ".html":  # pages moved one folder up: fix links to the results index
            t = p.read_text(encoding="utf8").replace("'../../../index_", "'../../index_")
            (dst / name).write_text(t, encoding="utf8")
        else:
            shutil.copy2(p, dst / name)
        written[key].append(name); n += 1
    if n and (site / "img").exists() and any((site / x).suffix == ".html" for x in names if (site / x).exists()) and not (dst / "img").exists():
        shutil.copytree(site / "img", dst / "img"); written[key].append("img")
    return n


main_list = [m for m in MAIN if WANT[m] in OUT or (m == "final_transcript.html" and not (site / "textbook.json").exists())]
n_main = copy_pages([m for m in main_list if T_OK or m not in TRANSCRIPT], "main")
n_rev = copy_pages(REVIEW + ([m for m in main_list if m in TRANSCRIPT] if not T_OK else []), "review")
n_aud = 0
for p in AUDIT:
    if p.exists():
        shutil.copy2(p, FOLD["audit"] / p.name); n_aud += 1; written["audit"].append(p.name)
if (run / "work/mathpix_raw").exists():
    shutil.copytree(run / "work/mathpix_raw", FOLD["audit"] / "slide_ocr_raw"); written["audit"].append("slide_ocr_raw")

# short human-readable summary of the audit trail
spend = collections.Counter(); calls = collections.Counter()
sp = run / "work/llm_spend.jsonl"
for line in (sp.read_text().splitlines() if sp.exists() else []):
    d = json.loads(line); lab = (d.get("label") or "gap resolution").split(" s")[0].split(" figure")[0].split(" c_")[0]
    lab = re.sub(r"\s+\d+$", "", lab)  # "gap resolution 12" -> "gap resolution"
    spend[lab] += d["usd"]; calls[lab] += 1
lines = ["# Audit summary", "", f"Run folder: {run.resolve().name}", ""]
prof = run / "work/profile.json"
if prof.exists():
    p = json.load(open(prof)); lines += [f"Video type: {p['type']} ({p['name']}), {p['how']}. Processing: {p['mode']}.", ""]
tbf = site / "textbook.json"
if tbf.exists():
    tb = json.load(open(tbf, encoding="utf8"))
    sents = [s for r in tb for s in r["sentences"]]
    lines += ["## Checks", f"- Sentences: {len(sents)}; flagged for review: {sum(bool(s['flags'] or s['uncertain']) for s in sents)}",
              f"- Peer-review findings: {sum(len(r['stumbles']) for r in tb)}",
              f"- Equations repaired against the slide image: {sum(len(r.get('repairs', [])) for r in tb)}",
              f"- Equations where the two slide readers disagree: {sum(sum(not c['agrees'] for c in r.get('eq_check', [])) for r in tb)}",
              f"- Figures described: {sum(len(r['fig_meta']) for r in tb)}", ""]
lines += ["## Claude spend logged in this folder (all runs, including re-runs)", "", "| Step | Calls | USD |", "|---|---|---|"]
lines += [f"| {k} | {calls[k]} | {v:.4f} |" for k, v in spend.most_common()] + [f"| **Total** | {sum(calls.values())} | **{sum(spend.values()):.4f}** |", "",
          "Files in this folder: textbook.json (every sentence with its sources and flags), resolved.json (every transcript fix and its "
          "evidence), gaps.json (where the speech engines disagreed), llm_spend.jsonl (every paid call), slide_ocr_raw (Mathpix "
          "before correction)."]
(FOLD["audit"] / "audit_summary.md").write_text("\n".join(lines) + "\n", encoding="utf8")
written["audit"].append("audit_summary.md")
for k, f in FOLD.items():
    (f / ".tracer_files").write_text(json.dumps(sorted(set(written[k]))), encoding="utf8")
draft = "" if _R.get("professor_reviewed") else " (marked Draft until the instructor's review is applied)"
tnote = ("transcript and captions (captions.vtt)" if T_OK else
         "the transcript and captions are in 2 Review resources until a person approves them")
(run / "README.txt").write_text(
    f"1 Main deliverables  - for students and instructors: textbook web pages and Word versions{draft}; {tnote}\n"
    "2 Review resources   - start with review_center.html: everything that needs a person's decision\n"
    "3 Audit and logs     - records of every decision, check and paid call; start with audit_summary.md\n"
    "work                 - the pipeline's working files (needed for re-runs)\n"
    "Files you add to these folders are kept when TRACER refreshes them.\n", encoding="utf8")
print(f"packaged: {n_main} deliverables, {n_rev} review pages, {n_aud} audit files (+ audit_summary.md)"
      + ("" if T_OK else "; transcript and captions kept in Review until approved"))

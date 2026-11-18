"""Apply review-center feedback to a finished run. Free: the rebuild reuses cached model calls.
Run from inside a run folder:
  python apply_review.py "TRACER feedback - designer - <run>.json" ["TRACER feedback - professor - <run>.json"] [--no-rebuild]
  python apply_review.py --approve-transcript      a person checked the transcript and captions: move them to Main deliverables

- Corrected sentences and equations ("Needs a fix" with an edited text) replace the originals in every format.
- Decisions and comments are kept in work/site/review_edits.json. The next review center shows earlier decisions, so after the
  designer's pass it becomes the professor's version: items the designer settled are marked done, questions stay open.
- Everything that needs a person (fixes described in words, questions, comments) is listed in review_todo.md.
- Once a professor's feedback file is applied, the student pages and Word files no longer say "Draft"."""
import json, pathlib, re, subprocess, sys

S = pathlib.Path(__file__).parent
site = pathlib.Path("work/site")
files = [pathlib.Path(a) for a in sys.argv[1:] if not a.startswith("--")]
APPROVE_T = "--approve-transcript" in sys.argv  # a person checked the transcript and captions: they move to Main deliverables
if APPROVE_T and not files:  # approve the transcript only (also for transcript-only runs, which have no review center)
    ef = site / "review_edits.json"
    R = json.load(open(ef, encoding="utf8")) if ef.exists() else {}
    R["transcript_approved"] = True
    site.mkdir(parents=True, exist_ok=True)
    with open(ef, "w", encoding="utf8") as fh:
        json.dump(R, fh, indent=1)
    subprocess.run([sys.executable, str(S / "package_outputs.py")])
    sys.exit("Transcript and captions approved and moved to '1 Main deliverables'.")
if not files or not (site / "textbook.json").exists():
    sys.exit(__doc__)
fb = [json.load(open(f, encoding="utf8")) for f in files]
for f, x in zip(files, fb):
    if not x.get("tracer_feedback"):
        sys.exit(f"{f} is not a TRACER feedback file")
    if x.get("lecture") and x["lecture"] != pathlib.Path.cwd().name:
        print(f"WARNING: {f.name} was saved for '{x['lecture']}', not this folder ('{pathlib.Path.cwd().name}')")
fb.sort(key=lambda x: x.get("role") != "designer")  # designer first, professor's word last

ef = site / "review_edits.json"
R = json.load(open(ef, encoding="utf8")) if ef.exists() else {}
R.setdefault("decisions", {}); R.setdefault("comments", []); R.setdefault("edits", []); R.setdefault("designer_email", "")
edits = {e["anchor"]: e for e in R["edits"]}
new_edits = 0
for x in fb:
    if x.get("role") == "designer" and x.get("designer_email"):
        R["designer_email"] = x["designer_email"]
    if x.get("role") == "professor":  # the instructor's pass is applied: student pages stop saying "Draft"
        R["professor_reviewed"] = True
if APPROVE_T:
    R["transcript_approved"] = True
    for cid, d in x.get("decisions", {}).items():
        R["decisions"][cid] = d
        a, txt, orig = d.get("anchor", ""), (d.get("text") or "").strip(), (d.get("original") or "").strip()
        if d.get("status") == "fix" and re.fullmatch(r"[xe]\d+-\d+", a) and orig and txt and txt != orig:
            if edits.get(a, {}).get("text") != txt:
                new_edits += 1
            edits[a] = dict(anchor=a, text=txt, before=orig, by=d.get("by") or d.get("role", "reviewer"), role=d.get("role"), id=cid)
    have = {c["id"]: i for i, c in enumerate(R["comments"])}
    for c in x.get("comments", []):
        if c["id"] in have:
            R["comments"][have[c["id"]]] = c
        else:
            R["comments"].append(c)
R["edits"] = list(edits.values())
json.dump(R, open(ef, "w", encoding="utf8"), indent=1)

# the list for the designer: everything a script cannot apply
ds = R["decisions"].values()
lines = ["# Review to-do", "", f"Feedback files: {', '.join(f.name for f in files)}", ""]
lines += ["## Applied automatically", ""] + ([f"- {e['anchor']} ({e['by']}):  \n  was: `{e['before']}`  \n  now: `{e['text']}`" for e in R["edits"]] or ["- nothing"]) + [""]
auto = {e.get("id") for e in R["edits"]}
manual = [d for cid, d in R["decisions"].items() if d.get("status") == "fix" and cid not in auto]
lines += ["## Fixes to make by hand", ""] + ([f"- {d.get('where', '')}: {d.get('text') or '(no text)'}  \n  about: {d.get('quote') or d.get('question', '')[:120]}"
                                              for d in manual] or ["- none"]) + [""]
asks = [d for d in ds if d.get("status") == "ask"]
lines += ["## Questions", ""] + ([f"- {d.get('where', '')} ({d.get('role')}): {d.get('text') or '(no note)'}  \n  item: {d.get('question', '')[:160]}"
                                  for d in asks] or ["- none"]) + [""]
lines += ["## Comments", ""] + ([f"- {c.get('where', '')} ({c.get('by') or c.get('role')}): {c.get('text') or '(empty)'}  \n  on: \"{(c.get('quote') or '')[:120]}\""
                                 for c in R["comments"]] or ["- none"]) + [""]
(site / "review_todo.md").write_text("\n".join(lines), encoding="utf8")
print(f"{len(R['decisions'])} decisions, {len(R['edits'])} text/equation edits ({new_edits} new), {len(manual)} fixes by hand, "
      f"{len(asks)} questions, {len(R['comments'])} comments. Wrote {ef} and {site / 'review_todo.md'}")

if "--no-rebuild" not in sys.argv:
    teach = json.load(open(site / "teaching.json", encoding="utf8")) if (site / "teaching.json").exists() else {}
    has_teach = any(teach.values()) if teach else False
    steps = [[sys.executable, S / "textbook.py"] + ([] if has_teach else ["--no-teaching"]),
             [sys.executable, S / "make_docx.py"] + (["--teaching"] if has_teach else []),
             [sys.executable, S / "package_outputs.py"]]
    for cmd in steps:
        print("---", pathlib.Path(cmd[1]).name)
        out = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf8", errors="replace")
        tail = [l for l in out.stdout.splitlines() if re.search(r"SPEND|reviewer edits|packaged|wrote|Wrote", l)]
        print("\n".join(tail[-4:]))
        if out.returncode:
            print(out.stderr[-2000:]); sys.exit(f"{cmd[1]} failed")
    print("Rebuilt. Send the professor the review center in '2 Review resources' (it now shows the designer's decisions).")

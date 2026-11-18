"""Vision correction of Mathpix OCR (paid, Claude Sonnet). Run from inside a run folder.
  fix_ocr.py [--dry-run]
Raw Mathpix output is backed up once to work/mathpix_raw/; work/mathpix/*.json "text" and *.md are replaced with the
corrected version. Guards: numbers of two or more digits must be the same before and after (none lost, none added), and a
correction that adds anything the model says it cannot see is rejected; rejected corrections are listed for review.
Writes work/mathpix_raw/.fixocr_done when every slide was handled (run_video.py uses it to know the step finished)."""
import hashlib, json, pathlib, re, shutil, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import llm_util

DRY = "--dry-run" in sys.argv
MODEL = llm_util.MODELS["fix_ocr"]
mp, raw, fr = pathlib.Path("work/mathpix"), pathlib.Path("work/mathpix_raw"), pathlib.Path("work/frames_final")
if not raw.exists():
    tmp = pathlib.Path("work/mathpix_raw.tmp")  # copy to a temporary name first: a crash mid-copy must not look like a finished backup
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(mp, tmp)
    tmp.rename(raw)
DONE = raw / ".fixocr_done"
DONE.unlink(missing_ok=True)
TOOL = {"name": "fix", "description": "Return the corrected slide transcription.", "strict": True,
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["corrected", "changes"],
            "properties": {"corrected": {"type": "string", "description": "Corrected markdown with LaTeX ($...$ / $$...$$)"},
                           "changes": {"type": "array", "items": {"type": "string"}, "description": "each: draft text -> what the image shows"}}}}
SYSTEM = ("You correct OCR of a handwritten/typed lecture slide. Compare the draft to the image. Fix only what the image clearly shows "
          "differently from the draft (misread symbols, subscripts, stray fragments, broken LaTeX). Never change numbers. "
          "Do not add content not visible. Keep LaTeX valid.")
import collections
# real quantities (2+ digits or decimals, e.g. 2.07, 20.7, 10) must match in both directions; single digits (subscripts,
# 8-vs-rho misreads) may change
digits = lambda s: collections.Counter(n for n in re.findall(r"\d+\.?\d*", s) if len(n) > 1)
INFERRED = re.compile(r"not visible|not shown|inferred|assum|cut off|hidden|cannot see|can't see|guess", re.I)
n_est = 0
for jf in sorted(raw.glob("*.json")):
    with open(jf, encoding="utf-8") as fh:
        d = json.load(fh)
    draft = d.get("text", "")
    if "$" not in draft:
        print(f"{jf.stem}: no math, skipped"); continue
    # cache keyed by image + draft + model, so re-extracted frames with the same name are never served a stale fix
    h = hashlib.sha256((fr / f"{jf.stem}.jpg").read_bytes() + draft.encode() + MODEL.encode()).hexdigest()[:16]
    cf = llm_util.CACHE / f"fix_{jf.stem}_{h}.json"
    old = llm_util.CACHE / f"fix_{jf.stem}.json"  # older runs cached by name only: reuse only if that fix is newer than the OCR it fixed
    if not cf.exists() and old.exists() and MODEL == "claude-sonnet-5-5" and jf.stat().st_mtime <= old.stat().st_mtime:
        cf = old
    if DRY:
        n_est += 1; print(f"{jf.stem}: would call (~$0.012)"); continue
    res, _ = llm_util.call(MODEL, SYSTEM, TOOL, "Draft OCR:\n" + draft, max_tokens=3000, label=f"fix_ocr {jf.stem}",
                           images=[fr / f"{jf.stem}.jpg"], cache_file=cf)
    lost = digits(draft) - digits(res["corrected"]) if res else None
    added = digits(res["corrected"]) - digits(draft) if res else None
    guessed = [c for c in (res or {}).get("changes", []) if INFERRED.search(c)]
    why = ("no result" if not res else f"numbers lost: {sorted(lost)}" if lost else f"numbers added: {sorted(added)}" if added else
           "it adds something it says is not visible: " + "; ".join(guessed) if guessed else "")
    if why:  # keep the original reading; the review page lists the rejected correction so a person can look
        d["fix_rejected"] = why
        with open(mp / jf.name, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        if (raw / f"{jf.stem}.md").exists():  # an earlier run may have replaced the text: restore the original reading
            shutil.copy2(raw / f"{jf.stem}.md", mp / f"{jf.stem}.md")
        print(f"{jf.stem}: correction REJECTED ({why}); original kept and flagged for review"); continue
    d["text"] = res["corrected"]; d["fixed_changes"] = res["changes"]; d.pop("fix_rejected", None)
    with open(mp / jf.name, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    (mp / f"{jf.stem}.md").write_text(res["corrected"], encoding="utf-8")
    print(f"{jf.stem}: fixed ({len(res['changes'])} changes)")
    for c in res["changes"]: print("    -", c)
if DRY:
    print(f"[dry-run] {n_est} calls, est ${n_est * 0.012:.3f}")
else:
    DONE.write_text("ok", encoding="utf8")

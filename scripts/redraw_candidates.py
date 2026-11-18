"""Which figures are worth redrawing as clean vector drawings? Free: uses data the pipeline already has, no API calls.
  redraw_candidates.py RUN_DIR [RUN_DIR ...]      (folders that contain work/site/textbook.json)
A figure qualifies only if all four tests pass:
  kind        simple or complex diagram, or a chart with few elements; never photos/micrographs, decorative or textual figures
  legibility  small, blurry or low-contrast (a sharp figure gains nothing from a redraw and only adds risk)
  certainty   the describer asked no SME question about it (unreadable labels would have to be guessed)
  importance  the text points to it (placed inline), or it is very hard to read
Writes RUN/work/site/redraw_candidates.json and prints a table."""
import json, pathlib, re, sys
import numpy as np
from PIL import Image, ImageFilter

PHOTO = re.compile(r"\b(photo|photograph|micrograph|microscope|picture of|video still|room|person|instructor|professor)\b", re.I)
DENSE = re.compile(r"\b(scatter|data points|measured data|many curves|histogram|contour|spectrum|map)\b", re.I)


def legibility(path):
    im = Image.open(path).convert("L")
    w, h = im.size
    a = np.asarray(im, dtype=np.float32)
    sharp = float(np.asarray(im.filter(ImageFilter.FIND_EDGES), dtype=np.float32).var() ** 0.5)
    contrast = float(np.percentile(a, 98) - np.percentile(a, 2))
    return w, h, sharp, contrast


def judge(fm, placed, w, h, sharp, contrast):
    fn = fm.get("function", "")
    text = " ".join([fm.get("alt", ""), fm.get("caption", "")] + list(fm.get("long_description") or []))
    if fn in ("decorative", "textual"):
        return False, f"{fn} figure"
    if PHOTO.search(text):
        return False, "photo or real-world image"
    if fn == "chart" and DENSE.search(text):
        return False, "data-heavy chart (a redraw could shift values)"
    if fn not in ("simple", "complex", "chart", "process"):
        return False, f"kind '{fn}'"
    if fm.get("sme_questions"):
        return False, f"{len(fm['sme_questions'])} unclear label(s): would have to guess"
    small, blurry, faint = max(w, h) < 420, sharp < 28, contrast < 150
    poor = [n for n, b in (("small", small), ("blurry", blurry), ("low contrast", faint)) if b]
    if not poor:
        return False, "already legible"
    if not placed and len(poor) < 2:
        return False, "not referenced in the text and only mildly hard to read"
    return True, "redraw: " + ", ".join(poor) + ("" if placed else " (unplaced)")


rows = []
for run in map(pathlib.Path, sys.argv[1:]):
    site = run / "work/site"
    tb = json.load(open(site / "textbook.json", encoding="utf8"))
    out = []
    for r in tb:
        for f, fm in r.get("fig_meta", {}).items():
            p = site / "img" / f
            if not p.exists():
                continue
            w, h, sharp, contrast = legibility(p)
            ok, why = judge(fm, f in r.get("placed_figs", []), w, h, sharp, contrast)
            out.append(dict(section=r["n"], figure=f, function=fm.get("function"), w=w, h=h, sharp=round(sharp, 1),
                            contrast=round(contrast), placed=f in r.get("placed_figs", []), redraw=ok, reason=why, alt=fm.get("alt", "")))
    json.dump(out, open(site / "redraw_candidates.json", "w"), indent=1)
    rows += [(run.name[:30], x) for x in out]

print(f"{'lecture':30} {'figure':22} {'kind':9} {'size':>9} {'sharp':>5} {'contr':>5}  verdict")
for name, x in rows:
    print(f"{name:30} {x['figure'][:22]:22} {str(x['function'])[:9]:9} {x['w']:>4}x{x['h']:<4} {x['sharp']:5} {x['contrast']:5}  "
          f"{'YES' if x['redraw'] else 'no '} {x['reason']}")
n = sum(x["redraw"] for _, x in rows)
print(f"\n{n} of {len(rows)} figures qualify for a redraw")

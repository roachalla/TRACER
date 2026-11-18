"""Group figure crops that show the same thing (the same slide filmed in several frames, a presenter or pointer in front of it,
crops with different margins) and keep one copy per group: the largest, sharpest one. No API calls."""
import numpy as np
from PIL import Image, ImageFilter

H = 40  # comparison height in pixels


def _load(path):
    im = Image.open(path).convert("L")
    w, h = im.size
    small = np.asarray(im.resize((max(8, round(w * H / h)), H)).filter(ImageFilter.GaussianBlur(1)), dtype=np.float32)
    edges = np.asarray(im.filter(ImageFilter.FIND_EDGES), dtype=np.float32)
    return dict(img=small, aspect=w / max(h, 1), quality=w * h * (edges.var() ** 0.5))


def _ncc(a, b):
    a = a - a.mean(); b = b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))


def similarity(a, b, max_shift=4):
    """best normalized correlation when the narrower crop slides across the wider one (different margins), with a small
    vertical shift (different top/bottom margins)"""
    x, y = (a, b) if a.shape[1] <= b.shape[1] else (b, a)
    best = -1.0
    for dy in range(-max_shift, max_shift + 1):
        ys = slice(max(0, dy), H + min(0, dy)); yb = slice(max(0, -dy), H + min(0, -dy))
        for dx in range(0, y.shape[1] - x.shape[1] + 1, 2):
            best = max(best, _ncc(x[ys], y[yb, dx:dx + x.shape[1]]))
    return best


def dedupe(paths, corr=0.45, max_aspect_ratio=2.2):
    """paths in reading order -> (kept paths in order of first appearance, {dropped: kept}).
    corr 0.45: on one test lecture (46 crops of 5 slide diagrams + 2 board sketches) copies of one diagram scored 0.47-0.84
    (pointer across it, different margins) and different diagrams 0.26-0.33."""
    info = {p: _load(p) for p in paths}
    groups = []

    def close(a, b):
        r = max(a["aspect"], b["aspect"]) / min(a["aspect"], b["aspect"])
        return r <= max_aspect_ratio and similarity(a["img"], b["img"]) >= corr
    for p in paths:
        a = info[p]
        for g in groups:
            if any(close(a, info[q]) for q in g):
                g.append(p); break
        else:
            groups.append([p])
    kept, mapping = [], {}
    for g in groups:
        best = max(g, key=lambda p: info[p]["quality"])
        kept.append(best)
        mapping.update({p: best for p in g if p != best})
    return kept, mapping

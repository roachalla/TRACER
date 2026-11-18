"""Stage 1: frames. Run with the envs/whisper python (needs cv2) from inside a run folder.
  extract_frames.py VIDEO [--step 20] [--crop 240,0,1440,1080] [--webcam 1600,0,320,180] [--threshold 2.0]

Writes work/frames_crop/c_NNNN.jpg (every STEP seconds, slide area only, webcam corner whited out) and
work/frames_final/ (the last frame of each slide state: a frame is kept when the NEXT frame differs by more than
THRESHOLD percent of pixels, plus the final frame)."""
import argparse, glob, pathlib, shutil, subprocess
import cv2
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("--step", type=float, default=20.0)
ap.add_argument("--crop", default="auto", help="x,y,w,h of the slide area in the full frame, or 'auto' to detect it")
ap.add_argument("--webcam", default="auto", help="x,y,w,h of the webcam overlay to white out (full-frame coordinates), a corner "
                "(top-right, top-left, bottom-right, bottom-left; 'auto' = top-right), or 'none'")
ap.add_argument("--threshold", type=float, default=2.0, help="percent of changed pixels that starts a new slide state")
ap.add_argument("--mode", choices=["slides", "board"], default="slides",
                help="slides: frame every STEP s, keep end-states. board: chalk/whiteboard with a moving camera and a presenter "
                     "in front of the board; dense frames, presenter removed by a temporal median, one clean board per window")
ap.add_argument("--window", type=float, default=20.0, help="board mode: target section length in seconds (snapped to sentence ends)")
a = ap.parse_args()


def board_mode(video, crop, window):
    """Board lectures. Frames every 2 s. Within one camera shot the board content is static while the presenter moves, so the
    per-pixel median of the last few frames shows the board without the presenter ('clean plate'). Windows of about WINDOW
    seconds end at sentence ends (from the Whisper segments when present); each window gets the clean plate at its end, so an
    equation first appears in the window where it was written. New ink between windows is measured with a background-difference
    ink mask (idea from ClassTranscribe, ASEE 2026). Writes frames_crop/ (dense), frames_final/c_NNNN.jpg (one per window) and
    work/frames_meta.json (mode, windows with times, new-ink percent)."""
    import json
    STEP = 2.0
    crop_dir, final_dir = pathlib.Path("work/frames_crop"), pathlib.Path("work/frames_final")
    for d in (crop_dir, final_dir):
        shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    cx, cy, cw, ch = (int(v) for v in crop.split(","))
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", video, "-vf", f"fps=1/{STEP:g},crop={cw}:{ch}:{cx}:{cy}",
                    "-q:v", "2", str(crop_dir / "d_%05d.jpg")], check=True)
    files = sorted(glob.glob(str(crop_dir / "d_*.jpg")))
    times = [i * STEP + STEP / 2 for i in range(len(files))]  # ffmpeg fps filter samples mid-interval
    small = [cv2.resize(cv2.imread(f, 0), (320, 240)) for f in files]
    # camera shots: a large global change between consecutive frames (cut, pan, zoom)
    shot, s = [], 0
    for i in range(len(files)):
        if i and (cv2.absdiff(small[i], small[i - 1]) > 40).mean() > 0.35:
            s += 1
        shot.append(s)

    def ink(img):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return cv2.absdiff(g, cv2.medianBlur(g, 31)) > 28

    INK = [int(ink(cv2.resize(cv2.imread(f), (320, 240))).sum()) for f in files]

    def plate(i, back=6):
        """best single frame among the last BACK frames up to i: the one with the most visible chalk/marker (sharp, presenter
        out of the way, most content). A per-pixel median was tried first and ghosted when the camera drifted."""
        idx = list(range(max(0, i - back + 1), i + 1))
        j = max(idx, key=lambda k: (INK[k], k))
        return cv2.imread(files[j]), j

    # window ends: every ~WINDOW s, moved to the nearest sentence end in the Whisper segments
    seg_f = pathlib.Path("work/asr/whisper_segments.json")
    ends = [x["end"] for x in json.load(open(seg_f, encoding="utf8"))] if seg_f.exists() else []
    dur = len(files) * STEP
    cuts, t = [], 0.0
    while t + window < dur - window / 2:
        target = t + window
        near = [e for e in ends if target - window / 3 <= e <= target + window / 3]
        t = min(near, key=lambda e: abs(e - target)) if near else target
        cuts.append(t)
    bounds = [0.0] + cuts + [dur]
    meta, prev_ink = [], None
    for w, (t0, t1) in enumerate(zip(bounds, bounds[1:]), 1):
        i = max(j for j in range(len(files)) if times[j] <= max(t1, times[0]))
        img, n = plate(i)
        m = ink(img)
        new = 100.0 * (m & ~cv2.dilate(prev_ink.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)).mean() if prev_ink is not None and prev_ink.shape == m.shape else 100.0 * m.mean()
        prev_ink = m
        key = f"{w:04d}"
        cv2.imwrite(str(final_dir / f"c_{key}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        meta.append(dict(key=key, t0=round(t0, 1), t1=round(t1, 1), frame_t=round(times[n], 1), shot=shot[n],
                         new_ink_pct=round(new, 2)))
    json.dump(dict(mode="board", step=STEP, windows=meta), open("work/frames_meta.json", "w"), indent=1)
    print(f"board mode: {len(files)} dense frames, {shot[-1] + 1} camera shots, {len(meta)} windows")
    for x in meta:
        print(f"  {x['key']} {x['t0']:6.1f}-{x['t1']:6.1f}s  frame at {x['frame_t']}s  shot {x['shot']}  new ink {x['new_ink_pct']}%")


if a.mode == "board":  # whole frame by default (no slide area, no webcam overlay to white out)
    import sys
    cap = cv2.VideoCapture(a.video); VW, VH = int(cap.get(3)), int(cap.get(4)); cap.release()
    board_mode(a.video, a.crop if a.crop != "auto" else f"0,0,{VW // 2 * 2},{VH // 2 * 2}", a.window)
    sys.exit(0)


def detect_layout(video):
    """Find the slide area: the widest run of non-black columns, measured on frames sampled across the video
    (lower three quarters only, so a webcam in the top corner does not count). Falls back to the full frame."""
    cap = cv2.VideoCapture(video)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cols, rows = [], []
    for q in (0.15, 0.3, 0.5, 0.7, 0.85):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * q))
        ok, fr = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        cols.append(g[H // 4:].mean(axis=0)); rows.append(g.mean(axis=1))
    cap.release()
    if not cols:
        return (0, 0, W, H), W, H
    c = np.median(cols, axis=0) > 30
    best, cur0, b0, b1 = 0, None, 0, W
    for x in range(W + 1):
        on = x < W and c[x]
        if on and cur0 is None:
            cur0 = x
        if not on and cur0 is not None:
            if x - cur0 > best:
                best, b0, b1 = x - cur0, cur0, x
            cur0 = None
    if best < 0.4 * W:
        return (0, 0, W, H), W, H
    r = np.median(rows, axis=0) > 30
    ys = np.where(r)[0]
    y0, y1 = (int(ys[0]), int(ys[-1]) + 1) if len(ys) else (0, H)
    return (b0 - b0 % 2, y0 - y0 % 2, (b1 - b0) // 2 * 2, (y1 - y0) // 2 * 2), W, H


CORNERS = ("auto", "top-right", "top-left", "bottom-right", "bottom-left")
if a.crop == "auto" or a.webcam in CORNERS:
    (dx, dy, dw, dh), VW, VH = detect_layout(a.video)
    if a.crop == "auto":
        a.crop = f"{dx},{dy},{dw},{dh}"
    if a.webcam in CORNERS:  # a corner overlay, scaled to the video width (320x180 on a 1920-wide frame); 'auto' = top-right
        s = VW / 1920; w, h = int(320 * s), int(180 * s)
        x = 0 if a.webcam.endswith("left") else VW - w
        y = VH - h if a.webcam.startswith("bottom") else 0
        a.webcam = f"{x},{y},{w},{h}"
    print(f"slide area (auto): crop={a.crop} webcam={a.webcam} in a {VW}x{VH} video")

crop_dir, final_dir = pathlib.Path("work/frames_crop"), pathlib.Path("work/frames_final")
for d in (crop_dir, final_dir):
    shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)

cx, cy, cw, ch = (int(v) for v in a.crop.split(","))
if a.webcam == "none":  # no overlay to hide (screen share only, edited video)
    vf = f"fps=1/{a.step:g},crop={cw}:{ch}:{cx}:{cy}"
else:
    wx, wy, ww, wh = (int(v) for v in a.webcam.split(","))
    vf = f"fps=1/{a.step:g},drawbox=x={wx}:y={wy}:w={ww}:h={wh}:color=white:t=fill,crop={cw}:{ch}:{cx}:{cy}"
subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", a.video, "-vf", vf, "-q:v", "2",
                str(crop_dir / "c_%04d.jpg")], check=True)

files = sorted(glob.glob(str(crop_dir / "*.jpg")))
small = [cv2.resize(cv2.imread(f, 0), (720, 540)) for f in files]
kept = []
for i, f in enumerate(files):
    last = i == len(files) - 1 or (cv2.absdiff(small[i], small[i + 1]) > 40).mean() * 100 > a.threshold
    if last:
        shutil.copy(f, final_dir / pathlib.Path(f).name); kept.append(pathlib.Path(f).name)
print(f"frames: {len(files)} extracted, {len(kept)} end-state kept -> {kept}")

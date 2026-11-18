"""Professor vs student split by voice (no API). Run with the envs/speaker python.
  speaker_split.py VIDEO [OUTDIR]
Windows of audio -> ECAPA voice embeddings -> dominant voice = professor; poor matches = student.
Writes OUTDIR/speakers.json (time ranges + labels) and prints a timeline."""
import json, pathlib, subprocess, sys
import numpy as np
import torch
from speechbrain.inference.speaker import EncoderClassifier

video = sys.argv[1]
out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "work/speakers"); out.mkdir(parents=True, exist_ok=True)
SR, WIN, HOP = 16000, 2.0, 1.0

raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", video, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                     capture_output=True, check=True).stdout
audio = np.frombuffer(raw, dtype=np.float32).copy()
dur = len(audio) / SR

clf = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", savedir=str(pathlib.Path(__file__).resolve().parent.parent / "work/models/ecapa"),
                                     run_opts={"device": "cpu"})
rms_all = float(np.sqrt(np.mean(audio ** 2)))
starts, embs = [], []
t = 0.0
while t + WIN <= dur:
    w = audio[int(t * SR):int((t + WIN) * SR)]
    if np.sqrt(np.mean(w ** 2)) > 0.25 * rms_all:  # skip near-silence
        with torch.no_grad():
            e = clf.encode_batch(torch.from_numpy(w)[None]).squeeze().numpy()
        embs.append(e / np.linalg.norm(e)); starts.append(t)
    t += HOP
E, starts = np.stack(embs), np.array(starts)

# professor profile = mean of the windows closest to the overall median voice (robust to a few interruptions)
med = np.median(E, axis=0); med /= np.linalg.norm(med)
sim0 = E @ med
core = E[sim0 >= np.percentile(sim0, 50)]
prof = core.mean(axis=0); prof /= np.linalg.norm(prof)
sim = E @ prof
THR = float(sys.argv[3]) if len(sys.argv) > 3 else 0.35   # similarity below this = not the professor
K = 5                                                      # rolling-median windows (smooths short dips like "okay")
pad = np.pad(sim, K // 2, mode="edge")
smooth = np.array([np.median(pad[i:i + K]) for i in range(len(sim))])
thr = THR
label = np.where(smooth >= thr, "professor", "student")

# merge consecutive windows into ranges (bridge 1-window gaps)
ranges, cur = [], None
for s, l, v in zip(starts, label, sim):
    if cur and cur["label"] == l and s - cur["end"] <= HOP + 1e-6 + 1.0:
        cur["end"] = s + WIN; cur["sims"].append(float(v))
    else:
        if cur: ranges.append(cur)
        cur = dict(start=float(s), end=float(s + WIN), label=str(l), sims=[float(v)])
ranges.append(cur)
for r in ranges:
    r["mean_sim"] = round(float(np.mean(r.pop("sims"))), 3); r["start"] = round(r["start"], 1); r["end"] = round(r["end"], 1)
# conservative default: most videos have NO student, so only keep long, clearly different stretches
MIN_SEC, MAX_MEAN = 10.0, 0.25
for r in ranges:
    if r["label"] == "student" and not (r["end"] - r["start"] >= MIN_SEC and r["mean_sim"] <= MAX_MEAN):
        r["label"] = "professor (weak student signal)"
json.dump(dict(duration=dur, threshold=round(thr, 3), windows=[dict(t=float(a), sim=round(float(b), 3), label=str(c))
          for a, b, c in zip(starts, sim, label)], ranges=ranges), open(out / "speakers.json", "w"), indent=1)

print(f"duration {dur:.0f}s, windows {len(starts)}, similarity median {np.median(sim):.2f}, threshold {thr:.2f}")
print(f"student-labelled windows: {int((label == 'student').sum())} ({100 * (label == 'student').mean():.1f}%)")
for r in ranges:
    if r["label"].startswith("student") or "weak" in r["label"]:
        print(f"  {'STUDENT' if r['label'] == 'student' else 'weak (kept as professor)'} {r['start']:6.1f}-{r['end']:6.1f}s  ({r['end'] - r['start']:.0f}s)  mean sim {r['mean_sim']}")

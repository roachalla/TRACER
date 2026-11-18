"""Transcribe one file with one local speech model and time it. Loaders are in asr_models.py.
Usage: <environment's python> asr_bench.py MODEL FILE OUTDIR   (MODEL: parakeet | qwen; Whisper uses asr_bench_whisper_pt.py)"""
import json, os, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import asr_models as tx

model, path, outdir = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
outdir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

t0 = time.perf_counter()
audio = tx.decode_audio(path)
t_decode = time.perf_counter() - t0
dur = len(audio) / tx.SAMPLE_RATE

t0 = time.perf_counter()
fn = tx.load_qwen_model(False) if model == "qwen" else tx.load_onnx_model(model, False)
t_load = time.perf_counter() - t0

t0 = time.perf_counter()
text = fn(audio)
t_run = time.perf_counter() - t0

(outdir / f"{model}.txt").write_text(text, encoding="utf8")
res = dict(model=model, audio_sec=round(dur, 1), decode_s=round(t_decode, 2), load_s=round(t_load, 2),
           transcribe_s=round(t_run, 2), realtime_factor=round(t_run / dur, 3), words=len(text.split()))
with open(outdir / f"{model}.json", "w", encoding="utf8") as f:
    json.dump(res, f, indent=1)
print(res)

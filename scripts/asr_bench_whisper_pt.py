"""openai-whisper large-v3 (PyTorch) timing run. Usage: envs/whisper python asr_bench_whisper_pt.py FILE OUTDIR"""
import json, pathlib, sys, time, os
os.environ["PYTHONIOENCODING"] = "utf-8"
import whisper
path, outdir = sys.argv[1], pathlib.Path(sys.argv[2]); outdir.mkdir(parents=True, exist_ok=True)
t0 = time.perf_counter(); m = whisper.load_model("large-v3", device="cuda"); t_load = time.perf_counter() - t0
t0 = time.perf_counter(); r = m.transcribe(path, language="en", fp16=True, verbose=False); t_run = time.perf_counter() - t0
text = r["text"].strip()
(outdir / "whisper.txt").write_text(text, encoding="utf8")
json.dump(r["segments"], open(outdir / "whisper_segments.json", "w"), indent=1)
dur = float(whisper.audio.load_audio(path).shape[0]) / whisper.audio.SAMPLE_RATE
res = dict(model="whisper", audio_sec=round(dur, 1), decode_s=0, load_s=round(t_load, 2), transcribe_s=round(t_run, 2),
           realtime_factor=round(t_run / dur, 3), words=len(text.split()))
json.dump(res, open(outdir / "whisper.json", "w"), indent=1); print(res)

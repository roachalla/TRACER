"""Run the whole pipeline for one video inside its own folder: runs/<slug>/work/...

  python scripts/run_video.py VIDEO [--free-only] [--steps frames,ocr,...] [--force] [--title "..."]

Stages (in order): frames, ocr(paid: Mathpix), asr, gaps, speakers, page, resolve(paid: Claude), page2, textbook(paid: Claude)
--free-only runs only the stages that cost nothing (frames, asr, gaps, speakers) plus page when OCR already exists.
Finished stages are skipped unless --force. Spend is printed per paid stage; Claude spend is logged to work/llm_spend.jsonl."""
import argparse, json, os, pathlib, re, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "scripts"
sys.path.insert(0, str(S))
from olc_paths import env_python
import course
PY_CV = env_python("whisper")            # cv2 + openai-whisper
PY_PARA = env_python("parakeet")       # onnx-asr (Parakeet)
PY_QWEN = env_python("qwen")           # Qwen3-ASR
PY_SPK = env_python("speaker")          # speechbrain voice check
PY_SYS = pathlib.Path(sys.executable)                      # anthropic SDK

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("--free-only", action="store_true")
ap.add_argument("--steps")
ap.add_argument("--force", action="store_true")
ap.add_argument("--title")
ap.add_argument("--crop", help="x,y,w,h of the slide area (default: detected automatically)")
ap.add_argument("--webcam", help="x,y,w,h of a webcam overlay to white out (default: top-right corner)")
ap.add_argument("--runs", help="results folder (default: <project>/runs); each video gets its own sub-folder")
ap.add_argument("--mode", choices=["slides", "board"], default=None,
                help="override the processing mode (normally set by the video type); board = chalk/whiteboard lecture")
ap.add_argument("--type", default="auto", choices=["auto", "1", "2", "3", "4", "5"],
                help="video type: 1 classroom recording, 2 board only, 3 screen share, 4 screen share + webcam corner, "
                     "5 edited / PowerPoint recording; auto = detect (one cheap vision call)")
ap.add_argument("--transcript-only", action="store_true",
                help="speech only: a cleaned, checked transcript with no slides, images or MathML (offered for type 1)")
ap.add_argument("--outputs", default="textbook,teaching,word,transcript",
                help="comma list of: textbook (web textbook), teaching (teaching edition), word (.docx), transcript (transcript + "
                     "captions). Without textbook/teaching/word only the speech stages run (no slides, OCR or Claude writing).")
ap.add_argument("--budget", type=float, default=None,
                help="most this run may spend on Claude, in USD; the run stops cleanly when it is reached (rerun to continue)")
ap.add_argument("--no-clarify", action="store_true", help="skip the loops that settle slide-reader questions and flagged sentences before asking a person (saves a little Claude spend; more review cards)")
args = ap.parse_args()
OUT = {x.strip() for x in args.outputs.split(",") if x.strip()}
if not OUT & {"textbook", "teaching", "word"}:
    args.transcript_only = True  # only the transcript and captions were asked for
TYPE1_EXIT = 3  # run stops with this code when a classroom recording is detected and --transcript-only was not given

video = pathlib.Path(args.video).resolve()
slug = course.slug(video.stem)
title = args.title or course.title_from(slug)
run = pathlib.Path(args.runs).resolve() / slug if args.runs else ROOT / "runs" / slug
run.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, PYTHONIOENCODING="utf-8")
env.pop("ANTHROPIC_API_KEY", None)  # never let a Claude Code key leak into script runs; scripts read .env themselves
# spending limit for this run: every Claude call checks it (scripts/llm_util.py); the offset makes it count this run only
_log = run / "work/llm_spend.jsonl"
env["TRACER_SPEND_OFFSET"] = str(_log.stat().st_size if _log.exists() else 0)
if args.budget is not None:
    env["TRACER_BUDGET_USD"] = str(args.budget)
BUDGET_EXIT = 4


def sh(label, cmd):
    t = time.time()
    print(f"\n=== [{slug}] {label}", flush=True)
    r = subprocess.run([str(c) for c in cmd], cwd=run, env=env)
    if r.returncode == BUDGET_EXIT:
        print(f"\n[{slug}] STOPPED: the spending limit was reached during '{label}'. Finished steps are saved.")
        sys.exit(BUDGET_EXIT)
    if r.returncode != 0:
        sys.exit(f"STAGE FAILED: {label} (exit {r.returncode})")
    print(f"    ({time.time() - t:.0f}s)")


def have(*paths):
    return all(any(run.glob(p)) if "*" in p else (run / p).exists() for p in paths)


# ---- stage 0: video type (decides how everything else runs)
if args.type == "auto" and not have("work/profile.json") and have("work/frames_final/*.jpg"):
    # run folders made before video types existed were all processed as slides with a webcam corner: keep that, no detection
    (run / "work/profile.json").write_text(json.dumps(dict(type=4, name="screen share with webcam corner", mode="slides",
                                                           webcam="auto", how="assumed for an older run", confidence="low", reason="")))
if args.type != "auto" or not have("work/profile.json") or (args.force and not args.steps):
    sh("video type", [PY_SYS, S / "video_profile.py", video, "--type", args.type])
PROFILE = json.load(open(run / "work/profile.json"))
MODE = args.mode or ("board" if PROFILE["mode"] == "board" else "slides")
WEBCAM = args.webcam or PROFILE.get("webcam", "auto")
if PROFILE["mode"] == "transcript_only" and not args.transcript_only and not args.mode:
    print(f"\nVIDEO TYPE 1 (classroom recording): [{slug}] this kind of video is not supported for full processing. The instructor "
          "stands in front of the screen and the screen is small and tilted, so slides, figures and equations would come out "
          "unreliable. Run again with --transcript-only for a cleaned, checked transcript (no slides, images or math), or with "
          "--type 2/3/4/5 if the detection is wrong.")
    sys.exit(TYPE1_EXIT)

STAGES = {
    "frames": (lambda: have("work/frames_final/*.jpg"),
               lambda: sh("frames", [PY_CV, S / "extract_frames.py", video, "--mode", MODE, "--webcam", WEBCAM]
                          + (["--crop", args.crop] if args.crop else [])), False),
    # done = the step's own end marker, written only when it finished every slide (an interrupted step is redone; finished
    # slides and cached answers are reused, so redoing it costs little)
    "ocr": (lambda: have("work/mathpix/.ocr_done"),
            lambda: sh("mathpix ocr", [PY_SYS, S / "mathpix_ocr.py"]), True),
    "fixocr": (lambda: have("work/mathpix_raw/.fixocr_done"), lambda: sh("vision-correct OCR (Claude)", [PY_SYS, S / "fix_ocr.py"]), True),
    "asr": (lambda: have("work/asr/whisper.txt", "work/asr/qwen.txt", "work/asr/parakeet.txt"),
            lambda: ([sh("asr whisper", [PY_CV, S / "asr_bench_whisper_pt.py", video, "work/asr"]) if not have("work/asr/whisper.txt") else None,
                      sh("asr qwen", [PY_QWEN, S / "asr_bench.py", "qwen", video, "work/asr"]) if not have("work/asr/qwen.txt") else None,
                      sh("asr parakeet", [PY_PARA, S / "asr_bench.py", "parakeet", video, "work/asr"]) if not have("work/asr/parakeet.txt") else None]), False),
    "gaps": (lambda: have("work/gaps/gaps.json"), lambda: sh("gap finder", [PY_SYS, S / "gap_finder.py"]), False),
    "speakers": (lambda: have("work/speakers/speakers.json"),
                 lambda: sh("speaker split", [PY_SPK, S / "speaker_split.py", video, "work/speakers"]), False),
    "page": (lambda: False, lambda: sh("review page", [PY_CV, S / "build_page.py", title, "--out=review_raw.html"]), False),
    "resolve": (lambda: have("work/gaps/resolved.json"), lambda: sh("resolve gaps (Claude)", [PY_SYS, S / "resolve_gaps.py"]), True),
    "page2": (lambda: False, lambda: sh("review page with LLM fixes", [PY_CV, S / "build_page.py", title, "--out=review_draft.html"]), False),
    "textbook": (lambda: have("work/site/textbook.json", "work/site/final_textbook.html"), lambda: sh("textbook (Claude)", [PY_SYS, S / "textbook.py", "--title", title]
                                                                     + ([] if "teaching" in OUT else ["--no-teaching"]) + (["--no-clarify"] if args.no_clarify else [])), True),
    "tprep": (lambda: False, lambda: sh("transcript prep", [PY_SYS, S / "transcript_only.py", "--prep"]), False),
    "transcript": (lambda: False, lambda: sh("transcript page", [PY_SYS, S / "transcript_only.py", title]), False),
    "docx": (lambda: False, lambda: sh("Word version", [PY_SYS, S / "make_docx.py"] + (["--teaching"] if "teaching" in OUT else [])), False),
    "package": (lambda: False, lambda: sh("sorting outputs", [PY_SYS, S / "package_outputs.py", "--outputs", ",".join(sorted(OUT))]), False),
}
if args.transcript_only:  # speech only: no frames, OCR or textbook
    order = ["asr", "gaps", "speakers", "tprep", "resolve", "transcript", "package"]
else:
    order = ["frames", "ocr", "fixocr", "asr", "gaps", "speakers", "page", "resolve", "page2", "textbook"] + \
            (["transcript"] if "transcript" in OUT else []) + (["docx"] if "word" in OUT else []) + ["package"]
chosen = args.steps.split(",") if args.steps else order
if args.free_only:
    chosen = [s for s in chosen if not STAGES[s][2]]
    if not args.transcript_only and not have("work/mathpix/*.json"):
        chosen = [s for s in chosen if s not in ("page", "page2")]

for name in order:
    if name not in chosen:
        continue
    done, do, paid = STAGES[name]
    if done() and not args.force:
        print(f"--- [{slug}] {name}: already done, skipping"); continue
    do()

spend = run / "work/llm_spend.jsonl"
usd = sum(json.loads(l)["usd"] for l in spend.read_text().splitlines()) if spend.exists() else 0.0
print(f"\n[{slug}] Claude spend so far in this run folder: ${usd:.4f}")

# TRACER

**Transcribe · Reconstruct · Audit · Caption · Encode · Review**

*Accessible transcripts with equations and figures from lecture videos.*

TRACER turns recorded, math-heavy lecture videos into accessible text that stays true to what the instructor taught. It is
built for videos where captions alone fall short: poor room audio, accents, and handwritten equations that captions cannot
carry. The goal is that a busy subject-matter expert reviews a short list of flagged items instead of re-watching the lecture.

> **Before you start: TRACER expects videos that are already split into topics.** It is built for lecture capture that has
> been cut into short segments, one topic each (often called lecturettes, typically 5 to 20 minutes). Each video becomes one
> chapter. TRACER does not analyze a full-length class recording or break it into segments; do that first with your video
> editor or lecture-capture platform.

| | What happens |
|---|---|
| **Transcribe** | Three speech engines (Whisper, Qwen3-ASR, Parakeet) transcribe the audio independently, on your own GPU. |
| **Reconstruct** | Where the engines disagree, Claude settles it using the slide as evidence, and a second model checks every fix. Slides are read by Mathpix, then a vision model corrects that reading against the slide image (arrows, annotations, misread symbols); a correction that changes or adds a number, or adds anything the model cannot see, is rejected and listed for review. Long student questions are moved out of the instructor's text (see Limits). |
| **Audit** | Code rules (numbers, quotes, sources, dropped content), an evidence auditor, a peer reviewer that sees only the finished page, and an equation repair loop that re-reads flagged equations against the slide image. |
| **Caption** | Corrected captions (WebVTT) and a clean, time-stamped transcript. Caption times are estimated: words are spread evenly across each Whisper segment, not timed one by one. |
| **Encode** | Math as real MathML (web) and editable equations (Word); figures with alt text and layered descriptions following an image-description guide. |
| **Review** | Questions the slide readers raise are first re-checked against other video frames and the professor's words; answers a second model confirms are settled, with their evidence. Everything still open goes to a review page for a person to decide. |

TRACER does not add content: no outside facts, new examples or invented figures. The writer may use only the transcript and
the slides, every sentence is checked against them, and anything it cannot trace is flagged. The optional teaching edition
(takeaways, study questions, glossary) is built only from the chapter's own text and audited item by item.

**Everything TRACER makes is a draft until a person reviews it.** Student pages and Word files say "Draft" until the
professor's review is applied, and the transcript and captions stay in the review folder until someone approves them (see
Reviewing).

## Quick start (desktop app)

1. Do the one-time **Setup** below.
2. `python olc_gui.py`
3. Choose a video or a folder, the video type (or let TRACER detect it), what to make, where results go, and your API keys.
   Press **Start**.

The app shows a time and cost estimate first (it learns your computer's speed after the first run), a live log, and a
spending limit. Every Claude call checks the limit before it is sent, so a run stops before going over it; finished work is
kept, and running again with a higher limit continues where it stopped.

## Video types

| Type | Handling |
|---|---|
| 1 Classroom recording (camera films a screen or board, instructor in front) | Transcript and captions only: slides would come out unreliable. You are asked first. |
| 2 Board only | Board mode: dense frames, sections by topic. |
| 3 Screen share (e.g. Zoom) | Slides. |
| 4 Screen share with a webcam corner | Slides; the webcam corner is hidden. |
| 5 Edited video or PowerPoint recording | Slides. |

Whatever type you choose, TRACER still looks for a webcam picture in the frames. If it finds one in a type 2, 3 or 5 video,
it hides it from the slide images and warns you, so camera footage of people never reaches Claude or the outputs by mistake.

## What you need

- **Windows 10/11 with an NVIDIA GPU** (8 GB+ VRAM recommended). Whisper runs on CUDA only, so a GPU is required.
  macOS/Linux are untested.
- **ffmpeg** on your PATH; **pandoc** for the Word version (https://pandoc.org).
- **Python 3.10+** for the app and the Claude steps.
- About **20 GB of disk** for the speech environments and models.
- **Your own API keys** (you are billed directly by each service):
  - Anthropic (Claude): https://console.anthropic.com
  - Mathpix (slide OCR): https://mathpix.com (app ID + app key)

**Cost:** expect **$0.05 to $0.08 per minute of video** for the full textbook (Claude plus Mathpix), and about $0.035 per
minute for transcript and captions only. Lectures with many slides and poor audio cost the most. Every paid call is logged
in the run's audit folder.

**Time** depends mostly on the GPU, because the three speech engines run on it one after another. On the development laptop,
a 13-minute lecture took about 40 minutes end to end. The app's estimate uses your own measured speed after the first run, and
warns if speech recognition is much slower than expected (usually a sign the GPU is not being used).

## Setup (one time)

Commands are for Windows PowerShell, run from the project folder.

1. **The app and the Claude steps:**
   ```
   pip install -r requirements.txt
   ```
2. **Four speech and video environments** under `envs/`. They are separate on purpose: their packages conflict. Each
   requirements file holds the versions TRACER was tested with.
   ```
   foreach ($e in "whisper","parakeet","qwen","speaker") {
     python -m venv envs\$e
     & envs\$e\Scripts\python -m pip install -r requirements\$e.txt
   }
   ```
   The `whisper` and `qwen` files install the CUDA build of PyTorch. If your NVIDIA driver needs a different CUDA version,
   change the `--extra-index-url` line (see https://pytorch.org).
3. **Speech models** (about 6 GB, downloaded once into `models/`):
   ```
   envs\parakeet\Scripts\python scripts\get_models.py
   ```
   This fetches Parakeet TDT 0.6B v3 (NVIDIA, CC-BY-4.0) and Qwen3-ASR-1.7B (Qwen team, Apache-2.0) from Hugging Face.
   Whisper large-v3 (OpenAI, MIT) downloads itself the first time it runs.
4. **Check:** start the app and press **Check setup**. It lists anything missing and how to fix it.

Environments and models can live elsewhere: set `OLC_PY_CV` (whisper), `OLC_PY_PARA`, `OLC_PY_QWEN`, `OLC_PY_SPK` to each
environment's `python.exe`, and `OLC_MODELS_DIR` to the models folder. Which Claude model does each job, and the prices used
for the spend log, are set in `config/models.json`.

**Your course:** copy `config/course.json` to `config/course.local.json` (git-ignored) and set the course name and subject.
The subject tells the peer reviewer and the figure writer what field the chapter is in. File names such as `W6_Z2_T1_Title`
or `ABCD123_09-15_T2_Title` are grouped by week and lecture on the pages and in the index (see `scripts/course.py`).

**Tests** for the text checks (no keys, models or video needed): `python -m unittest discover tests`

## Your API keys

- Keys are typed into masked fields and held in memory. Tick **Remember on this computer** to store them in the operating
  system's credential vault (Windows Credential Manager / macOS Keychain). They are never written to a file or to the log.
- **Forget saved keys** removes them. **Check Claude key** makes a free, read-only call.
- The pipeline ignores the `ANTHROPIC_API_KEY` environment variable on purpose, so a key meant for another tool is never used.
- Command-line use: put `OLC_SCRIPT_API_KEY`, `MATHPIX_APP_ID`, `MATHPIX_APP_KEY` in a `.env` file at the project root
  (git-ignored). Never commit it.

## Command line

```
python scripts/run_video.py VIDEO --runs RESULTS_DIR [options]
python scripts/build_index.py RESULTS_DIR      # index pages for a folder of results
```

| Option | What it does |
|---|---|
| `--budget USD` | Most this run may spend on Claude. Checked before every call; the run stops cleanly (exit code 4) when it is reached. Also settable as the `TRACER_BUDGET_USD` environment variable. |
| `--type auto\|1-5` | Video type (see the table above). `auto` detects it. |
| `--outputs` | Any of `textbook,teaching,word,transcript` (default: all). |
| `--transcript-only` | Transcript and captions only; no slides, no textbook. |
| `--free-only` | Run only the steps that cost nothing (frames, speech, gap finding, speaker split). |
| `--title "..."` | Title shown on the pages (default: taken from the file name). |
| `--mode slides\|board` | Override the slide/board decision. |
| `--crop x,y,w,h` | The slide area in the frame, if automatic detection gets it wrong. |
| `--webcam x,y,w,h` | A webcam overlay to hide, if automatic detection misses it. |
| `--steps a,b,...` | Run only these steps: `frames, ocr, fixocr, asr, gaps, speakers, page, resolve, page2, textbook, transcript, docx, package`. |
| `--force` | Redo the listed steps even if they finished before. Claude answers already received are reused from the cache at no cost. |
| `--no-clarify` | Skip the loops that settle slide-reader questions and flagged sentences before asking a person (a little cheaper, more review cards). |

Finished steps are skipped when you run a video again. Each step writes an end marker only when it completes, so a step
that crashed is redone; answers already paid for are cached, so resuming costs little or nothing.

## What you get (per video, in `RESULTS_DIR/<video>/`)

- **1 Main deliverables**: `final_textbook.html` (student textbook), `final_textbook_teaching.html` (teaching edition),
  `textbook.docx` / `textbook_teaching.docx` (Word, editable equations). After approval, also `final_transcript.html`,
  `transcript.txt` and `captions.vtt`.
- **2 Review resources**: `review_center.html` (start here: see below), `review_textbook.html` (the text with each slide and a
  plain-language **For you to check** list), `review_transcript.html` (uncertain passages and caption cues), `review_draft.html`.
  Until they are approved, the transcript and captions are here too.
- **3 Audit and logs**: `audit_summary.md` (checks and spend at a glance), every sentence with its sources, every transcript
  fix with its evidence, every paid call, the raw slide OCR, and `figure_sources.json` (where each figure came from, for
  copyright checks; fill it in, and TRACER keeps what you wrote on later runs).
- `work/`: the pipeline's working files (needed for re-runs). `index_student.html` / `index_review.html` in `RESULTS_DIR`.

The three folders are rebuilt on every run. Only files TRACER made are replaced; files you add to them are kept.

## Reviewing: designer first, then the professor

`review_center.html` is one page: the textbook exactly as students get it on the left (teaching materials can be hidden), and
one card per item to check on the right. Cards and the text are linked: click a card to jump to its sentence, equation or
figure; click a numbered marker in the text to jump to its card. For each card choose **Looks right**, **Needs a fix** (type the
corrected sentence or equation) or **Ask**. Select any text to leave a comment. A red line at the top counts the serious
items still open. Questions TRACER settled itself, from other video frames and the professor's words, are under **Settled by
TRACER** with their evidence; some cards (formatting, figure sources) are shown to the designer only.

1. Put the run folder in a OneDrive folder shared with the professor.
2. The designer opens the review center (from the synced folder, in Edge or Chrome), reviews, and presses **Save feedback**,
   saving the file in `2 Review resources`. Work is also kept in the browser, so the page can be closed and reopened.
3. `python scripts/apply_review.py "2 Review resources/TRACER feedback - designer - <run>.json"` (from the run folder)
   applies the corrections to every format (web, Word) at no cost and rebuilds the review center as the professor's version:
   settled items are marked done, the designer's questions stay open.
4. The professor reviews and saves the same way; OneDrive syncs the file back. **Email summary** opens an email to the
   designer in the default mail program (Outlook, for most) with a summary. Run `apply_review.py` with both files for the
   final version. Once the professor's file is applied, the "Draft" notice is removed from the student pages and Word files.
5. `review_todo.md` lists what a script cannot apply: fixes described in words, questions and comments.
6. When someone has checked the transcript and captions (`review_transcript.html`), run
   `python scripts/apply_review.py --approve-transcript` from the run folder to move them to `1 Main deliverables`.

## Limits worth knowing

- A subject-matter expert should still review the output before students see it. The review pages exist for that.
- Handwriting is read by OCR and by a vision model; disagreements are flagged, but a missed label (an omission) can only be
  caught by a person.
- Student voices: a different voice speaking for 10 seconds or more is moved out of the instructor's text. Shorter stretches
  are flagged for review but left in, and very short remarks can be missed. Classroom recordings get a transcript only.
- The writer sometimes has to guess what the professor is pointing at ("this one here"). Those sentences are checked, but a
  wrong guess can still read smoothly, which is one reason the review step matters.
- Accuracy has not yet been measured against a hand-checked reference, and the output has not been tested with learners.
- Source videos, slides and generated pages are course material: `runs/`, `work/`, `envs/`, `models/`, media files and `.env`
  are git-ignored. Keep any other folder of course material out of the repository too.

## License

MIT (see `LICENSE`). The speech models and libraries TRACER downloads keep their own licences: Whisper (MIT), Parakeet TDT
0.6B v3 (CC-BY-4.0), Qwen3-ASR-1.7B (Apache-2.0), SpeechBrain (Apache-2.0), MathJax (Apache-2.0). Mathpix and Claude are paid
services under their providers' terms. Ideas for later are in `docs/roadmap.md`.

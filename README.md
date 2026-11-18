# TRACER

**Transcribe · Reconstruct · Audit · Caption · Encode · Review**

*Accessible transcripts with equations and figures from lecture videos.*

TRACER is built to create accessible transcripts and study materials from challenging videos with layers of complex content
and/or where captions alone fall short: poor room audio, strong accents, specialized vocabulary. Its goal is to stay as true
as possible to what the instructor taught.

> **Before you start: TRACER expects videos that are already split into topics.** It is built for lecture capture that has
> been cut into short segments, one topic each (often called lecturettes, typically 5 to 20 minutes). Each video becomes one
> chapter. TRACER does not analyze a full-length class recording or break it into segments; do that first with your video
> editor or lecture-capture platform.

## How the pipeline works

**`run_video.py`** runs the following sequence of scripts to process each video:

1. **`video_profile.py`** checks what kind of video it is (screen share, board, classroom camera), finds the slide area and
   hides any webcam picture.
2. **`extract_frames.py`** samples the video and keeps one frame for each finished state of a slide.
3. **`mathpix_ocr.py`** reads the text and math on each kept frame with Mathpix OCR.
4. **`fix_ocr.py`** has a Claude vision model check each OCR reading against the slide image and correct misreadings.
   Corrections that change numbers or add things the model cannot see are rejected and flagged.
5. **`asr_bench_whisper_pt.py`, `asr_bench.py`** transcribe the audio three times, with Whisper, Qwen3-ASR and Parakeet, on
   your own GPU.
6. **`gap_finder.py`** lines up the three transcripts and lists every place they disagree.
7. **`speaker_split.py`** separates student voices from the instructor's.
8. **`resolve_gaps.py`** has Claude settle each disagreement using the slide as evidence; a second model checks every fix.
9. **`build_page.py`** builds draft review pages that show each slide next to its transcript.
10. **`textbook.py`** writes the chapter from the settled transcript and the slides, with every sentence citing its source.
    It then audits the chapter (number and quote checks, an evidence auditor, a peer reviewer, equation repair), writes the
    figure descriptions, and builds the web editions and the review page.
11. **`transcript_only.py`** produces the clean, time-stamped transcript and the captions.
12. **`make_docx.py`** makes the Word version with editable equations.
13. **`package_outputs.py`** sorts everything into three folders: main deliverables, review resources, and audit logs.

## What you need

- **A Windows 10 or 11 computer with an NVIDIA graphics card** (8 GB of graphics memory or more). The speech engines run on
  the graphics card. Mac and Linux have not been tested.
- **About 20 GB of free disk space** for the speech models.
- **Python 3.10 or newer** (https://www.python.org), **ffmpeg** (https://ffmpeg.org) and **pandoc** (https://pandoc.org).
- **Two API keys**, one for the Claude API (from the Anthropic Console) and one for the Mathpix API (an app ID and app key).
  These are pay-as-you-go developer keys billed to you by each service; a Claude.ai chat subscription or a Mathpix Snip
  subscription does not include them.

**Cost:** a few cents per minute of video in Claude and Mathpix fees. Lectures with poor audio cost more. The app shows an
estimate before each run and stops at the spending limit you set.

**Time:** depends mostly on your graphics card. Expect a run to take a few times the length of the video.

## Setup and use

> [!TIP]
> **Let Claude set it up for you.** A great way to use this tool is with Claude Code, in the Code tab of the Claude desktop
> app. Point it at this repository (`https://github.com/roachalla/TRACER`) and ask it to download TRACER and set it up by
> following this README. It can run the installation commands for you, check what is missing, and explain any errors. When
> setup is done, ask it to start TRACER, and the app opens. Type your API keys into the app yourself rather than pasting
> them into the chat.

There are four steps:

1. Get your API keys.
2. Install the speech models and set up the speech and video environments (one time).
3. Use the app.
4. Find your results.

### 1. Get your API keys

- **Anthropic (Claude):** create a key at https://console.anthropic.com.
- **Mathpix:** create an app ID and app key at https://mathpix.com.

You type the keys into the app. Tick **Remember on this computer** to keep them in Windows Credential Manager; they are never
written to a file or shown in the log.

### 2. Install the speech models and environments (one time)

Open the TRACER folder in File Explorer, click the address bar, type `powershell` and press Enter. A PowerShell window opens
in that folder. Copy each block below into it and press Enter, one at a time. Steps b and c download several gigabytes and
can take a while.

a. The app:
```
pip install -r requirements.txt
```
b. The four speech and video environments:
```
foreach ($e in "whisper","parakeet","qwen","speaker") {
  python -m venv envs\$e
  & envs\$e\Scripts\python -m pip install -r requirements\$e.txt
}
```
c. The speech models:
```
envs\parakeet\Scripts\python scripts\get_models.py
```

### 3. Use the app

Double-click **Start TRACER.bat** in the TRACER folder. Then, in the app:

1. Press **Check setup** the first time. It lists anything missing and how to fix it.
2. Choose a video, or a folder of videos.
3. Choose the video type, or leave it on automatic.
4. Tick what to make: textbook, teaching edition, Word document, transcript and captions.
5. Choose where to save the results.
6. Enter your API keys and a spending limit.
7. Press **Start**. The app shows a time and cost estimate and a live log while it works.

### 4. What you get

Each video gets its own results folder with three folders inside:

- **1 Main deliverables:** the textbook as a web page, the teaching edition, and the Word versions. The transcript and
  captions are added here once someone has approved them.
- **2 Review resources:** the review pages, and the transcript and captions until they are approved. The interactive review
  page (`review_center.html`) is still being developed.
- **3 Audit and logs:** a summary of the checks and the spending, every sentence with its sources, and every paid call.

Everything is a draft until it has been reviewed: student pages and Word files are marked "Draft" until the instructor's review
is applied.

## Reviewing (being tested)

The interactive review page is a feature we are testing now. It shows the chapter exactly as students will see it, with a card
for each item that needs a person: a questioned sentence, equation or figure. The designer reviews first, then the instructor.
For each card they choose **Looks right**, **Needs a fix** or **Ask**, can comment on any text, and save their feedback to a file
in the shared folder. Applying that feedback to the chapter currently uses a script (`scripts/apply_review.py`).

## Disclaimer

This is a test tool developed for a specific case study and is not a polished finished product.

## License

MIT (see `LICENSE`). The speech models and libraries TRACER downloads keep their own licences: Whisper (MIT), Parakeet TDT
0.6B v3 (CC-BY-4.0), Qwen3-ASR-1.7B (Apache-2.0), SpeechBrain (Apache-2.0), MathJax (Apache-2.0). Mathpix and Claude are paid
services under their providers' terms. Ideas for later are in `docs/roadmap.md`.

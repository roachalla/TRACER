# Roadmap

Ideas that were tested or discussed and deliberately left for later. Contributions and test results are welcome.

## Speech

- **Slide vocabulary for the speech engines.** Pass terms read from the slides to Whisper (`initial_prompt`), Parakeet (word
  boosting) and Qwen3-ASR (context), and measure the gain with and without.
- **A transcript correction pass** using the slides, before the gap finder.
- **Enrolling the instructor's voice** from a known clip, so student speech is separated more reliably, including short remarks.
- **Other engines** (Canary-Qwen, WhisperX) and cloud services. Cloud services need a privacy decision first, because student
  voices would leave the computer.

## Slides and handwriting

- **Circles, arrows and highlights.** Handwritten text in any colour is already read. Non-text marks are not: options are
  finding coloured contours with OpenCV and reporting what each one encloses, or one vision call per frame that lists them.
- **Before/after frame differences**, to show what the instructor added when.
- **Using the original slide deck (.pptx)** as a reference when it is available and has real text, not images.

## Speed and cost

- Run the Claude steps through the Message Batches API (about half the price, slower).
- Write sections in parallel.
- Cache the slide-image part of repeated prompts.

## Product

- A frame preview grid, to drop frames before they are sent for OCR.
- Pause-for-review between steps.
- Tagged PDF (PDF/UA) output.

## Code

- Split `scripts/textbook.py` into modules.
- One shared Claude client, `.env` reader and price table.
- `argparse` and `with open(..., encoding="utf8")` everywhere.

## Not planned

- Synthetic or cloned instructor voices: not without the instructor's consent.
- Marking reconstructed text with italics alone: most screen readers do not announce it.

"""Version 3 ("textbook") pass: formal prose per slide section, with provenance, inline slide equations,
mechanical checks, an evidence auditor (sees sources) and a blind reader (sees what the learner sees).

  python textbook.py --dry-run         show what would be sent; no spend
  python textbook.py [--only N]        run (all sections, or just section N)

Reads  work/site/v2.json  (from build_page.py)
Writes work/site/textbook.json, review_textbook.html, final_textbook.html
Student chunks are never sent to the writer; the review build lists them as asides."""
import argparse, html, json, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import llm_util, provenance, figdedupe, course
from checks import nums, split_md, toks, REL, eq_norm, covered, incomplete

ap = argparse.ArgumentParser()
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--only", type=int)
ap.add_argument("--writer", default=llm_util.MODELS["writer"])
ap.add_argument("--judge", default=llm_util.MODELS["judge"])
ap.add_argument("--reviewer", default=llm_util.MODELS["reviewer"], help="model for the peer-review read of the whole chapter")
ap.add_argument("--segmenter", default=llm_util.MODELS["segmenter"], help="model that splits a board lecture into topic sections")
ap.add_argument("--title", default="Lecturette")
ap.add_argument("--no-teaching", action="store_true", help="skip the teaching layer (no teaching edition)")
ap.add_argument("--no-clarify", action="store_true", help="skip the loop that settles slide-reader questions from more frames and the speech")
args = ap.parse_args()

V2 = json.load(open("work/site/v2.json"))
site = pathlib.Path("work/site")


def possible_students(sim_max=0.25, min_windows=2):
    """Short stretches whose voice does not match the professor, below the 10 s rule in speaker_split.py.
    Calibrated on a 43-clip listening check (2026-11-11): runs of >=2 windows held a student in 4 of 11;
    single windows in 0 of 25 (3 'both'), so they are ignored. Review flag only; text is never moved."""
    spf, gpf = pathlib.Path("work/speakers/speakers.json"), pathlib.Path("work/gaps/gaps.json")
    if not spf.exists():
        return []
    sp = json.load(open(spf))
    W = sp["windows"]
    cur = [r for r in sp["ranges"] if r["label"] == "student"]
    toks, times = [], []
    if gpf.exists():
        g = json.load(open(gpf, encoding="utf8"))
        toks, times = g["tokens"], g["times"]
    out, i = [], 0
    while i < len(W):
        if W[i]["sim"] >= sim_max:
            i += 1
            continue
        j = i
        while j + 1 < len(W) and W[j + 1]["sim"] < sim_max and W[j + 1]["t"] - W[j]["t"] <= 1.01:
            j += 1
        a, b = W[i]["t"], W[j]["t"] + 2.0
        if j - i + 1 >= min_windows and not any(r["start"] - 1 <= a <= r["end"] + 1 for r in cur):
            out.append(dict(a=a, b=b, words=" ".join(t for t, x in zip(toks, times) if a - 0.5 <= x <= b + 0.5)))
        i = j + 1
    return out


POSSIBLE_STUDENT = possible_students()

WRITER_SYSTEM = """You convert a university lecture transcript into textbook prose for ONE slide section. The readers are learners
reading the finished text. The transcript is spoken and imperfect; some chunks are marked unresolved (garbled or disputed).

Rules:
1. Write clear, warm, learner-friendly teaching prose, like a good textbook author talking a student through the idea. Plain words,
   short sentences, natural transitions ("Next,", "Notice that", "Let's start with"). "We" and "you" are welcome in moderation. Do not
   sound clinical or stiff, and do not praise, reassure or add commentary. Warmth comes from phrasing only; transitions must not add
   facts. Remove fillers, false starts, repetition and conversational asides ("okay", "as I said"). You may merge and reorder within
   this section only.
1b. MATH IN TEXT: every mathematical symbol, variable, number with units, exponent or expression that appears in a sentence must be
   written as LaTeX between single dollar signs, e.g. $h = 1\\,\\mathrm{m}$, $2.07 \\times 10^{4}\\,\\mathrm{Pa}$, $\\omega$, $K_p(z)$,
   $\\cosh(kh)$. Never write math as plain text (no 10^4, no h_1, no Greek words like "omega", no bare "m/s"). Plain counting words
   and ordinary numbers in everyday phrases ("two sensors") stay as words.
2. Preserve the professor's meaning. Do NOT add facts, numbers, formulas or explanations that are not in the transcript chunks
   or the slide content. Never complete the physics from your own knowledge.
3. The slide's equations are numbered EQ1, EQ2, ... The page displays an equation right after the sentence that lists its number in
   `equations`. When the professor refers to an equation ("this", "given by this"), introduce it in prose and attach it with
   `equations`, instead of saying "the slide expression". Attach each equation at most once, in the order it is used. Do not write
   LaTeX yourself.
3b. FIGURES: the slide's figures are listed as FIG1, FIG2 ... The page shows a figure right after the sentence that lists its
   number in `figures`. Attach each figure (at most once) to the sentence where the lecture first talks about it, or where it helps
   the learner most. Do not write figure numbers in the prose; say "the figure below" or describe what it shows. Attach every slide
   equation that the lecture states or uses; leave one out only if it is clearly never discussed.
3c. GIVEN VALUES: when the slide states a problem (given quantities such as a depth, a period, a distance), say in prose what is
   given, with its values, before working with them, so the learner knows the problem without reading it off an equation.
4. Spoken pointing ("this one", "here") may otherwise only be resolved using the slide; put the exact slide text you relied on in
   slide_quotes. If it cannot be resolved, rewrite so it does not depend on pointing, or omit it.
5. Chunks with status "unresolved" may be wrong. Either omit them, or use them in a sentence with uncertain=true. Never invent a replacement.
6. Every sentence must list its sources: the chunk ids it is built from, and/or "slide" when it is taken from the slide content.
7. If little reliable content exists, return few or no sentences. Do not pad."""

WRITE_TOOL = {"name": "write_section", "description": "Return the textbook prose for this section as sentences.", "strict": True,
    "input_schema": {"type": "object", "required": ["sentences"], "additionalProperties": False, "properties": {"sentences": {
        "type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["text", "sources", "uncertain", "new_paragraph", "slide_quotes", "equations", "figures"], "properties": {
            "text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}},
            "uncertain": {"type": "boolean"}, "new_paragraph": {"type": "boolean"},
            "slide_quotes": {"type": "array", "items": {"type": "string"}},
            "equations": {"type": "array", "items": {"type": "integer"}},
            "figures": {"type": "array", "items": {"type": "integer"}}}}}}}}

AUDIT_SYSTEM = """You audit textbook sentences against the transcript chunks they cite and the slide content. For each sentence say
whether every claim, number and term in it is supported by the cited chunks or the slide. Unsupported = adds content, changes the
meaning, or states a number/term found in neither. Also unsupported: any added explanation, justification, example, caveat or
physical reasoning that is not in the cited chunks or the slide, even if it is true. Rewording, removing fillers, merging, and attaching a slide equation are fine."""
AUDIT_TOOL = {"name": "audit", "description": "Per-sentence audit.", "strict": True, "input_schema": {
    "type": "object", "required": ["results"], "additionalProperties": False, "properties": {"results": {"type": "array", "items": {
        "type": "object", "required": ["index", "supported", "issue"], "additionalProperties": False,
        "properties": {"index": {"type": "integer"}, "supported": {"type": "boolean"}, "issue": {"type": "string"}}}}}}}

BLIND_SYSTEM = """You are a peer reviewer: a university instructor in """ + course.SUBJECT + """ reviewing a worked-example chapter
before it goes to students. You read it from start to end exactly as the page shows it: the text with the equations displayed where
they appear. Earlier sections count as context for later ones. You do not see the lecture or the slides, only the page, so judge it
with your own subject knowledge and by checking the chapter against itself.
Look for, in this order of importance:
- factual: a statement, definition, equation or relationship that is wrong for the subject.
- calculation: arithmetic, substitution or rounding that does not give the number stated; a value that changes between sections.
- units: missing, wrong or inconsistent units.
- missing step: a jump a student could not follow, or a result used before it is obtained.
- undefined: a symbol or term used without being defined or obvious from earlier text.
- contradiction: two places in the chapter that disagree.
- unclear: wording a student would struggle to follow.
For each problem quote the exact words, say what is wrong and, where you can, what you would expect instead. Recompute numbers
yourself before reporting a calculation problem; if your recomputation agrees with the page, it is not a problem, so do not report
it. Figures are shown as [Figure displayed: ...] with their description; treat them as visible on the page.
Names and conventions differ between textbooks (for example which strain measure is called "Cauchy"). If the chapter is consistent
with itself and its figures but uses a convention different from the one you know, report it only as low confidence, phrased as a
question for the instructor. A worked example may start by finishing a previous problem; that is not a contradiction, but say so if
the page does not make the switch clear.
Set is_problem last, after your reasoning: true only if, having checked, something is really wrong or a student really could not
follow it. Rate severity: high = a student would learn something wrong or could not reproduce
the result; medium = a student would be confused; low = style or polish. Rate confidence: high only if you are sure from the page and
standard subject knowledge; low if it depends on context you cannot see (the lecture may have defined it). Report real problems
only; if the chapter is sound, return an empty list. Text between dollar signs is formatted math on the real page; judge its
meaning, not its markup. Do not rewrite the chapter."""
BLIND_TOOL = {"name": "report", "description": "Peer review findings.", "strict": True, "input_schema": {
    "type": "object", "required": ["stumbles"], "additionalProperties": False, "properties": {"stumbles": {"type": "array", "items": {
        "type": "object", "required": ["section", "quote", "kind", "severity", "confidence", "problem", "is_problem"],
        "additionalProperties": False,
        "properties": {"section": {"type": "integer", "description": "the [Section N] number the quote is in"},
                       "quote": {"type": "string"},
                       "kind": {"type": "string", "enum": ["factual", "calculation", "units", "missing step", "undefined",
                                                           "contradiction", "unclear"]},
                       "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                       "confidence": {"type": "string", "enum": ["high", "low"]},
                       "problem": {"type": "string"}, "is_problem": {"type": "boolean"}}}}}}}

def merge_states(secs, thr=0.8):
    """Progressive slide reveals / repeated states: when an earlier section's slide content is (almost) contained in the next
    section's slide, fold it into the next one (speech kept in time order, figures kept, later slide shown)."""
    import difflib
    norm = lambda m: re.sub(r"\s+", "", m)
    out = []
    for s in secs:
        for p in out:
            if len(norm(s["md"])) > 150 and difflib.SequenceMatcher(None, norm(p["md"]), norm(s["md"]), autojunk=False).ratio() >= 0.9:
                print(f"merging repeated slide: section {s['n']} folded into section {p['n']}")
                p["chunks"] = p["chunks"] + s["chunks"]
                p["figs"] = list(dict.fromkeys(p["figs"] + s["figs"]))
                break
        else:
            out.append(dict(s))
    return out


FIG_SYSTEM = """You write the accessible alternative for one figure in a university """ + course.SUBJECT_SHORT + """ textbook chapter built
from a recorded worked-example lecture. Goal: give a learner who cannot see the figure the same information and the same learning
task that the figure gives a sighted learner. Purpose before appearance.

Work in this order:
1. Purpose and context. From the slide text and the professor's words, decide what the learner is meant to notice, compare, follow,
   read off or calculate with this figure. Classify its function:
   - decorative: adds no instructional information (logo, background, ornament)
   - simple: purpose and needed information fit in a short phrase
   - complex: relationships, layout, sequence, hierarchy or values the learner must use
   - chart: a graph or plot
   - process: a sequence, flowchart, branch or loop
   - textual: mostly an image of text, a table or equations
2. Choose the minimum treatment that gives equivalent access:
   - decorative: alt "", caption "", long_description [].
   - simple: concise alt and caption; long_description [].
   - complex: concise alt for orientation, then a layered long description: first item = one-line overview, then items giving the
     parts and how they relate, in a stable reading order (left to right, top to bottom, or process order).
   - chart: chart type, subject, axes with units and scale, legend/encodings, the dominant pattern, and only the values the task needs.
   - process: one item per step, in order, with decision points, branches and loops.
   - textual: transcribe text exactly and write equations in LaTeX; do not narrate or paraphrase them.
3. Write from verified evidence only.
   - Preserve exact labels, symbols, values, units and technical terms (keep the term; do not replace it with a plain word).
   - Mention colour, line style, highlighting or position only when it carries meaning (e.g. red marks the reflected wave).
   - No pixel inventory: do not list shapes and lines without saying what they represent and how they relate.
   - Do not add conclusions, explanations, physics or values that the figure and context do not show. Do not complete a reasoning
     step or give an answer that the figure leaves for the learner.
   - Do not guess. If a label is illegible or a mark's meaning is unclear, say it is unclear in the description and put a focused
     question in sme_questions (e.g. "What does the dashed red line represent?"). Never state uncertain content as fact.
   - Do not repeat what the slide text already says unless needed to identify the figure's role.
   - Use the professor's words only to understand purpose. The description describes the figure; never report what the
     professor says, and never state a reading (e.g. a value read off a curve) that is not labelled; ask in sme_questions instead.
Return:
- function: one of the classes above.
- alt: under 140 characters: what the figure is and its purpose; no "image of" or "figure of". Empty only if decorative.
- caption: one sentence a textbook would print under the figure. Empty only if decorative.
- long_description: list of short items as described above (empty for decorative and simple). Symbols in LaTeX between single
  dollar signs.
- sme_questions: questions for the subject expert about anything unclear (usually empty)."""
FIG_TOOL = {"name": "describe", "description": "Accessible alternative for one figure.", "strict": True, "input_schema": {
    "type": "object", "required": ["function", "alt", "caption", "long_description", "sme_questions"], "additionalProperties": False,
    "properties": {
        "function": {"type": "string", "enum": ["decorative", "simple", "complex", "chart", "process", "textual"]},
        "alt": {"type": "string"}, "caption": {"type": "string"},
        "long_description": {"type": "array", "items": {"type": "string"}},
        "sme_questions": {"type": "array", "items": {"type": "string"}}}}}



TRANSCRIBE_SYSTEM = """You transcribe an image from a lecture slide that is mostly equations and text, so that it can be published as
real, accessible text instead of a picture. Transcribe exactly what is written; do not explain, solve, complete or reorder the
content beyond reading order.
- equations: every equation or relation, one per item, in LaTeX without dollar signs, in logical reading order (finish one column
  or derivation before the next; do not merge items that are side by side on the slide).
- text_lines: words, labels and short notes that are not equations, exactly as written (math inside them between single $ signs).
- uncertain: one short question per symbol, sign or number you cannot read with confidence (e.g. "Is the sign in Q = iQ_zp(a - a†)
  a minus?"). Never guess silently; if unsure, transcribe your best reading and ask here.
- remaining_graphic: true only if part of the image is a real graphic (a plot, diagram, level ladder, circuit) that text cannot
  replace; then graphic_description describes only that graphic part (short items, symbols in LaTeX between $ signs).
  Otherwise false and graphic_description is empty."""
TRANSCRIBE_TOOL = {"name": "transcribe", "description": "Exact transcription of a textual figure.", "strict": True, "input_schema": {
    "type": "object", "required": ["equations", "text_lines", "uncertain", "remaining_graphic", "graphic_description"],
    "additionalProperties": False, "properties": {
        "equations": {"type": "array", "items": {"type": "string"}}, "text_lines": {"type": "array", "items": {"type": "string"}},
        "uncertain": {"type": "array", "items": {"type": "string"}}, "remaining_graphic": {"type": "boolean"},
        "graphic_description": {"type": "array", "items": {"type": "string"}}}}}


READ_SYSTEM = """You read one lecture slide image for its mathematical meaning. It is often a printed or typed page with the
instructor's handwritten annotations (often red) on top. You also get the Mathpix OCR of the slide, which reads characters exactly
but splits handwriting into fragments and does not understand arrows or layout.
Return every equation or relation the slide states, in reading order, each as one complete LaTeX expression (no dollar signs):
- Read annotations by their meaning. An arrow from a symbol to an expression means the symbol is (defined as) that expression:
  write a definition such as K(z) = \\frac{\\cosh k(z+h)}{\\cosh kh}, never a product of the two. An arrow from an expression to a
  simpler one usually means "becomes, in this case": write it as a separate equation and say so in note.
- Join pieces of one equation that are split across lines or written around a figure. Do not split one equation into two.
- Keep the instructor's notation and numbers exactly, including any mistake the instructor made: you report what the slide says.
- Never add an equation, step, value or simplification that is not written on the slide. Subject knowledge may be used only to
  decipher what is written (which symbol a scrawl is, which way an arrow points), never to complete or correct the work.
- Prefer Mathpix's characters where they are clearly right; prefer the image where Mathpix garbled the handwriting.
For each equation: latex; source (printed | handwritten); note = how you interpreted an annotation or layout ('' if none);
confident = false if any symbol is a guess.
annotations: short plain sentences saying what each handwritten mark means (e.g. "Red arrow: K(z) is the ratio cosh k(z+h)/cosh kh").
questions: one short question per symbol or mark you cannot read with confidence (for the subject expert)."""
READ_TOOL = {"name": "read_slide", "description": "Equations and annotation meanings on one slide.", "strict": True, "input_schema": {
    "type": "object", "required": ["equations", "annotations", "questions"], "additionalProperties": False, "properties": {
        "equations": {"type": "array", "items": {"type": "object", "required": ["latex", "source", "note", "confident"],
            "additionalProperties": False, "properties": {"latex": {"type": "string"}, "source": {"type": "string", "enum": ["printed", "handwritten"]},
                                                          "note": {"type": "string"}, "confident": {"type": "boolean"}}}},
        "annotations": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}}}}}


REPAIR_SYSTEM = """A peer reviewer flagged an equation in a textbook chapter built from a lecture slide (attached). Decide whether
the published equation is a MISREADING of the slide (OCR or transcription error: a dropped symbol, an arrow read as
multiplication, fragments glued together, a truncated line) or what the slide really says.
- misread=true: give latex = the equation exactly as the slide states it (complete, no dollar signs), and say what was misread.
- misread=false: the slide really says this (even if the reviewer thinks it is wrong); latex = "".
Never correct the instructor's own math; only fix the reading of the slide."""
REPAIR_TOOL = {"name": "repair", "description": "Misread or not.", "strict": True, "input_schema": {
    "type": "object", "required": ["misread", "latex", "reason"], "additionalProperties": False,
    "properties": {"misread": {"type": "boolean"}, "latex": {"type": "string"}, "reason": {"type": "string"}}}}
VERIFY_EQ_SYSTEM = """You check one equation against a lecture slide image. matches_slide=true only if the proposed LaTeX states what
the slide shows (same symbols, same structure, same numbers; an arrow on the slide may be written as a definition). Do not judge
whether the physics is right."""
VERIFY_EQ_TOOL = {"name": "verdict", "description": "Does it match the slide?", "strict": True, "input_schema": {
    "type": "object", "required": ["matches_slide", "reason"], "additionalProperties": False,
    "properties": {"matches_slide": {"type": "boolean"}, "reason": {"type": "string"}}}}


def describe_figs(rec, ctx):
    """Accessibility text for each figure, written before the prose so the writer can place figures next to the sentences."""
    for f in rec["figs"]:
        fm, _ = llm_util.call(args.writer, FIG_SYSTEM, FIG_TOOL, ctx, effort="low", max_tokens=1600,
                              label=f"figure {f}", images=[site / "img" / f])
        if fm:
            rec["fig_meta"][f] = fm


CLARIFY_SYSTEM = """You settle open questions about one lecture slide before a textbook section is written from it. The questions
come from readers that each saw only ONE still image of the slide: an image reader (meaning) and OCR (exact characters). You now
get more evidence: several video frames from the time the slide was on screen (the instructor may write more, a menu or pointer
may move, the view may change), crops of the figures some questions are about, the OCR text, and what the professor said while
the slide was shown (speech recognition, may contain errors).
For each item:
- settled=true only if the evidence answers it clearly. Cite each piece of evidence: a frame or figure by its image number and
  exactly what it shows, or the professor's words quoted exactly as they appear in the transcript. Subject knowledge may help you
  decipher a symbol that is visible; it must never fill in what is not visible or not said.
- If the evidence does not answer it, settled=false, and answer says what is still unclear. Do not guess.
- action, for equation items (ids starting E, U, O): "keep" = the equation as listed is right; "replace" = give the full
  corrected LaTeX (no dollar signs) in latex, exactly as the slide states it; "drop" = it is not a separate equation on the slide
  (a fragment of another one, a duplicate, or a misread); "add" = an OCR-only equation the image reader missed that really is a
  separate equation on the slide (latex = its full form). For other items action = "none" and latex = "".
Report what the slide and the professor say, including any mistake the instructor made. Never correct the instructor's math.
MATH in answer and evidence: every symbol or expression as LaTeX between single dollar signs, e.g. $\\lambda \\to \\infty$,
$D = Ce^{-2kH}$; never bare LaTeX commands or Unicode math outside dollar signs. The latex field has no dollar signs."""
CLARIFY_TOOL = {"name": "settle", "description": "Answers to the open slide questions.", "strict": True, "input_schema": {
    "type": "object", "required": ["answers"], "additionalProperties": False, "properties": {"answers": {"type": "array", "items": {
        "type": "object", "required": ["id", "settled", "answer", "evidence", "action", "latex"], "additionalProperties": False,
        "properties": {"id": {"type": "string"}, "settled": {"type": "boolean"}, "answer": {"type": "string"},
                       "evidence": {"type": "array", "items": {"type": "object", "required": ["kind", "ref", "quote"], "additionalProperties": False,
                           "properties": {"kind": {"type": "string", "enum": ["frame", "figure", "speech", "ocr"]},
                                          "ref": {"type": "string"}, "quote": {"type": "string"}}}},
                       "action": {"type": "string", "enum": ["none", "keep", "replace", "drop", "add"]}, "latex": {"type": "string"}}}}}}}
CHECK_SYSTEM = """You check answers that another model gave to questions about a lecture slide. You see the same images and the
same transcript. For each answer: supported=true only if the cited evidence really shows or says what the answer claims (look at
the images yourself; check that quoted speech says it), and the answer does not go beyond the evidence. Otherwise false, with the reason."""
CHECK_TOOL = {"name": "check", "description": "Is each answer supported?", "strict": True, "input_schema": {
    "type": "object", "required": ["results"], "additionalProperties": False, "properties": {"results": {"type": "array", "items": {
        "type": "object", "required": ["id", "supported", "reason"], "additionalProperties": False,
        "properties": {"id": {"type": "string"}, "supported": {"type": "boolean"}, "reason": {"type": "string"}}}}}}}


def section_frames(rec, most=4):
    """frames extracted while this slide was on screen (every STEP s), evenly sampled, always including the slide image itself"""
    step = 20.0
    fm = pathlib.Path("work/frames_meta.json")
    if fm.exists():
        step = float(json.load(open(fm)).get("step", step))
    slide = site / "img" / f"slide_{rec['key']}.jpg"
    if rec.get("t0") is None or rec.get("t1") is None:
        return [slide] if slide.exists() else []
    allf = sorted(pathlib.Path("work/frames_crop").glob("c_*.jpg"))
    win = [f for f in allf if rec["t0"] - 1 <= (int(f.stem[2:]) - 1) * step <= rec["t1"] + 1]
    if len(win) > most - 1:
        win = [win[round(i * (len(win) - 1) / (most - 2))] for i in range(most - 1)] if most > 2 else win[-1:]
    out = [f for f in win if not (slide.exists() and f.name == f"c_{rec['key']}.jpg")]
    return out[:most - 1] + ([slide] if slide.exists() else [])


def clarify(rec, sec, prof, eqs, ctx):
    """Second look at what the slide readers could not settle from one image: more frames from the same stretch of video, the figure
    crops, and the professor's speech. An answer is kept only if a second model confirms the evidence (and quoted speech is really in
    the transcript). Settled items leave the SME's list but stay on the review page with their evidence; the rest stay open."""
    items = []  # (id, text, kind, ref)
    for k, q in enumerate(rec.get("reader_questions", []), 1):
        items.append((f"R{k}", f"(slide reader) {q}", "reader", q))
    ke = ko = ku = 0
    for c in rec.get("eq_check", []):
        if c.get("ocr_only"):
            ko += 1; items.append((f"O{ko}", f"Equation read by OCR only, not confirmed by the image reader: {c['latex']}  -- is it really a "
                                   "separate equation on the slide, as written?", "eq", c))
        elif not c["agrees"]:
            ke += 1; items.append((f"E{ke}", f"Equation as read from the image: {c['latex']}  -- the OCR read this part differently. "
                                   f"Which reading is right?" + (f" (reader's note: {c['note']})" if c["note"] else ""), "eq", c))
        elif not c["confident"]:
            ku += 1; items.append((f"U{ku}", f"The image reader was unsure of a symbol in: {c['latex']}" + (f" (note: {c['note']})" if c["note"] else ""), "eq", c))
    frames = section_frames(rec)
    figs_q = [f for f in rec["figs"] if rec["fig_meta"].get(f, {}).get("sme_questions")]
    images = frames + [site / "img" / f for f in figs_q]
    fig_no = {f: len(frames) + i + 1 for i, f in enumerate(figs_q)}
    kf = 0
    for f in figs_q:
        for q in rec["fig_meta"][f]["sme_questions"]:
            kf += 1; items.append((f"F{kf}", f"(about the figure in image {fig_no[f]}) {q}", "fig", (f, q)))
    if not items or not images:
        return
    step_lbl = [f"Image {i + 1}: video frame" + (" (the slide image the readers used)" if p.name.startswith("slide_") else
                f" at about {int(((int(p.stem[2:]) - 1) * 20) // 60)}:{int(((int(p.stem[2:]) - 1) * 20) % 60):02}") for i, p in enumerate(frames)]
    step_lbl += [f"Image {fig_no[f]}: figure crop" for f in figs_q]
    said = " ".join(c["text"] for c in prof)
    said_cut = " ".join(said.split()[:900])
    user = ("\n".join(step_lbl) + f"\n\nOCR OF THE SLIDE:\n{sec['md'][:4000]}\n\nEQUATIONS AS CURRENTLY READ:\n" +
            "\n".join(f"- {e}" for e in eqs) + f"\n\nWHAT THE PROFESSOR SAYS WHILE THIS SLIDE IS SHOWN:\n{said_cut}\n\nITEMS TO SETTLE:\n" +
            "\n".join(f"{i}: {t}" for i, t, _, _ in items))
    ans, _ = llm_util.call(args.writer, CLARIFY_SYSTEM, CLARIFY_TOOL, user, effort="medium", max_tokens=4000,
                           label=f"clarify s{sec['n']}", images=images)
    if not ans:
        return
    by = {a["id"]: a for a in ans["answers"]}
    plain = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    said_p = plain(said)
    cand = []
    for i, t, kind, ref in items:
        a = by.get(i)
        if not a or not a["settled"]:
            continue
        speech = [e for e in a["evidence"] if e["kind"] == "speech"]
        if speech and not all(covered(plain(e["quote"]), said_p) >= 0.8 for e in speech):  # quoted words must really be in the transcript
            a["settled"], a["answer"] = False, "quoted speech not found in the transcript; " + a["answer"]
            continue
        if kind == "eq" and a["action"] in ("replace", "add") and not a["latex"].strip():
            a["settled"] = False; continue
        if kind == "eq" and a["action"] == "drop" and not i.startswith("O"):
            # removing an equation the image reader saw is a content decision: never automatic (a handwritten limit such as
            # H -> infinity is easily mistaken for "just a note")
            a["settled"], a["answer"] = False, a["answer"] + " TRACER suggests removing it from the equations; a person should decide."
            continue
        if kind == "eq" and i.startswith("O") and a["action"] == "keep":  # "really on the slide" = add it, unless already listed
            if covered(eq_norm(ref["latex"]), eq_norm(" ".join(eqs))) >= 0.8:
                a["action"] = "drop"
            else:
                a["action"], a["latex"] = "add", a["latex"].strip() or ref["latex"]
        cand.append((i, t, a))
    ok = {}
    if cand:
        chk_in = (user.split("ITEMS TO SETTLE:")[0] + "ANSWERS TO CHECK:\n" + "\n\n".join(
            f"{i}: QUESTION: {t}\n   ANSWER: {a['answer']}" + (f"\n   PROPOSED LATEX: {a['latex']}" if a["latex"] else "") +
            f"\n   ACTION: {a['action']}\n   EVIDENCE: " + "; ".join(f"[{e['kind']} {e['ref']}] {e['quote']}" for e in a["evidence"])
            for i, t, a in cand))
        ch, _ = llm_util.call(llm_util.MODELS["verifier"], CHECK_SYSTEM, CHECK_TOOL, chk_in, max_tokens=2000, label=f"clarify check s{sec['n']}", images=images)
        ok = {x["id"]: x for x in (ch or {}).get("results", [])}
    rec["settled"], rec["clarify_open"] = [], {}
    n_set = 0
    for i, t, kind, ref in items:
        a = by.get(i)
        if not a:
            continue
        good = a["settled"] and ok.get(i, {}).get("supported")
        qtext = ref if kind == "reader" else ref["latex"] if kind == "eq" else ref[1]
        if not good:
            note = a["answer"] if not a["settled"] else f"{a['answer']} (not confirmed by the check: {ok.get(i, {}).get('reason', 'no verdict')})"
            rec["clarify_open"][qtext] = note
            continue
        n_set += 1
        if kind == "eq":
            t = {"O": "Equation read by the OCR only", "E": "Equation the two slide readers read differently",
                 "U": "Equation with a symbol the image reader was unsure of"}[i[0]] + f": ${ref['latex']}$"
        else:
            t = ref if kind == "reader" else ref[1]
        rec["settled"].append(dict(id=i, kind=kind, question=t, answer=a["answer"], orig=ref["latex"] if kind == "eq" else "",
                                   evidence=a["evidence"], action=a["action"], latex=a["latex"],
                                   fig=ref[0] if kind == "fig" else None, check=ok[i].get("reason", "")))
        if kind == "reader":
            rec["reader_questions"] = [q for q in rec["reader_questions"] if q != ref]
        elif kind == "fig":
            fm = rec["fig_meta"][ref[0]]
            fm["sme_questions"] = [q for q in fm["sme_questions"] if q != ref[1]]
            fm.setdefault("settled", []).append(f"{ref[1]} -> {a['answer']}")
        else:
            ref["settled"] = a["answer"]
            act, lx = a["action"], a["latex"].strip()
            if act == "replace" and ref["latex"] in eqs:
                eqs[eqs.index(ref["latex"])] = lx; ref["replaced_by"] = lx
            elif act == "drop" and ref["latex"] in eqs:
                eqs.remove(ref["latex"])
            elif act == "add" and eq_norm(lx) not in {eq_norm(e) for e in eqs}:
                eqs.append(lx)
    # figures whose open questions were settled get their description rewritten with the answers (no hedging left over)
    for f in {s["fig"] for s in rec["settled"] if s["kind"] == "fig"}:
        if "transcription" in rec["fig_meta"][f]:  # an image of text/equations already converted: keep its description as is
            continue
        fm2, _ = llm_util.call(args.writer, FIG_SYSTEM, FIG_TOOL, ctx + "\n\nSETTLED FROM THE VIDEO AND THE PROFESSOR'S WORDS (use these; do not "
                               "ask about them again):\n" + "\n".join(rec["fig_meta"][f]["settled"]), effort="low", max_tokens=1600,
                               label=f"figure {f} (settled)", images=[site / "img" / f])
        if fm2:
            fm2["settled"] = rec["fig_meta"][f]["settled"]
            fm2["sme_questions"] = [q for q in fm2["sme_questions"]
                                    if not any(covered(plain(q), plain(s)) >= 0.6 for s in fm2["settled"])]
            rec["fig_meta"][f] = fm2
    print(f"section {sec['n']}: clarify loop settled {n_set} of {len(items)} open item(s) from {len(frames)} frame(s) and the speech")


def human(flag):
    if flag.startswith("number(s) not in"):
        return "These numbers do not appear in the transcript passages or the slide this sentence is based on: " + flag.split("]")[0].split("[")[-1]
    if flag.startswith("cites missing"):
        return "This sentence could not be traced to a source passage."
    if flag.startswith("slide quote not found"):
        return "The slide wording quoted for this sentence was not found on the slide."
    if flag.startswith("built on unresolved"):
        return "Based on audio that was hard to make out. Listen to this part of the video before approving."
    if flag.startswith("auditor:"):
        return "Fact-check found something the sources do not support: " + flag[8:].strip()
    if flag.startswith("board and speech disagree:"):
        return "The board and the speech give different values: " + flag.split(":", 1)[1].strip() + " Check the video."
    if flag.startswith("provenance:"):
        m = re.search(r"passage s(\d+)c", flag)
        return ("This sentence matches a different part of the lecture" + (f" (section {m.group(1)})" if m else "") +
                " better than the passages it cites. It may be repeated, misplaced, or built from the wrong passage.")
    return flag


RECHECK_SYSTEM = """You re-check textbook sentences that automatic checks flagged, before they go to the professor. Each sentence was
written from a lecture transcript and its slide. For each flagged sentence you get the reason it was flagged, the transcript
chunks of the section (status unresolved = the speech engines did not agree; for those you also see what each engine heard),
and the slide content. Decide:
- supported: every claim in the sentence is stated by clear transcript words or by the slide. For a sentence built on an
  unresolved chunk, it is supported only if it does NOT depend on the disputed words: the claim is carried by clear words, by the
  slide, or by at least two engines hearing the same thing. Give the supporting quotes.
- rewrite: part of the sentence is supported and part is not (an added caveat, a remark about the transcript or the recording,
  a detail no source states, or a mis-cited passage). Give a rewritten sentence that keeps only what the quotes support, in the
  same warm textbook style. Never add content. Math as LaTeX between single dollar signs. Also give the supporting quotes.
- open: the evidence does not settle it (the claim rests on words no engine heard clearly). Say in one sentence what is unclear.
Quotes must be copied exactly from a transcript chunk (give its id) or from the slide (id "slide"). If the sentence's content
really comes from a different chunk than the one it cites, quote that chunk: that is fine. Judge content, not style.
The sentences are deliberately rewritten from speech into textbook prose: rewording, a synonym ("travelling" for a spoken
"operating"), tidy grammar and naming a thing the professor points at are all SUPPORTED when the meaning is the same. Never rewrite
to match the transcript's wording, and never make a sentence less clear or less complete. Choose rewrite only to remove or correct a
claim the sources do not support; the rewrite must otherwise keep the original sentence's wording."""
RECHECK_TOOL = {"name": "recheck", "description": "Verdict per flagged sentence.", "strict": True, "input_schema": {
    "type": "object", "required": ["results"], "additionalProperties": False, "properties": {"results": {"type": "array", "items": {
        "type": "object", "required": ["index", "verdict", "quotes", "rewrite", "note"], "additionalProperties": False,
        "properties": {"index": {"type": "integer"}, "verdict": {"type": "string", "enum": ["supported", "rewrite", "open"]},
                       "quotes": {"type": "array", "items": {"type": "object", "required": ["id", "quote"], "additionalProperties": False,
                           "properties": {"id": {"type": "string"}, "quote": {"type": "string"}}}},
                       "rewrite": {"type": "string"}, "note": {"type": "string"}}}}}}}
RECHECK_VERIFY = """You check verdicts on textbook sentences written from a spoken lecture. For each one you see the sentence (and a
proposed rewrite, if any) and the quotes offered as evidence (speech-recognition text, so wording is rough). ok=true only if the
quotes support the MEANING of every claim in the sentence (or the rewrite); paraphrase and tidier wording are fine. A rewrite must
add nothing the quotes do not state, and must only remove or correct a claim: ok=false for a rewrite that merely copies the spoken
wording or makes the sentence less clear. Otherwise ok=false, with the reason."""
RECHECK_VERIFY_TOOL = {"name": "verify", "description": "Is each verdict right?", "strict": True, "input_schema": {
    "type": "object", "required": ["results"], "additionalProperties": False, "properties": {"results": {"type": "array", "items": {
        "type": "object", "required": ["index", "ok", "reason"], "additionalProperties": False,
        "properties": {"index": {"type": "integer"}, "ok": {"type": "boolean"}, "reason": {"type": "string"}}}}}}}
RECHECKABLE = ("built on unresolved", "provenance:", "auditor:", "number(s) not in", "slide quote not found", "cites missing")
_GAPS = json.load(open("work/gaps/gaps.json", encoding="utf8"))["gaps"] if pathlib.Path("work/gaps/gaps.json").exists() else []


def engines_heard(c, chunks):
    """what each speech engine heard during an unresolved chunk (from the gap finder), so the checker can see what is disputed"""
    ts = sorted(x["t"] for x in chunks.values() if x.get("t") is not None and x["t"] > c.get("t", 0))
    t1 = ts[0] if ts else 1e9
    gs = [g for g in _GAPS if c.get("t", 0) - 1 <= g["t"] < t1]
    return "".join(f"\n      disputed at {g['t']:.0f}s: Whisper '{g['whisper']}' | Qwen '{g['qwen']}' | Parakeet '{g['parakeet']}'" for g in gs[:6])


def recheck(rec, sec, prof, chunks):
    """Second look at sentences the automatic checks flagged: is the sentence carried by clear words, by the slide or by engines
    that agree, or does it rest on disputed audio? Supported sentences are cleared; partly supported ones are rewritten to what the
    evidence states; the rest stay for a person, with a note. Quotes must be verbatim (checked in code) and a second model must agree."""
    plain = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    todo = [(i, s) for i, s in enumerate(rec["sentences"])
            if (s["flags"] and all(f.startswith(RECHECKABLE) for f in s["flags"])) or (s["uncertain"] and not s["flags"])]
    if not todo:
        return
    extra = set()
    for _, s in todo:
        for f in s["flags"]:
            m = re.search(r"passage (s\d+c\d+)", f)
            if m:
                extra.add(m[1])
    chunk_lines = "\n".join(f"[{c['id']} | {c['status']}] {c['text']}" + (engines_heard(c, {x['id']: x for x in prof}) if c["status"] in ("unresolved", "unintelligible") else "")
                            for c in prof)
    chunk_lines += "".join(f"\n[{x} | from another section] {chunks[x]['text']}" for x in sorted(extra) if x in chunks)
    reason = lambda s: "; ".join(human(f) for f in s["flags"]) or human("built on unresolved")
    user = (f"SLIDE CONTENT:\n{sec['md'][:5000]}\n\nTRANSCRIPT CHUNKS:\n{chunk_lines}\n\nFLAGGED SENTENCES:\n" +
            "\n".join(f"SENTENCE {i}: {s['text']}\n   cites: {', '.join(s['sources'])}\n   flagged because: {reason(s)}" for i, s in todo))
    out, _ = llm_util.call(args.writer, RECHECK_SYSTEM, RECHECK_TOOL, user, effort="medium", max_tokens=4000, label=f"recheck s{sec['n']}")
    if not out:
        return
    src_text = {c["id"]: c["text"] for c in prof} | {x: chunks[x]["text"] for x in extra if x in chunks} | {"slide": sec["md"]}
    by = {r["index"]: r for r in out["results"]}
    cand = []
    for i, s in todo:
        r = by.get(i)
        if not r:
            continue
        if r["verdict"] != "open":
            bad_q = [q for q in r["quotes"] if covered(plain(q["quote"]), plain(src_text.get(q["id"], ""))) < 0.85]
            if not r["quotes"] or bad_q:
                r["verdict"], r["note"] = "open", "the evidence quoted was not found word for word in the sources"
            elif r["verdict"] == "rewrite":
                allowed = set().union(*[nums(src_text.get(q["id"], "")) for q in r["quotes"]]) | nums(sec["md"])
                if not r["rewrite"].strip() or nums(r["rewrite"]) - allowed:
                    r["verdict"], r["note"] = "open", "the proposed rewrite was empty or added a number"
        if r["verdict"] != "open":
            cand.append((i, s, r))
    ok = {}
    if cand:
        vin = "\n\n".join(f"ITEM {i}: SENTENCE: {s['text']}" + (f"\n   REWRITE: {r['rewrite']}" if r["verdict"] == "rewrite" else "") +
                          "\n   QUOTES: " + " || ".join(f"[{q['id']}] {q['quote']}" for q in r["quotes"]) for i, s, r in cand)
        vf, _ = llm_util.call(llm_util.MODELS["verifier"], RECHECK_VERIFY, RECHECK_VERIFY_TOOL, vin, max_tokens=2000,
                              label=f"recheck verify s{sec['n']}")
        ok = {x["index"]: x for x in (vf or {}).get("results", [])}
    n_ok = n_rw = 0
    for i, s in todo:
        r = by.get(i)
        if not r:
            continue
        good = r["verdict"] != "open" and ok.get(i, {}).get("ok")
        if not good:
            s["recheck_note"] = r["note"] if r["verdict"] == "open" else f"TRACER's re-check was not confirmed: {ok.get(i, {}).get('reason', 'no verdict')}"
            continue
        if r["verdict"] == "rewrite":
            # a wording change is never automatic (tests showed rewrites that copied rough speech or added a detail): it is offered
            # on the review card as a one-click suggestion, and the flag stays until a person accepts or rejects it
            s["recheck_suggest"] = dict(text=r["rewrite"].strip(), quotes=r["quotes"], check=ok[i].get("reason", ""))
            s["recheck_note"] = "Part of this sentence is not supported by the sources; TRACER suggests a corrected wording below."
            n_rw += 1
            continue
        s["rechecked"] = dict(verdict=r["verdict"], flags=s["flags"] or ["built on unresolved chunk; marked uncertain"], quotes=r["quotes"],
                              before=s["text"], check=ok[i].get("reason", ""))
        n_ok += 1
        s["flags"], s["uncertain"] = [], False
    print(f"section {sec['n']}: re-check of {len(todo)} flagged sentence(s): {n_ok} supported, {n_rw} with a suggested rewording, "
          f"{len(todo) - n_ok - n_rw} left for review")


MODE = json.load(open(site / "mode.json"))["mode"] if (site / "mode.json").exists() else "slides"


def board_equations(secs):
    """Board lectures: every window photographs the whole board, so equations repeat and half-written or half-hidden versions
    appear. Keep an equation only (1) if it states a relation (fragments such as a lone 'dt' or 'V_A V_B' are labels),
    (2) if no equation within the next two windows contains it (a fuller version was written or uncovered), and (3) in the
    first window where it appears."""
    def side_by_side(e):  # 'A = B \qquad C = D' is two equations written next to each other on the board
        parts = [p.strip() for p in re.split(r"\\qquad|\\quad", e)]
        return parts if len(parts) > 1 and all(REL.search(p) for p in parts) else [e]
    per = [[p for e in split_md(s["md"])[1] for p in side_by_side(e)] for s in secs]
    cand = []
    for w, eqs in enumerate(per):
        for e in eqs:
            n = eq_norm(e)
            if len(n) >= 4 and (REL.search(e) or "\\begin{array}" in e or "\\frac" in e):
                cand.append((w, e, n))
    keep = []
    for w, e, n in cand:
        if any(w2 >= w and w2 <= w + 2 and n2 != n and len(n2) > len(n) and covered(n, n2) >= 0.9 for w2, _, n2 in cand):
            continue  # a fuller version exists nearby
        if any(covered(n, n2) >= 0.9 and len(n) <= len(n2) * 1.05 for _, _, n2 in keep):
            continue  # already shown earlier
        keep.append((w, e, n))
    for w, s in enumerate(secs):
        s["eqs"] = [e for w2, e, _ in keep if w2 == w]
    dropped = len(cand) - len(keep) + sum(len(x) for x in per) - len(cand)
    print(f"board equations: {sum(len(x) for x in per)} read across windows, {len(keep)} kept, {dropped} duplicates or fragments dropped")
    return secs


SEG_SYSTEM = """You divide a lecture transcript into the sections of a textbook chapter. The transcript comes in short numbered windows
(about 20 seconds each). Group consecutive windows so that each section teaches one idea a textbook would give its own heading:
a definition and its derivation, one worked example, one application. Never split an explanation or a worked example across
sections. Typical sections last 1.5 to 3 minutes. Every window belongs to exactly one section, in order; sections are contiguous.
Windows that only contain housekeeping (setup, asides, the end of a previous topic) join the neighbouring section."""
SEG_TOOL = {"name": "sections", "description": "Contiguous groups of windows.", "strict": True, "input_schema": {
    "type": "object", "required": ["sections"], "additionalProperties": False, "properties": {"sections": {"type": "array", "items": {
        "type": "object", "required": ["first_window", "last_window", "topic"], "additionalProperties": False,
        "properties": {"first_window": {"type": "integer"}, "last_window": {"type": "integer"}, "topic": {"type": "string"}}}}}}}


def topic_sections(secs):
    """Board lectures: merge fixed time windows into topic sections (one cheap call). Falls back to the windows if the answer
    is not a clean, contiguous cover."""
    doc = "\n".join(f"[W{i + 1} {int(s['t0'] // 60)}:{int(s['t0'] % 60):02d}] " +
                    " ".join(c["text"] for c in s["chunks"] if c["speaker"] == "professor") for i, s in enumerate(secs))
    if args.dry_run:
        return secs
    out, _ = llm_util.call(args.segmenter, SEG_SYSTEM, SEG_TOOL, doc, effort="low", max_tokens=1500, label="topic sections")
    groups = (out or {}).get("sections", [])
    pos = 1
    for g in groups:
        if g["first_window"] != pos or g["last_window"] < g["first_window"]:
            break
        pos = g["last_window"] + 1
    else:
        if pos == len(secs) + 1:
            merged = []
            for k, g in enumerate(groups, 1):
                part = secs[g["first_window"] - 1:g["last_window"]]
                lines = list(dict.fromkeys(l for s in part for l in s["md"].split("\n") if l.strip()))
                merged.append(dict(n=k, heading=g["topic"], key=part[-1]["key"], t0=part[0]["t0"], t1=part[-1]["t1"],
                                   md="\n".join(lines), figs=list(dict.fromkeys(f for s in part for f in s["figs"])),
                                   chunks=[c for s in part for c in s["chunks"]], eqs=[e for s in part for e in s["eqs"]],
                                   windows=[s["key"] for s in part]))
            for s in merged:  # inside one section, drop an equation that a fuller one in the same section contains
                ns = [eq_norm(e) for e in s["eqs"]]
                s["eqs"] = [e for e, n in zip(s["eqs"], ns) if not any(m != n and len(m) > len(n) and covered(n, m) >= 0.9 for m in ns)]
            print(f"topic sections: {len(secs)} windows -> {len(merged)} sections: " + "; ".join(s["heading"] for s in merged))
            return merged
    print("topic sections: segmenter answer was not a clean cover of the windows; keeping the windows")
    return secs


if MODE == "board":
    V2 = topic_sections(board_equations(V2))
    WRITER_SYSTEM += """
8. This lecture was given at a chalkboard. "The slide" is a photo of the board at the end of the section; parts may be hidden by
   the presenter, and it may still show earlier work. If the board and the transcript give different values for the same quantity
   (for example the board says 110 and the speech says 100), do not choose silently: write the value the transcript supports and
   describe the disagreement in `conflict` for that sentence. Otherwise leave `conflict` empty."""
    WRITE_TOOL = json.loads(json.dumps(WRITE_TOOL))
    _it = WRITE_TOOL["input_schema"]["properties"]["sentences"]["items"]
    _it["required"].append("conflict"); _it["properties"]["conflict"] = {"type": "string"}
else:
    V2 = merge_states(V2)
for _s in V2:  # the same figure filmed in several frames (presenter in front, slide shown again) is described and shown once
    if len(_s["figs"]) > 1:
        _kept, _ = figdedupe.dedupe([str(site / "img" / f) for f in _s["figs"]])
        _kept = [pathlib.Path(k).name for k in _kept]
        if len(_kept) < len(_s["figs"]):
            print(f"section {_s['n']}: {len(_s['figs'])} figure crops -> {len(_kept)} distinct figures")
        _s["figs"] = _kept
PROV = provenance.Index([c for s in V2 for c in s["chunks"] if c["speaker"] == "professor"])
all_chunks = {c["id"]: c for s in V2 for c in s["chunks"]}
results = []
for sec in V2:
    if args.only and sec["n"] != args.only:
        continue
    prof = [c for c in sec["chunks"] if c["speaker"] == "professor"]
    stud = [c for c in sec["chunks"] if c["speaker"] == "student"]
    words = sum(len(c["text"].split()) for c in prof)
    slide_text, eqs = split_md(sec["md"])
    if "eqs" in sec:  # board mode: de-duplicated, fragment-free equations
        eqs = sec["eqs"]
    rec = dict(n=sec["n"], heading=sec["heading"], key=sec["key"], md=sec["md"], slide_text=slide_text, equations=eqs,
               t0=sec.get("t0"), t1=sec.get("t1"),
               figs=sec["figs"], fig_meta={}, students=stud, sentences=[], stumbles=[], note="")
    results.append(rec)
    if words < 12:
        rec["note"] = "too little professor speech; skipped"
        print(f"section {sec['n']}: skipped ({words} words)"); continue
    if not args.dry_run:
        said = " ".join(c["text"] for c in prof).split()
        ctx = (f"SLIDE TITLE: {sec['heading']}\nSLIDE TEXT (OCR):\n" + "\n".join(slide_text[:12]) +
               "\n\nWHAT THE PROFESSOR SAYS WHILE THIS SLIDE IS SHOWN (speech recognition, may contain errors):\n" + " ".join(said[:300]))
        describe_figs(rec, ctx)
        # textual figures (an image of equations or text) become real text: their equations join the slide equations, so the
        # writer places them in the prose; the image stays only for any part that is a true graphic
        rec["converted_figs"] = []
        for f in list(rec["figs"]):
            fm = rec["fig_meta"].get(f)
            if not fm or fm.get("function") != "textual":
                continue
            tr, _ = llm_util.call(args.writer, TRANSCRIBE_SYSTEM, TRANSCRIBE_TOOL, ctx, effort="low", max_tokens=2500,
                                  label=f"transcribe {f}", images=[site / "img" / f])
            if not tr or not (tr["equations"] or tr["text_lines"]):
                continue
            fm["transcription"] = tr
            fix = lambda e: re.sub(r"\s*~\s*", r" \\sim ", e.replace("<<", r"\ll ").replace(">>", r"\gg "))  # plain ASCII relations
            tr = dict(tr, equations=[fix(e) for e in tr["equations"]])
            have = {eq_norm(e) for e in eqs}
            eqs += [e for e in tr["equations"] if eq_norm(e) not in have]
            slide_text += tr["text_lines"]
            sec["md"] += "\n" + "\n".join(tr["text_lines"] + [f"$${e}$$" for e in tr["equations"]])
            rec["md"] = sec["md"]
            fm["sme_questions"] = list(fm.get("sme_questions", [])) + tr["uncertain"]
            if tr["remaining_graphic"]:
                fm["long_description"] = tr["graphic_description"]  # describe only the graphic part; the rest is now text
            else:
                rec["converted_figs"].append(f)
        if rec["converted_figs"]:
            sec["figs"] = rec["figs"] = [f for f in rec["figs"] if f not in rec["converted_figs"]]
            print(f"section {sec['n']}: {len(rec['converted_figs'])} image(s) of equations/text converted to real text")
        # second slide reader: a vision model reads the slide image for meaning; Mathpix is the exact-character cross-check
        slide_img = site / "img" / f"slide_{sec['key']}.jpg"
        rec["annotations"], rec["eq_check"] = [], []
        if MODE != "board" and slide_img.exists() and (eqs or "$" in sec["md"]):
            rd, _ = llm_util.call(args.writer, READ_SYSTEM, READ_TOOL, "MATHPIX OCR OF THIS SLIDE (may be fragmentary or misread):\n"
                                  + sec["md"], effort="medium", max_tokens=3000, label=f"slide reader s{sec['n']}", images=[slide_img])
            if rd and rd["equations"]:
                ocr = list(eqs)
                new = [e["latex"] for e in rd["equations"]]
                ocr_all = eq_norm(" ".join(ocr) + " " + sec["md"])  # Mathpix often groups several equations in one block
                new_all = eq_norm(" ".join(new))
                for e in rd["equations"]:  # agreement per equation, for the review page
                    rec["eq_check"].append(dict(latex=e["latex"], agrees=covered(eq_norm(e["latex"]), ocr_all) >= 0.7,
                                                note=e["note"], confident=e["confident"]))
                for o in ocr:  # OCR equations the image reader did not confirm (often fragments); review only
                    if covered(eq_norm(o), new_all) < 0.6:
                        rec["eq_check"].append(dict(latex=o, agrees=False, note="read by OCR only; the image reader did not confirm it",
                                                    confident=False, ocr_only=True))
                eqs[:] = new  # rec["equations"] is the same list
                rec["annotations"] = rd["annotations"]
                rec["reader_questions"] = rd["questions"]
                sec["md"] += "\n" + "\n".join(f"$${x}$$" for x in new); rec["md"] = sec["md"]
                n_dis = sum(not c["agrees"] for c in rec["eq_check"])
                print(f"section {sec['n']}: slide reader {len(new)} equations, {n_dis} disagree with the OCR (flagged for review)")
        if MODE != "board" and not args.no_clarify:
            clarify(rec, sec, prof, eqs, ctx)
    fig_txt = "\n".join(f"FIG{i + 1}: {rec['fig_meta'][f]['alt']}" for i, f in enumerate(sec["figs"]) if f in rec["fig_meta"]) or "(none)"
    chunk_txt = "\n".join(f"[{c['id']} | {c['status']}] {c['text']}" for c in prof)
    eq_txt = "\n".join(f"EQ{i + 1}: {e}" for i, e in enumerate(eqs)) or "(none)"
    ann = rec.get("annotations") or []
    prompt = (f"SLIDE TITLE: {sec['heading']}\n\nSLIDE TEXT (OCR, may contain small errors):\n" + "\n".join(slide_text) +
              f"\n\nSLIDE EQUATIONS (LaTeX, read from the slide image and checked against OCR):\n{eq_txt}\n\n" +
              ("WHAT THE HANDWRITTEN MARKS MEAN (read from the slide image):\n" + "\n".join(f"- {a}" for a in ann) + "\n\n" if ann else "") +
              f"SLIDE FIGURES (shown to the learner):\n{fig_txt}\n\n"
              "The slide image is attached so you can see the layout and handwriting. Use it to understand the slide, not to add content.\n\n"
              f"TRANSCRIPT CHUNKS (professor only). Status: attested/majority = engines agree; slide/llm = chosen or corrected "
              f"using the slide; unresolved = garbled or disputed, may be wrong.\n{chunk_txt}")
    if args.dry_run:
        print(f"section {sec['n']}: {len(prof)} chunks, {words} words, {len(eqs)} equations, prompt ~{len(prompt) // 4} tokens"); continue

    simg = site / "img" / f"slide_{sec['key']}.jpg"
    out, _ = llm_util.call(args.writer, WRITER_SYSTEM, WRITE_TOOL, prompt, effort="medium", max_tokens=4000,
                           label=f"writer s{sec['n']}", images=[simg] if simg.exists() and MODE != "board" else [])
    if out is None:
        rec["note"] = "writer returned no tool call"; continue
    sents = out["sentences"]
    by_id = {c["id"]: c for c in prof}
    used_eq, used_fig = set(), set()
    for i, s in enumerate(sents):
        flags = []
        cited = [by_id[x] for x in s["sources"] if x in by_id]
        unknown = [x for x in s["sources"] if x not in by_id and x != "slide"]
        if not s["sources"] or unknown:
            flags.append(f"cites missing or unknown source: {unknown or 'none given'}")
        allowed = nums(sec["md"])
        for c in cited:
            allowed |= nums(c["text"])
        bad = sorted(nums(s["text"]) - allowed)
        if bad:
            flags.append(f"number(s) not in cited chunks or slide: {bad}")
        blob = re.sub(r"\s+", " ", sec["md"]).lower()
        blob_n = eq_norm(re.sub(r"[$]", "", blob))
        for q in s["slide_quotes"]:  # the same words or LaTeX, ignoring spacing, braces, dollar signs and \text-style markup
            if re.sub(r"\s+", " ", q).lower() not in blob and covered(eq_norm(re.sub(r"[$]", "", q.lower())), blob_n) < 0.9:
                flags.append(f"slide quote not found on slide: {q[:40]!r}")
        s["equations"] = [e for e in s["equations"] if 1 <= e <= len(eqs) and e not in used_eq]
        used_eq |= set(s["equations"])
        s["figures"] = [k for k in s.get("figures", []) if 1 <= k <= len(sec["figs"]) and sec["figs"][k - 1] not in used_fig]
        used_fig |= {sec["figs"][k - 1] for k in s["figures"]}
        if any(c["status"] in ("unresolved", "unintelligible") for c in cited) and not s["uncertain"]:
            s["uncertain"] = True; flags.append("built on unresolved chunk; marked uncertain")
        if s.get("conflict"):
            flags.append("board and speech disagree: " + s["conflict"])
        pv = provenance.check(PROV, s["text"], s["sources"])
        if pv:
            flags.append(pv)
        s["flags"], s["index"] = flags, i
    rec["unplaced_equations"] = [i + 1 for i in range(len(eqs)) if i + 1 not in used_eq]
    rec["placed_figs"] = sorted(used_fig)

    # evidence auditor (sees sources)
    aud_in = (f"SLIDE CONTENT:\n{sec['md']}\n\n" + "\n".join(
        f"SENTENCE {s['index']}: {s['text']}\n  CITED: " + " || ".join(by_id[x]["text"] for x in s["sources"] if x in by_id)
        for s in sents))
    aud, _ = llm_util.call(args.judge, AUDIT_SYSTEM, AUDIT_TOOL, aud_in, label=f"auditor s{sec['n']}")
    for r in (aud or {}).get("results", []):
        if 0 <= r["index"] < len(sents) and not r["supported"]:
            sents[r["index"]]["flags"].append("auditor: " + r["issue"])
    rec["sentences"] = sents
    if not args.no_clarify:
        recheck(rec, sec, prof, all_chunks)
    nflag = sum(1 for s in sents if s["flags"] or s["uncertain"])
    print(f"section {sec['n']}: {len(sents)} sentences, {nflag} flagged/uncertain, {len(used_eq)}/{len(eqs)} equations placed")

# blind reader: reads the WHOLE lecturette in order, exactly as the page shows it (equations where displayed)
if not args.dry_run and not args.only:
    doc = []
    for r in results:
        if not r["sentences"]:
            continue
        doc.append(f"[Section {r['n']}] {r['heading']}")
        def fig_txt_for(f):
            fm = r["fig_meta"].get(f) or {}
            if fm.get("function") == "decorative":
                return None
            ld = fm.get("long_description") or []
            ld = " ".join(ld) if isinstance(ld, list) else ld
            return f"[Figure displayed: {fm.get('caption') or fm.get('alt') or 'a slide figure'} Description: {ld}]"
        for f in [f for f in r["figs"] if f not in r.get("placed_figs", [])]:
            if fig_txt_for(f): doc.append(fig_txt_for(f))
        for s in r["sentences"]:
            doc.append(s["text"])
            for e in s["equations"]:
                doc.append(f"[Equation displayed:]  {r['equations'][e - 1]}")
            for k in s.get("figures") or []:
                if 0 < k <= len(r["figs"]) and fig_txt_for(r["figs"][k - 1]):
                    doc.append(fig_txt_for(r["figs"][k - 1]))
        for e in (r.get("unplaced_equations", []) if MODE != "board" else []):
            doc.append(f"[Equation displayed:]  {r['equations'][e - 1]}")
    bl, _ =llm_util.call(args.reviewer, BLIND_SYSTEM, BLIND_TOOL, "\n".join(doc), effort="medium", max_tokens=8000, label="peer review")
    withdrawn = [x for x in (bl or {}).get("stumbles", []) if x.get("is_problem") is False]
    if bl:
        bl["stumbles"] = [x for x in bl["stumbles"] if x.get("is_problem") is not False]
        print(f"peer review: {len(withdrawn)} findings withdrawn by the reviewer after checking")
    for x in (bl or {}).get("stumbles", []):
        # the reviewer's own words decide: a finding it calls rounding or minor is not a high-severity card for the professor
        if x.get("severity") in ("high", "medium") and re.search(
                r"\b(rounding|rounded|round-off|minor|negligible|cosmetic|small (?:difference|discrepancy)|within rounding)\b", x["problem"], re.I):
            x["severity_was"], x["severity"] = x["severity"], "low"
        n = x.get("section")
        tgt = next((r for r in results if r["n"] == n), None) or next((r for r in results if r["sentences"]), None)
        if tgt:
            tgt["stumbles"].append(x)
    print(f"blind reader (whole lecturette): {len((bl or {}).get('stumbles', []))} stumbles")
    # repair loop: an equation the peer reviewer flags is re-read against the slide image; a fix is kept only if a second model
    # confirms it matches the slide. The instructor's own mistakes are kept (and stay flagged): we report what the slide says.
    n_fix = 0
    for r in results:
        img = site / "img" / f"slide_{r['key']}.jpg"
        if MODE == "board" or not img.exists():
            continue
        for x in r["stumbles"]:
            if x.get("kind") not in ("factual", "contradiction", "unclear", "calculation", "missing step"):
                continue
            qn = eq_norm(x["quote"])
            idx = next((i for i, e in enumerate(r["equations"]) if len(qn) >= 4 and
                        max(covered(qn, eq_norm(e)), covered(eq_norm(e), qn)) >= 0.8), None)
            if idx is None:
                continue
            before = r["equations"][idx]
            rp, _ = llm_util.call(args.writer, REPAIR_SYSTEM, REPAIR_TOOL, f"EQUATION AS PUBLISHED: {before}\n\nREVIEWER'S CONCERN: {x['problem']}",
                                  effort="medium", max_tokens=1500, label=f"equation repair s{r['n']}", images=[img])
            if not rp or not rp["misread"] or not rp["latex"].strip():
                x["repair"] = "the slide shows it this way" if rp and not rp["misread"] else "not repaired"
                continue
            vf, _ = llm_util.call(args.judge, VERIFY_EQ_SYSTEM, VERIFY_EQ_TOOL, f"PROPOSED LATEX: {rp['latex']}\nEARLIER READING: {before}",
                                  max_tokens=600, label=f"repair check s{r['n']}", images=[img])
            if vf and vf["matches_slide"]:
                r["equations"][idx] = rp["latex"]; n_fix += 1; x["repair"] = "repaired"
                r.setdefault("repairs", []).append(dict(before=before, after=rp["latex"], reason=rp["reason"]))
            else:
                x["repair"] = "proposed fix not confirmed: " + ((vf or {}).get("reason", ""))
    print(f"equation repair loop: {n_fix} equation(s) repaired against the slide image")



if args.dry_run:
    sys.exit(0)

# ---------------- structure pass: objectives + synthesized titles; figure accessibility text
STRUCT_SYSTEM = """You are the instructional editor of a university textbook chapter that was built from a lecture. You see the finished
text of each section. Produce:
1. lecturette_title: a short synthesized title (3-9 words) naming the topic the learner studies, in sentence case. Not a question
   number and not a quote from the slides.
2. objectives: 2-4 learning objectives. Each starts with an action verb (Calculate, Explain, Identify, Apply, Determine ...) and
   completes "By the end of this lecturette, you will be able to ...". Each must be taught or demonstrated in the text below. Do
   not promise anything that is not there.
3. section_titles: for every section number, a synthesized heading (3-9 words, sentence case) that names the idea or step the
   section teaches, so a learner can scan the headings and see how the topic is organized. Do not copy slide titles, do not use
   question numbers like "4.9" alone, and no "continued". Headings in a chapter must be distinct and read as a logical progression.
Use only what is in the text. Plain text only; if a symbol is needed write it in LaTeX between single dollar signs."""
STRUCT_TOOL = {"name": "structure", "description": "Title, objectives and section titles.", "strict": True, "input_schema": {
    "type": "object", "required": ["lecturette_title", "objectives", "section_titles"], "additionalProperties": False, "properties": {
        "lecturette_title": {"type": "string"}, "objectives": {"type": "array", "items": {"type": "string"}},
        "section_titles": {"type": "array", "items": {"type": "object", "required": ["section", "title"], "additionalProperties": False,
                                                       "properties": {"section": {"type": "integer"}, "title": {"type": "string"}}}}}}}

STRUCT = {"lecturette_title": "", "objectives": []}
if not args.only:
    doc = []
    for r in results:
        if r["sentences"]:
            doc.append(f"[Section {r['n']}] (slide heading: {r['heading']})")
            doc += [s["text"] for s in r["sentences"]]
    st, _ = llm_util.call(args.writer, STRUCT_SYSTEM, STRUCT_TOOL, "\n".join(doc), effort="low", max_tokens=1500, label="structure")
    if st:
        STRUCT = st
        titles = {x["section"]: x["title"] for x in st["section_titles"]}
        for r in results:
            r["title"] = titles.get(r["n"]) or r["heading"]
for r in results:
    r.setdefault("title", r["heading"])

# ---------------- teaching layer (teaching edition only): built from the finished chapter text, never from outside knowledge
TEACH_SYSTEM = """You are the instructional editor of a university textbook chapter built from a recorded lecture. Add a study layer
for learners, using ONLY what the chapter text below says. Never add facts, formulas, numbers, examples or explanations that are
not in the chapter, even if they are true; every item must be answerable or checkable from the cited sentences.
Produce:
- takeaways: for each section, 1-3 short key takeaways restating what that section teaches.
- questions: 4-8 study questions across the chapter: recall, explain, or redo a calculation the chapter shows (same numbers).
  Each has a model answer written from the chapter, and cites the sentence ids it relies on.
- glossary: 3-10 technical terms the chapter uses and explains, each with a one-sentence definition taken from the chapter.
Sentence ids look like S2.4 (section 2, sentence 4). Plain, warm, concise wording.
MATH: every symbol, variable, number with a unit, exponent or expression must be LaTeX between single dollar signs, exactly as
the chapter writes it, e.g. $h = 9.89\\,\\mathrm{m}$, $2.07 \\times 10^{4}\\,\\mathrm{N/m^2}$, $T = 8\\,\\mathrm{s}$, $-\\rho\\,\\partial\\phi/\\partial t$.
Never write math as plain text (no 10^4, no Unicode symbols like ρ or ∂ outside dollar signs)."""
TEACH_TOOL = {"name": "teaching_layer", "description": "Study layer for the chapter.", "strict": True, "input_schema": {
    "type": "object", "required": ["takeaways", "questions", "glossary"], "additionalProperties": False, "properties": {
        "takeaways": {"type": "array", "items": {"type": "object", "required": ["section", "points", "sources"], "additionalProperties": False,
            "properties": {"section": {"type": "integer"}, "points": {"type": "array", "items": {"type": "string"}},
                           "sources": {"type": "array", "items": {"type": "string"}}}}},
        "questions": {"type": "array", "items": {"type": "object", "required": ["question", "answer", "kind", "sources"], "additionalProperties": False,
            "properties": {"question": {"type": "string"}, "answer": {"type": "string"},
                           "kind": {"type": "string", "enum": ["recall", "explain", "calculate"]},
                           "sources": {"type": "array", "items": {"type": "string"}}}}},
        "glossary": {"type": "array", "items": {"type": "object", "required": ["term", "definition", "sources"], "additionalProperties": False,
            "properties": {"term": {"type": "string"}, "definition": {"type": "string"},
                           "sources": {"type": "array", "items": {"type": "string"}}}}}}}}
TEACH_AUDIT = """You audit a study layer (takeaways, study questions with answers, glossary) written for a textbook chapter. For each
item you see the item and the chapter sentences it cites. supported=true only if everything in the item (every fact, number,
formula and claim in the question, answer, takeaway or definition) is stated in or directly follows from the cited sentences.
Anything added from outside the chapter, even if true, is unsupported."""
TEACH_AUDIT_TOOL = {"name": "audit", "description": "Per-item audit.", "strict": True, "input_schema": {
    "type": "object", "required": ["results"], "additionalProperties": False, "properties": {"results": {"type": "array", "items": {
        "type": "object", "required": ["id", "supported", "issue"], "additionalProperties": False,
        "properties": {"id": {"type": "string"}, "supported": {"type": "boolean"}, "issue": {"type": "string"}}}}}}}

TEACH = {"takeaways": [], "questions": [], "glossary": []}
if not args.only and not args.no_teaching and any(r["sentences"] for r in results):
    sid ={f"S{r['n']}.{i + 1}": s["text"] for r in results for i, s in enumerate(r["sentences"])}
    doc = "\n".join(f"[Section {r['n']}] {r['title']}\n" + "\n".join(f"  S{r['n']}.{i + 1} {s['text']}" for i, s in enumerate(r["sentences"]))
                    for r in results if r["sentences"])
    tl, _ = llm_util.call(args.writer, TEACH_SYSTEM, TEACH_TOOL, doc, effort="low", max_tokens=4000, label="teaching layer")
    if tl:
        items = ([(f"T{k}", " / ".join(x["points"]), x["sources"]) for k, x in enumerate(tl["takeaways"])] +
                 [(f"Q{k}", f"Q: {x['question']}  A: {x['answer']}", x["sources"]) for k, x in enumerate(tl["questions"])] +
                 [(f"G{k}", f"{x['term']}: {x['definition']}", x["sources"]) for k, x in enumerate(tl["glossary"])])
        aud_in = "\n\n".join(f"ITEM {i}: {txt}\n  CITED: " + " || ".join(sid.get(s, f"(unknown id {s})") for s in src) for i, txt, src in items)
        au, _ = llm_util.call(args.judge, TEACH_AUDIT, TEACH_AUDIT_TOOL, aud_in, label="teaching audit")
        verdict = {x["id"]: x for x in (au or {}).get("results", [])}
        for key, lst in (("T", tl["takeaways"]), ("Q", tl["questions"]), ("G", tl["glossary"])):
            for k, x in enumerate(lst):
                v = verdict.get(f"{key}{k}")
                bad_src = [s for s in x["sources"] if s not in sid]
                x["supported"] = bool(v and v["supported"]) and not bad_src
                x["issue"] = (v or {}).get("issue", "not audited") if not bad_src else f"cites unknown sentence(s) {bad_src}"
        TEACH = tl
        n_bad = sum(not x["supported"] for lst in TEACH.values() for x in lst)
        print(f"teaching layer: {len(TEACH['takeaways'])} takeaway groups, {len(TEACH['questions'])} questions, "
              f"{len(TEACH['glossary'])} glossary terms; {n_bad} item(s) not supported by the chapter (left out of the teaching edition)")
json.dump(TEACH, open(site / "teaching.json", "w"), indent=1)

# ---------------- reviewer edits (written by apply_review.py from the review center's feedback). Applied after every model step,
# so a re-run reuses the cached model calls and costs nothing; the edited sentence or equation is marked as decided by a person.
REVIEW = json.load(open(site / "review_edits.json", encoding="utf8")) if (site / "review_edits.json").exists() else {}
for ed in REVIEW.get("edits", []):
    m = re.fullmatch(r"([xe])(\d+)-(\d+)", ed["anchor"])
    r = next((r for r in results if m and r["n"] == int(m[2])), None)
    if not r:
        continue
    k = int(m[3])
    if m[1] == "x" and k < len(r["sentences"]):
        s = r["sentences"][k]
        s.setdefault("original", s["text"]); s["text"] = ed["text"]
        s["flags"], s["uncertain"], s["edited_by"] = [], False, ed.get("by", "reviewer")
    elif m[1] == "e" and 0 < k <= len(r["equations"]):
        r.setdefault("eq_edits", []).append(dict(before=r["equations"][k - 1], after=ed["text"], by=ed.get("by", "reviewer")))
        r["equations"][k - 1] = ed["text"]
if REVIEW.get("edits"):
    print(f"reviewer edits applied: {len(REVIEW['edits'])}")

# ---------------- plain-text math lint (math must be inside $...$ so it renders as MathML)
PLAIN_MATH = re.compile(r"\^|_\{?\w|[=<>±≈≤≥×·]|[α-ωΑ-Ω]|\b(?:omega|rho|delta|theta|lambda|sigma|eta|phi|pi)\b|"
                        r"\b(?:sinh|cosh|tanh|sin|cos|tan)\b|\d\s?(?:m|s|Pa|kPa|N|kg|Hz|m/s|rad|cm|km)\b")
for r in results:
    for s in r["sentences"]:
        bare = re.sub(r"\$[^$]+\$", " ", s["text"])
        m = PLAIN_MATH.search(bare)
        s["lint"] = [f"plain-text math outside math markup: {m.group(0)!r}"] if m else []

# unplaced slide equations: show one at the end of its section only if the text does not already state it and it was not
# already displayed earlier in the chapter (a slide shown twice, values written inline). The review page still lists all.
_shown = ""
for r in results:
    eqs_r = r.get("equations", [])
    inline = eq_norm(" ".join(m for s in r["sentences"] for m in re.findall(r"\$([^$]+)\$", s["text"])))
    for s in r["sentences"]:
        _shown += " " + " ".join(eq_norm(eqs_r[e - 1]) for e in s["equations"] if 0 < e <= len(eqs_r))
    keep = []
    for e in r.get("unplaced_equations", []):
        n = eq_norm(eqs_r[e - 1])
        if covered(n, inline) >= 0.8 or covered(n, _shown) >= 0.9:
            continue
        keep.append(e); _shown += " " + n
    r["unplaced_show"] = keep
json.dump(results, open(site / "textbook.json", "w"), indent=1)
json.dump(STRUCT, open(site / "structure.json", "w"), indent=1)

# ---------------- render
import latex2mathml.converter as l2m
esc = html.escape
FALLBACKS = {"n": 0}


def mml(latex, block=False):
    """LaTeX -> MathML (the page also loads MathJax, which renders MathML consistently and sizes it legibly)."""
    try:
        # latex2mathml turns \mathrm{m} into an italic <mi>; plain-letter units must stay upright
        latex = re.sub(r"\\mathrm\{\s*~?\s*([A-Za-z]+)\s*\}", r"\\text{\1}", latex)
        return l2m.convert(latex, display="block" if block else "inline")
    except Exception:
        FALLBACKS["n"] += 1
        return esc(("$$" + latex + "$$") if block else ("$" + latex + "$"))


def prose(text):
    """text with $...$ math -> escaped HTML with inline MathML."""
    parts = re.split(r"\$([^$]+)\$", text)
    return "".join(esc(p) if i % 2 == 0 else mml(p) for i, p in enumerate(parts))


EQNUM = {"on": False, "sec": 0, "k": 0}  # student editions number displayed equations (section.k)
CENTER = {"on": False, "n": 0}  # review center: every sentence, equation and figure carries an anchor (data-a) the review cards point to


def anc(a, raw=None):
    if not CENTER["on"]:
        return ""
    return f" data-a='{a}'" + (f" data-raw=\"{esc(raw, quote=True)}\"" if raw is not None else "")


def eq_html(eqs, idx):
    out = []
    for i in idx:
        if EQNUM["on"] and incomplete(eqs[i - 1]):
            continue
        a = anc(f"e{CENTER['n']}-{i}", eqs[i - 1])
        if EQNUM["on"]:
            EQNUM["k"] += 1
            n = f"{EQNUM['sec']}.{EQNUM['k']}"
            out.append(f"<div class='eq numbered' id='eq-{n}'{a}><div class=eqm>{mml(eqs[i - 1], True)}</div>"
                       f"<span class=eqn aria-label='Equation {n}'>({n})</span></div>")
        else:
            out.append(f"<div class=eq{a}>{mml(eqs[i - 1], True)}</div>")
    return "".join(out)


def body_html(r, review, counter):
    eqs, parts, para = r["equations"], [], []

    def flush():
        if para:
            parts.append("<p>" + " ".join(para) + "</p>"); para.clear()
    for si, s in enumerate(r["sentences"]):
        if s["new_paragraph"]:
            flush()
        if CENTER["on"]:
            sa = anc("x%d-%d" % (r["n"], si), s["text"])
            para.append(f"<span class=s{sa}>{prose(s['text'])}</span>")
        elif review and (s["flags"] or s["uncertain"]):
            only_unc = len(s["flags"]) == 1 and s["flags"][0].startswith("built on")
            cls = "unc" if (s["uncertain"] and (not s["flags"] or only_unc)) else "bad"
            tip = esc(" | ".join(human(f) for f in s["flags"]) or "Based on audio that was hard to make out.", quote=True)
            para.append(f"<span class='s {cls}' title=\"{tip}\">{prose(s['text'])}</span>")
        else:
            para.append(prose(s["text"]))
        if s["equations"]:
            flush(); parts.append(eq_html(eqs, s["equations"]))
        if s.get("figures"):
            flush(); parts.append(fig_html(r, counter, [r["figs"][k - 1] for k in s["figures"]]))
    flush()
    show = r.get("unplaced_equations", []) if review else r.get("unplaced_show", r.get("unplaced_equations", []))
    if show and (review or MODE != "board"):
        # board lectures: an equation nobody talks about is usually left over from earlier work on the board; review page only
        parts.append(eq_html(eqs, show))
    return "\n".join(parts)


CSS = """:root{--teal:#004D6C;--teal2:#006389;--cyan:#0D9EC4;--warm:#FBAE40;--ink:#1c2a31;--mut:#5b6b73;--paper:#fff;--tint:#eef6f9;--line:#cfe0e7;
--amber:#ffe8c2;--red:#ffd3d0;--eqbg:#f4f9fb;--eqline:#b9d4de}
@media(prefers-color-scheme:dark){:root{--teal:#0b6a8d;--teal2:#2a86a9;--cyan:#52bde0;--ink:#e6eef2;--mut:#a3b5be;--paper:#10191e;--tint:#16262e;
--line:#2c4450;--amber:#5a4012;--red:#6b2622;--eqbg:#15252d;--eqline:#2f5666}}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--ink);font:19px/1.75 Georgia,'Times New Roman',serif}
.skip{position:absolute;left:-999px}.skip:focus{left:8px;top:8px;background:#fff;color:#000;padding:8px;z-index:9}
header.band{background:var(--teal);color:#fff;padding:26px 16px 30px;border-bottom:6px solid var(--warm)}
header.band>*{max-width:820px;margin-left:auto;margin-right:auto}
header.band nav{font:14px system-ui,'Segoe UI',Calibri,sans-serif;opacity:.9}header.band nav a{color:#fff}
.eyebrow{font:600 13px system-ui,'Segoe UI',Calibri,sans-serif;letter-spacing:.09em;text-transform:uppercase;margin:18px auto 4px;color:#bfe6f3}
h1{font:700 34px/1.2 system-ui,'Segoe UI',Calibri,sans-serif;margin:0 auto}
main{max-width:820px;margin:0 auto;padding:8px 16px 48px}
h2{font:700 25px/1.3 system-ui,'Segoe UI',Calibri,sans-serif;color:var(--teal);margin:44px 0 6px;padding-bottom:6px;border-bottom:2px solid var(--line)}
@media(prefers-color-scheme:dark){h2{color:var(--cyan)}}
.draft{margin:24px 0 0;padding:12px 18px;border:2px solid #b4690e;border-radius:8px;background:var(--amber);font:16px/1.5 system-ui,'Segoe UI',Calibri,sans-serif}
.objectives{background:var(--tint);border:1px solid var(--line);border-top:5px solid var(--cyan);border-radius:6px;padding:4px 24px 12px;margin-top:30px}
.objectives h2{border:0;margin-top:18px;font-size:21px}.objectives p{margin:0 0 4px}.objectives li{margin:5px 0}
.eq{margin:20px auto;padding:16px 20px;background:var(--eqbg);border:1px solid var(--eqline);border-left:5px solid var(--cyan);
border-radius:10px;text-align:center;overflow:visible;box-shadow:0 1px 3px rgba(0,60,90,.08)}
.eq math,.eq mjx-container{font-size:120%}
figure{margin:26px 0;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--paper)}
figure img{max-width:100%;display:block;margin:0 auto;min-width:min(100%,300px);height:auto}
figcaption{font:15px/1.5 system-ui,'Segoe UI',Calibri,sans-serif;color:var(--mut);margin-top:10px}figcaption b{color:var(--teal2)}
details{font:15px/1.5 system-ui,'Segoe UI',Calibri,sans-serif;margin-top:8px}summary{cursor:pointer;color:var(--teal2)}
footer{max-width:820px;margin:0 auto;padding:18px 16px 40px;font:14px system-ui,sans-serif;color:var(--mut);border-top:1px solid var(--line)}
a{color:var(--teal2)}
/* review build */
.sum{font:16px/1.5 system-ui,'Segoe UI',Calibri,sans-serif;background:var(--tint);border:1px solid var(--line);border-radius:8px;padding:14px 18px;margin:22px 0}
.sum h2{font-size:18px;border:0;margin:0 0 6px;padding:0}.sum ul{margin:6px 0 0;padding-left:20px}
.key span.s{padding:2px 6px;border-radius:4px}.s.unc{background:var(--amber)}.s.bad{background:var(--red)}
.card{border:1px solid var(--line);border-radius:10px;margin:30px 0;background:var(--paper);overflow:hidden}
.card>h2{margin:0;padding:12px 18px;background:var(--tint);border:0;font-size:20px}
.card .meta{font:13px system-ui,sans-serif;color:var(--mut);margin-top:2px}
.cols{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,8fr);gap:22px;padding:16px 18px}
.slide img{width:100%;border:1px solid var(--line);border-radius:6px}.slide{position:sticky;top:10px;align-self:start}
.check{font:15px/1.5 system-ui,'Segoe UI',Calibri,sans-serif;border-top:1px dashed var(--line);margin-top:14px;padding-top:10px}
.check h3{font-size:15px;margin:0 0 4px;color:var(--teal2)}.check li{margin:6px 0}.chip{font:600 11px system-ui;background:var(--teal2);color:#fff;border-radius:10px;padding:1px 8px;margin-right:6px;text-transform:uppercase}
.chip.w{background:#b4690e}.chip.r{background:#a8322b}
.quote{color:var(--mut);font-style:italic}
.aside{border-left:3px solid #7aa874;padding-left:10px;color:var(--mut);font-size:15px}
@media(max-width:760px){.cols{grid-template-columns:1fr}.slide{position:static}h1{font-size:27px}body{font-size:18px}}
/* student editions: numbered sections and equations, contents list, Pressbooks-compatible boxes */
.secno{display:inline-block;min-width:1.6em;color:var(--cyan);font-weight:800;margin-right:.35em}
.toc{margin:26px 0 8px;padding:14px 20px;border:1px solid var(--line);border-radius:10px;font:16px/1.5 system-ui,'Segoe UI',Calibri,sans-serif}
.toc h2{font-size:13px;letter-spacing:.09em;text-transform:uppercase;color:var(--mut);border:0;margin:0 0 6px;padding:0}
.toc ol{list-style:none;margin:0;padding:0;columns:2 280px;column-gap:28px}.toc li{margin:3px 0;break-inside:avoid}
.toc a{text-decoration:none;color:var(--ink)}.toc a:hover{color:var(--teal2);text-decoration:underline}.toc .secno{font-size:14px}
.eq.numbered{display:grid;grid-template-columns:1fr auto;align-items:center;gap:12px}
.eq.numbered .eqm{min-width:0;overflow-x:auto;text-align:center}
.eqn{font:600 15px system-ui,'Segoe UI',Calibri,sans-serif;color:var(--mut);font-variant-numeric:tabular-nums}
.textbox{border:1px solid var(--line);border-radius:10px;margin:30px 0;overflow:hidden;background:var(--paper)}
.textbox__header{background:var(--tint);padding:10px 22px;border-bottom:1px solid var(--line)}
.textbox__title{font:700 17px/1.3 system-ui,'Segoe UI',Calibri,sans-serif;color:var(--teal);margin:0;padding:0;border:0}
.textbox__content{padding:6px 22px 10px}.textbox__content p{margin:10px 0 4px}.textbox__content li{margin:6px 0}
.textbox--learning-objectives{border-top:5px solid var(--cyan)}
.textbox--key-takeaways{border-left:5px solid var(--cyan);margin:24px 0 8px}
.textbox--key-takeaways .textbox__header{background:transparent;border:0;padding-bottom:0}
.textbox--exercises{border-top:5px solid var(--teal2)}.textbox--glossary{border-top:5px solid var(--mut)}
ol.qs{padding-left:22px}ol.qs>li{margin:14px 0}ol.qs details{margin:4px 0 0}ol.qs details p{margin:6px 0;color:var(--ink)}
dl.gloss{margin:8px 0}dl.gloss dt{font-weight:700;margin-top:10px;color:var(--teal2)}dl.gloss dd{margin:2px 0 0}
@media print{body{font-size:12pt;background:#fff;color:#000}header.band{background:#fff;color:#000;border-bottom:2px solid #000}
 header.band nav,.skip,.zoom::after{display:none}header.band .eyebrow{color:#444}.toc{break-after:avoid}
 .sec{display:block!important}.sec-side{position:static!important;max-height:none!important;overflow:visible!important}
 figure,.eq,.textbox{break-inside:avoid}details>summary{display:none}details{display:block}details>*{display:block}
 a{color:#000;text-decoration:none}dialog{display:none}}
/* figure panel (student build): figures the text does not point to sit beside their section and stay in view while it is read;
   below 1100px they follow the section text as a compact grid. Pure CSS; DOM order = reading order. */
.sec-side h3{font:600 13px system-ui,'Segoe UI',Calibri,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);margin:0 0 8px}
.sec-side figure{margin:0 0 14px;padding:10px}.sec-side figcaption{font-size:14px}
.sec-side .figs{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}.sec-side .figs figure{margin:0}
.zoom{all:unset;display:block;cursor:zoom-in;border-radius:4px}.zoom:focus-visible{outline:3px solid var(--cyan);outline-offset:2px}
.zoom img{width:100%;min-width:0;max-height:260px;object-fit:contain}
dialog.zoomed{border:1px solid var(--line);border-radius:10px;padding:14px;max-width:min(96vw,1100px);background:var(--paper);color:var(--ink)}
dialog.zoomed img{max-width:100%;max-height:78vh;display:block;margin:0 auto}dialog.zoomed::backdrop{background:rgba(0,0,0,.6)}
dialog.zoomed button{font:15px system-ui,sans-serif;margin-top:10px;padding:6px 14px;border:1px solid var(--line);border-radius:6px;background:var(--tint);color:var(--ink);cursor:pointer}
.sec-side{margin:18px 0 8px}
@media(min-width:1100px){
 body.panels main{max-width:1160px}body.panels header.band>*,body.panels footer{max-width:1128px}
 body.panels main>:not(.sec){max-width:770px}
 body.panels .sec{display:grid;grid-template-columns:minmax(0,770px) 320px;gap:38px;align-items:start}
 .sec-side{position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto;margin:52px 0 0;padding-right:4px}
 .sec-side .figs{grid-template-columns:1fr}}"""
ZOOM_JS = ("<dialog class=zoomed id=zoomdlg aria-label='Enlarged figure'><img alt=''><p></p><button type=button>Close</button></dialog>"
           "<script>(()=>{const d=document.getElementById('zoomdlg');if(!d.showModal)return;"
           "document.querySelectorAll('.zoom').forEach(b=>b.addEventListener('click',()=>{const i=b.querySelector('img');"
           "d.querySelector('img').src=i.src;d.querySelector('img').alt=i.alt;d.querySelector('p').textContent=b.dataset.cap||'';d.showModal()}));"
           "d.querySelector('button').onclick=()=>d.close();d.addEventListener('click',e=>{if(e.target===d)d.close()})})()</script>")
MJ = ("<script>window.MathJax={tex:{inlineMath:[['$','$']],displayMath:[['$$','$$']]},chtml:{scale:1.0}}</script>"
      "<script async src='https://cdnjs.cloudflare.com/ajax/libs/mathjax/3.2.2/es5/tex-mml-chtml.min.js' crossorigin='anonymous' "
      "integrity='sha384-M5jmNxKC9EVnuqeMwRHvFuYUE8Hhp0TgBruj/GZRkYtiMrCRgH7yvv5KY+Owi7TW' referrerpolicy='no-referrer'></script>")
# the integrity hash pins the exact MathJax file: if the CDN copy were ever changed, browsers refuse to run it


def where():
    n = pathlib.Path.cwd().name
    m = re.match(r"W(\d+)_Z(\d+)_T(\d+)", n)
    if m:
        return (f"Week {m[1]}", f"Lecture {m[2]}", f"Lecturette {m[3]}")
    m = re.match(rf"{course.CODE}_([\d-]+)_T(\d+)", n)
    if m:
        return (f"Session {m[1]}", "", f"Lecturette {m[2]}")
    return ("", "", "")


COURSE = course.course_name(pathlib.Path.cwd().name)


def crumb(*parts):
    return " &rsaquo; ".join(x for x in parts if x)


def disp(f):
    """display copy of a figure crop: contrast stretched (projected slides filmed in a room are washed out), small crops upscaled
    2x, lightly sharpened. The original crop is untouched, so OCR and cached figure descriptions keep using it."""
    from PIL import Image, ImageFilter, ImageOps
    src, dst = site / "img" / f, site / "img" / f"disp_{f}"
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        im = ImageOps.autocontrast(Image.open(src).convert("RGB"), cutoff=1)
        if im.width < 600:
            im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
        im.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=3)).save(dst, quality=92)
    return f"disp_{f}"


def fig_html(r, counter, only=None, panel=False):
    """panel=True: compact figures for the side panel; the image is a button that opens an enlarged view; decorative crops
    are left out of the panel (they carry no information)."""
    out = []
    for f in (only if only is not None else [f for f in r["figs"] if f not in r.get("placed_figs", [])]):
        counter[0] += 1
        fm = r["fig_meta"].get(f)
        fa = anc(f"f{r['n']}-{r['figs'].index(f) + 1}") if f in r["figs"] else ""
        if fm and fm.get("function") == "decorative":
            counter[0] -= 1
            if not panel:
                out.append(f"<figure{fa}><img src='img/{f}' alt=''></figure>")
        elif fm:
            ld = fm.get("long_description") or []
            if isinstance(ld, str):  # older runs stored one paragraph
                ld_html = f"<p>{prose(ld)}</p>" if ld else ""
            else:
                tag = "ol" if fm.get("function") == "process" else "ul"
                ld_html = f"<{tag}>" + "".join(f"<li>{prose(x)}</li>" for x in ld) + f"</{tag}>" if ld else ""
            det = (f"<details><summary>Long description of Figure {counter[0]}</summary>{ld_html}</details>" if ld_html else "")
            alt = fm["alt"].rstrip()
            if ld_html:
                alt = (alt if alt.endswith((".", "?", "!")) else alt + ".") + " Long description below."
            img = f"<img src='img/{disp(f)}' alt=\"{esc(alt, quote=True)}\">"
            if panel:
                img = (f"<button type=button class=zoom aria-label=\"Enlarge Figure {counter[0]}\" "
                       f"data-cap=\"{esc('Figure ' + str(counter[0]) + '. ' + fm['caption'], quote=True)}\">{img}</button>")
            out.append(f"<figure{fa}>{img}<figcaption><b>Figure {counter[0]}.</b> "
                       f"{prose(fm['caption'])}</figcaption>{det}</figure>")
        elif not panel:
            out.append(f"<figure{fa}><img src='img/{f}' alt=''></figure>")
    return "".join(out)


def locate(r, quote):
    """anchor of the sentence or equation a free-text quote (peer reviewer) points to; the section if neither matches well"""
    n, qn = r["n"], eq_norm(quote)
    for i, e in enumerate(r["equations"]):
        if len(qn) >= 4 and max(covered(qn, eq_norm(e)), covered(eq_norm(e), qn)) >= 0.8:
            return f"e{n}-{i + 1}"
    plain = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    best = max(((covered(plain(quote), plain(s["text"])), i) for i, s in enumerate(r["sentences"])), default=(0, 0))
    return f"x{n}-{best[1]}" if best[0] >= 0.6 else f"s{n}"


def eq_anchor(r, latex):
    k = next((i for i, e in enumerate(r["equations"]) if eq_norm(e) == eq_norm(latex)), None)
    return f"e{r['n']}-{k + 1}" if k is not None else f"s{r['n']}"


def items_for(r):
    """(kind, chip, quote html, plain-language description html, anchor). Anchors: x{section}-{sentence index}, e{section}-{equation
    number}, f{section}-{figure number}, s{section}."""
    items, n = [], r["n"]
    for i, s in enumerate(r["sentences"]):
        rn = (f" <span class=checked><b>TRACER re-checked the transcript, what each speech engine heard, and the slide:</b> "
              f"{prose(s['recheck_note'])}</span>" if s.get("recheck_note") else "")
        sg = s.get("recheck_suggest")
        if sg:
            ev = "; ".join(f"{'slide' if q['id'] == 'slide' else q['id']}: &ldquo;{prose(q['quote'])}&rdquo;" for q in sg["quotes"])
            rn += (f"<div class=suggest data-text=\"{esc(sg['text'], quote=True)}\"><b>TRACER suggests:</b> {prose(sg['text'])}"
                   f"<div class=mut>Based on {ev}</div></div>")
        for f in s["flags"]:
            items.append(("evidence", "w" if f.startswith(("built", "provenance")) else "r", prose(s["text"]), human(f) + rn, f"x{n}-{i}"))
            rn = ""
        if s["uncertain"] and not s["flags"]:
            items.append(("audio", "w", prose(s["text"]), human("built on unresolved") + rn, f"x{n}-{i}"))
        for l in s.get("lint", []):
            items.append(("math format", "w", prose(s["text"]), l, f"x{n}-{i}"))
    rank = {"high": 0, "medium": 1, "low": 2}
    for x in sorted(r["stumbles"], key=lambda x: rank.get(x.get("severity"), 1)):
        if "kind" in x:  # peer review
            chip = "r" if x["severity"] == "high" else "w" if x["severity"] == "medium" else ""
            sure = "" if x["confidence"] == "high" else " (reviewer unsure: may depend on the lecture)"
            items.append((f"peer review: {x['kind']}", chip, esc(x["quote"][:160]), esc(x["problem"]) + sure, locate(r, x["quote"])))
        else:  # older runs
            items.append(("reader stuck", "", esc(x["quote"][:160]), esc(x["problem"]), locate(r, x["quote"])))
    mj = pathlib.Path(f"work/mathpix/c_{r['key']}.json")
    if mj.exists():
        with open(mj, encoding="utf-8") as fh:
            mjd = json.load(fh)
        for c in mjd.get("fixed_changes", []):
            if re.search(r"uncertain|\?|unclear|guess|not sure|kept as", c, re.I):
                items.append(("handwriting", "w", "", "The reading of the handwriting was a guess: " + esc(c), f"s{n}"))
        if mjd.get("fix_rejected"):
            items.append(("handwriting", "w", "", "An automatic correction of this slide's OCR was rejected, so the raw OCR reading was "
                          f"used ({esc(mjd['fix_rejected'])}). Compare the equations in this section with the slide.", f"s{n}"))
    for e in r.get("unplaced_equations", []):
        if e not in r.get("unplaced_show", r.get("unplaced_equations", [])) and MODE != "board":
            items.append(("not discussed", "", prose("$" + r["equations"][e - 1][:80] + "$"),
                          "Slide equation not attached by the writer and not shown to students: the text already states it, or it was "
                          "shown earlier in the chapter. Add it back if students need it here.", f"s{n}"))
            continue
        items.append(("not discussed", "w", prose("$" + r["equations"][e - 1][:80] + "$"),
                      "This board equation is not mentioned in the text. It is shown here only, not in the student version "
                      "(it is often left over from earlier work on the board). Add it back if students need it."
                      if MODE == "board" else
                      "This slide equation is not mentioned in the text, so it appears at the end of the section. Keep it, move it, or drop it.",
                      f"s{n}" if MODE == "board" else f"e{n}-{e}"))
    shown = [e for s in r["sentences"] for e in s["equations"]] + (r.get("unplaced_show", []) if MODE != "board" else [])
    for e in shown:
        if 0 < e <= len(r["equations"]) and incomplete(r["equations"][e - 1]):
            items.append(("incomplete equation", "", prose("$" + r["equations"][e - 1][:100] + "$"),
                          "This slide equation is unfinished (it ends in an equals sign, or is empty), so it is left out of the "
                          "student pages. Add the missing part if students need it.", f"s{n}"))
    for rp in r.get("repairs", []):
        items.append(("equation repaired", "w", prose("$" + rp["after"][:120] + "$"),
                      "The peer reviewer flagged this equation; a check against the slide image found it had been misread and fixed it "
                      f"(was {prose('$' + rp['before'][:100] + '$')}). {esc(rp['reason'])}", eq_anchor(r, rp["after"])))
    checked = lambda key: (f" <span class=checked><b>TRACER checked the other video frames and the speech:</b> "
                           f"{prose(r['clarify_open'][key])}</span>" if key in r.get("clarify_open", {}) else "")
    for c in r.get("eq_check", []):
        if c.get("settled"):
            continue
        if c.get("ocr_only"):
            items.append(("OCR fragment", "", prose("$" + c["latex"][:100] + "$"),
                          "The OCR read this, but the slide-image reader did not confirm it, so it is not in the text. Usually a fragment of "
                          "handwriting; add it back if the slide really shows it." + checked(c["latex"]), f"s{n}"))
        elif not c["agrees"] or not c["confident"]:
            why = "read from the slide image; the OCR read it differently" if not c["agrees"] else "the image reader was unsure of a symbol"
            items.append(("equation check", "w", prose("$" + c["latex"][:120] + "$"),
                          f"This equation was {why}. Check it against the slide." + (f" Reading: {esc(c['note'])}" if c["note"] else "") +
                          checked(c["latex"]), eq_anchor(r, c["latex"])))
    for q in r.get("reader_questions", []):
        items.append(("equation check", "w", "", "Question for the SME (slide reader): " + prose(q) + checked(q), f"s{n}"))
    for f in r.get("converted_figs", []):
        fm = r["fig_meta"].get(f, {})
        n_eq = len(fm.get("transcription", {}).get("equations", []))
        items.append(("converted", "w", f"<img src='img/{disp(f)}' alt='' style='max-width:280px;display:block;margin:4px 0'>",
                      f"This image of equations and text was converted to real text ({n_eq} equations); students see the text, not "
                      "the image. Check the transcribed equations in this section against the image.", f"s{n}"))
        for q in fm.get("sme_questions", []):
            items.append(("figure", "w", "", "Question for the SME (converted image): " + prose(q), f"s{n}"))
    for k, f in enumerate(r["figs"], 1):
        if r["sentences"] and f not in r.get("placed_figs", []):
            items.append(("not discussed", "w", "", f"A slide figure ({esc(r['fig_meta'].get(f, {}).get('alt', f))}) is not discussed in the text, so it appears at the top of the section.", f"f{n}-{k}"))
        if f not in r["fig_meta"]:
            items.append(("figure", "w", "", f"Figure {esc(f)} has no description yet.", f"f{n}-{k}"))
        for q in r["fig_meta"].get(f, {}).get("sme_questions", []):
            items.append(("figure", "w", esc(r["fig_meta"][f].get("alt", f)), "Question for the SME: " + prose(q) + checked(q), f"f{n}-{k}"))
    if r.get("t0") is not None and r.get("t1") is not None:
        for p in POSSIBLE_STUDENT:
            if r["t0"] <= p["a"] < r["t1"]:
                items.append(("possible student", "", esc(p["words"][:160]),
                              f"At {int(p['a'] // 60)}:{int(p['a'] % 60):02} the voice may not be the professor's (about {p['b'] - p['a']:.0f} s). "
                              "Listen there. If a student is speaking, check that the text does not present their words as the professor's.", f"s{n}"))
    return items


def textbox(kind, title, inner, hid):
    """Pressbooks-compatible box markup (class names match Pressbooks textboxes, so content pastes into a Pressbooks chapter)"""
    teach = kind in ("key-takeaways", "exercises", "glossary")
    extra = (" teach" if teach and CENTER["on"] else "")
    return (f"<section class='textbox textbox--{kind}{extra}' aria-labelledby='{hid}'{anc('t-' + hid) if teach else ''}><header class=textbox__header>"
            f"<h2 class=textbox__title id='{hid}'>{title}</h2></header><div class=textbox__content>{inner}</div></section>")


def render_final(teaching=False, center=False):
    """student editions. teaching=False: the lecture only. teaching=True: the same chapter plus the audited study layer.
    center=True: the teaching edition's content only (no page shell), with anchors, for the review center."""
    wk, lec, lt = where()
    body, counter, toc = [], [0], []
    take = {x["section"]: x for x in TEACH["takeaways"] if x.get("supported")} if teaching else {}
    EQNUM["on"] = True
    CENTER["on"] = center
    shown = [r for r in results if r["sentences"]]
    for no, r in enumerate(shown, 1):
        EQNUM["sec"], EQNUM["k"] = no, 0
        CENTER["n"] = r["n"]
        text = body_html(r, False, counter)   # inline figures are numbered first, in reading order
        side = fig_html(r, counter, panel=True)
        if side:
            side = (f"<aside class=sec-side aria-label='Figures for {esc(re.sub(r'<[^>]+>|[$]', '', r['title']), quote=True)}' tabindex=0>"
                    f"<h3>Figures for this section</h3><div class=figs>{side}</div></aside>")
        if r["n"] in take:
            text += textbox("key-takeaways", "Key takeaways", "<ul>" + "".join(f"<li>{prose(p)}</li>" for p in take[r["n"]]["points"]) + "</ul>",
                            f"kt{r['n']}")
        toc.append(f"<li><a href='#s{r['n']}'><span class=secno>{no}</span>{prose(r['title'])}</a></li>")
        body.append(f"<section class=sec aria-labelledby='s{r['n']}'{anc('s%d' % r['n'])}><div class=sec-main><h2 id='s{r['n']}'><span class=secno>{no}</span>"
                    f"{prose(r['title'])}</h2>{text}</div>{side}</section>")
    EQNUM["on"] = False
    panels = any("class=sec-side" in b for b in body)
    objs = "".join(f"<li>{prose(o)}</li>" for o in STRUCT["objectives"])
    obj = textbox("learning-objectives", "Learning objectives",
                  f"<p>By the end of this lecturette, you will be able to:</p><ul>{objs}</ul>", "obj") if objs else ""
    extra = ""
    if teaching:
        qs = [q for q in TEACH["questions"] if q.get("supported")]
        gl = sorted([g for g in TEACH["glossary"] if g.get("supported")], key=lambda g: g["term"].lower())
        if qs:
            extra += textbox("exercises", "Study questions", "<ol class=qs>" + "".join(
                f"<li><p>{prose(q['question'])}</p><details><summary>Show answer</summary><p>{prose(q['answer'])}</p></details></li>"
                for q in qs) + "</ol>", "studyq")
            toc.append("<li><a href='#studyq'><span class=secno>&#x2022;</span>Study questions</a></li>")
        if gl:
            extra += textbox("glossary", "Glossary", "<dl class=gloss>" + "".join(
                f"<dt>{prose(g['term'])}</dt><dd>{prose(g['definition'])}</dd>" for g in gl) + "</dl>", "gloss")
            toc.append("<li><a href='#gloss'><span class=secno>&#x2022;</span>Glossary</a></li>")
    nav = f"<nav class=toc aria-labelledby=toch><h2 id=toch>On this page</h2><ol>{''.join(toc)}</ol></nav>" if len(toc) > 1 else ""
    t = STRUCT["lecturette_title"] or args.title
    if center:
        CENTER["on"] = False
        return f"<h1 class=doctitle>{prose(t)}</h1>{obj}{nav}{''.join(body)}{extra}"
    edition = " &middot; Teaching edition" if teaching else ""
    # until the instructor's review has been applied (apply_review.py), the page says it is a draft: flagged problems are
    # hidden from student pages by design, so an unreviewed page must not read as final
    draft = ("" if REVIEW.get("professor_reviewed") else
             "<div class=draft role=note><b>Draft.</b> This page was generated from a lecture recording and has not yet been reviewed "
             "by the instructor. It may contain errors.</div>")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(t)} | {COURSE}</title><style>{CSS}</style>{MJ}</head><body{' class=panels' if panels else ''}><a class=skip href='#main'>Skip to main content</a>"
            f"<header class=band><nav aria-label=Breadcrumb><a href='../../../index_student.html'>{COURSE}</a>{' &rsaquo; ' + crumb(wk, lec) if (wk or lec) else ''}</nav>"
            f"<p class=eyebrow>{lt}{edition}</p><h1>{prose(t)}</h1></header><main id=main>{draft}{obj}{nav}{''.join(body)}{extra}</main>"
            f"<footer>{' &middot; '.join(x for x in (COURSE, wk, lec, lt) if x)}</footer>{ZOOM_JS if panels else ''}</body></html>")


def render_review():
    wk, lec, lt = where()
    counter, cards, jump, total = [0], [], [], 0
    for r in results:
        its = items_for(r)
        total += len(its)
        if not r["sentences"]:
            cards.append(f"<div class=card><h2>Section {r['n']}: {prose(r['heading'])}<div class=meta>Slide shown {r['t0']}-{r['t1']} s. "
                         f"{esc(r['note'] or 'No text generated.')} (omitted from the student version)</div></h2></div>")
            continue
        if its:
            jump.append(f"<li><a href='#c{r['n']}'>Section {r['n']}: {prose(r['title'])}</a> - {len(its)} item(s)</li>")
        chk = ""
        if its:
            lis = "".join(f"<li><span class='chip {c}'>{k}</span>{('<span class=quote>' + q + '</span><br>') if q else ''}{d}</li>"
                          for k, c, q, d, _a in its)
            chk = f"<div class=check><h3>For you to check ({len(its)})</h3><ul>{lis}</ul></div>"
        stud = "".join(f"<p class=aside><b>Student comment (not in the student version):</b> {esc(c['text'])}</p>" for c in r["students"])
        fixes = ""
        mj = pathlib.Path(f"work/mathpix/c_{r['key']}.json")
        if mj.exists():
            fc = json.load(open(mj, encoding="utf-8")).get("fixed_changes", [])
            if fc:
                fixes = ("<details class=check><summary>Handwriting corrections the pipeline made on this slide</summary><ul>"
                         + "".join(f"<li>{esc(c)}</li>" for c in fc) + "</ul></details>")
        cards.append(
            f"<section class=card id='c{r['n']}'><h2>Section {r['n']}: {prose(r['title'])}<div class=meta>Slide heading: {prose(r['heading'])}"
            f" &middot; slide shown {r['t0']}-{r['t1']} s</div></h2><div class=cols>"
            f"<div class=slide><a href='img/slide_{r['key']}.jpg' target=_blank><img src='img/slide_{r['key']}.jpg' alt='Slide for section {r['n']}'></a></div>"
            f"<div>{fig_html(r, counter)}{body_html(r, True, counter)}{stud}{chk}{fixes}</div></div></section>")
    t = STRUCT["lecturette_title"] or args.title
    objs = "".join(f"<li>{prose(o)}</li>" for o in STRUCT["objectives"])
    summ = (f"<div class=sum><h2>How to review this page</h2><p>This is the same text students will see, plus marks for you. "
            f"Each section shows its slide on the left. Sentences that need a decision are highlighted: "
            f"<span class=key><span class='s unc'>amber</span> = based on audio that was hard to make out; "
            f"<span class='s bad'>red</span> = a fact-check found something the sources do not support</span>. "
            f"Hover a highlighted sentence for the reason. Under each section, <b>For you to check</b> lists every item in plain language. "
            f"Nothing on this page except the text, figures and equations appears in the student version.</p>"
            f"<p><b>{total} item(s) to check in this lecturette.</b></p>{('<ul>' + ''.join(jump) + '</ul>') if jump else ''}</div>")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Review: {esc(t)}</title><style>{CSS}</style>{MJ}</head><body><header class=band>"
            f"<nav><a href='../../../index_review.html'>Review index</a>{' &rsaquo; ' + crumb(wk, lec) if (wk or lec) else ''}</nav>"
            f"<p class=eyebrow>Review version &middot; {lt}</p><h1>{prose(t)}</h1></header><main>{summ}"
            f"<div class=sum><h2>Learning objectives (as written for students)</h2><ul>{objs}</ul></div>{''.join(cards)}{teach_review()}</main></body></html>")


def teach_review():
    """what the teaching edition adds, with the audit result, so the instructor can approve it"""
    if not any(TEACH.values()):
        return ""
    mark = lambda x: "" if x.get("supported") else f"<br><span class='chip r'>left out</span> {esc(x.get('issue', ''))}"
    tk = "".join(f"<li><b>Section {x['section']}:</b> {' · '.join(prose(p) for p in x['points'])}{mark(x)}</li>" for x in TEACH["takeaways"])
    qs = "".join(f"<li>{prose(q['question'])}<br><span class=quote>Answer: {prose(q['answer'])}</span>{mark(q)}</li>" for q in TEACH["questions"])
    gl = "".join(f"<li><b>{prose(g['term'])}</b>: {prose(g['definition'])}{mark(g)}</li>" for g in TEACH["glossary"])
    return (f"<section class=card id=teach><h2>Teaching layer (teaching edition only)<div class=meta>Written from this chapter's text and "
            f"checked against the sentences each item cites. Items marked 'left out' failed that check and are not in the teaching "
            f"edition. The standard student edition contains none of this.</div></h2><div style='padding:12px 18px'>"
            f"<h3>Key takeaways</h3><ul>{tk}</ul><h3>Study questions</h3><ol>{qs}</ol><h3>Glossary</h3><ul>{gl}</ul></div></section>")


def render_center():
    """one-page review center: the teaching edition on the left (exactly what students get, with anchors), review cards on the
    right, each linked to the sentence, equation, figure or box it is about. Decisions and comments are kept in the browser and
    saved to a feedback file that apply_review.py reads. Static page: works from a synced OneDrive folder, no server, no account."""
    import hashlib
    left = render_final(teaching=True, center=True)
    shown = [r for r in results if r["sentences"]]
    secno = {r["n"]: i for i, r in enumerate(shown, 1)}
    GROUP = lambda kind, a: ("speech" if kind in ("audio", "possible student", "student remark") else
                             "equations" if kind in ("equation check", "equation repaired", "handwriting", "converted") or a.startswith("e") else
                             "figures" if kind == "figure" or a.startswith("f") else
                             "teaching" if kind == "teaching" else "text")
    mmss = lambda t: f"{int(t // 60)}:{int(t % 60):02}"
    cards = []

    DESIGNER_ONLY = {"not discussed", "math format", "OCR fragment", "not attempted", "incomplete equation", "figure sources"}  # housekeeping: the professor's version leaves these out

    def card(r, kind, chip, q, d, a, extra="", group=None):
        plain = re.sub(r"<[^>]+>", "", f"{q}|{d}")
        cid = hashlib.sha1(f"{r['n'] if r else 0}|{kind}|{a}|{plain}".encode()).hexdigest()[:10]
        aud = " data-aud='designer'" if kind in DESIGNER_ONLY else ""
        if group == "settled":
            aud = ""
        where_ = ((f"Section {secno[r['n']]}" if r and r["n"] in secno else "Not in the textbook") if r else
                  "Whole recording" if kind == "not attempted" else "Teaching edition")
        if r and r.get("t0") is not None:
            where_ += f" &middot; video {mmss(r['t0'])}" + (f"&ndash;{mmss(r['t1'])}" if r.get("t1") is not None else "")
        slide = site / "img" / f"slide_{r['key']}.jpg" if r else None
        ev = (f"<details class=ev><summary>Compare with the slide</summary><button type=button class=zoom data-cap='Slide for {where_}'>"
              f"<img loading=lazy src='img/slide_{r['key']}.jpg' alt='Slide for this section'></button></details>"
              if slide and slide.exists() else "")
        tag = "<span class=aud>designer only</span>" if aud else ""
        cards.append(f"<article class='card{' settled' if group == 'settled' else ''}' data-id='{cid}' data-anchor='{a}' data-group='{group or GROUP(kind, a)}' "
                     f"data-kind='{esc(kind, quote=True)}'{aud} tabindex=0><header><span class=num></span><span class='chip {chip}'>{esc(kind)}</span>{tag}<span class=where>{where_}</span>"
                     f"<span class=state></span></header>{('<div class=q>' + q + '</div>') if q else ''}<div class=d>{d}</div>{extra}{ev}</article>")

    for r in results:
        if not r["sentences"]:
            card(r, "omitted section", "", "", f"Slide heading: {prose(r['heading'])}. {esc(r['note'] or 'No text was generated for this part of the video.')} "
                 "It is left out of the textbook. Check the video if something should be here.", "top")
            continue
        merged = {}  # several questions about the same spot and of the same kind become one card
        for k, c, q, d, a in items_for(r):
            m = merged.setdefault((a, k), [k, c, q, [], a])
            if d not in m[3]:
                m[3].append(d)
            if c == "r" or (c == "w" and m[1] == ""):
                m[1] = c
        for k, c, q, ds, a in merged.values():
            card(r, k, c, q, ds[0] if len(ds) == 1 else "<ul class=many>" + "".join(f"<li>{x}</li>" for x in ds) + "</ul>", a)
        for i, s in enumerate(r["sentences"]):
            rc_ = s.get("rechecked")
            if not rc_:
                continue
            ev = "".join(f"<li><b>{'slide' if q['id'] == 'slide' else 'transcript ' + esc(q['id'])}:</b> &ldquo;{prose(q['quote'])}&rdquo;</li>" for q in rc_["quotes"])
            why = "; ".join(human(f) for f in rc_["flags"])
            d = (f"Flagged because: {why} TRACER found the sentence is supported by the sources below." if rc_["verdict"] == "supported" else
                 f"Flagged because: {why} TRACER rewrote it to say only what the sources state. Before: <i>{prose(rc_['before'])}</i>")
            card(r, "settled by TRACER", "", prose(s["text"]), d, f"x{r['n']}-{i}",
                 f"<details class=ev><summary>Evidence ({len(rc_['quotes'])}) and check</summary><ul>{ev}</ul>"
                 f"<p class=mut>Second-model check: {esc(rc_['check'])}</p></details>", group="settled")
        for s in r.get("settled", []):
            ev = "".join(f"<li><b>{esc(e['kind'])}{(' ' + esc(e['ref'])) if e['ref'] else ''}:</b> {prose(e['quote'])}</li>" for e in s["evidence"])
            act = {"replace": f" The equation was corrected to {prose('$' + s['latex'] + '$')}.", "drop": " It was removed: not a separate equation on the slide.",
                   "add": f" Added: {prose('$' + s['latex'] + '$')}.", "keep": " The equation stays as read."}.get(s["action"], "")
            a = (f"f{r['n']}-{r['figs'].index(s['fig']) + 1}" if s["kind"] == "fig" and s["fig"] in r["figs"] else
                 eq_anchor(r, s["latex"] if s["action"] in ("replace", "add") else s["orig"]) if s["kind"] == "eq" else f"s{r['n']}")
            card(r, "settled by TRACER", "", prose(s["question"]), prose(s["answer"]) + act,
                 a, f"<details class=ev><summary>Evidence ({len(s['evidence'])}) and check</summary><ul>{ev}</ul>"
                    f"<p class=mut>Second-model check: {esc(s['check'])}</p></details>", group="settled")
        for c in r["students"]:
            card(r, "student remark", "", esc(c["text"]), "A student's words, kept out of the textbook. Check that nothing the professor "
                 "said in reply depends on it.", f"s{r['n']}")
    figs_all = [(r, k, f) for r in shown for k, f in enumerate(r["figs"], 1)
                if (r["fig_meta"].get(f) or {}).get("function") not in (None, "decorative")]
    if figs_all:  # TRACER cannot know where a figure came from: ask once, and keep a sources list in the audit folder
        r0, k0, _ = figs_all[0]
        card(r0, "figure sources", "", "", f"This chapter shows {len(figs_all)} figure(s) taken from the slides. If any is copied from a "
             "published book, paper or website (for example a photo of a textbook page), check that it may be used, and add its "
             "source. A list to fill in is in '3 Audit and logs/figure_sources.json'.", f"f{r0['n']}-{k0}")
        fsf = site / "figure_sources.json"
        old = {}
        if fsf.exists():  # keep what a person already filled in
            with open(fsf, encoding="utf8") as fh:
                old = {x["figure"]: x for x in json.load(fh)}
        with open(fsf, "w", encoding="utf8") as fh:
            json.dump([dict(section=secno[r["n"]], figure=f, alt=(r["fig_meta"][f] or {}).get("alt", ""),
                            source=old.get(f, {}).get("source", ""), permission=old.get(f, {}).get("permission", ""))
                       for r, k, f in figs_all], fh, indent=1)
    rsf = pathlib.Path("work/gaps/resolved.json")
    if rsf.exists():
        with open(rsf, encoding="utf8") as fh:
            na = [g for g in json.load(fh).values() if g.get("status") == "not attempted"]
        if na:
            card(None, "not attempted", "w", "", f"{len(na)} unclear passage(s) in the recording were not sent for automatic "
                 "resolution because that step's spending limit was reached (the most important ones went first). They stay "
                 "marked as uncertain: sentences built on them carry their own cards. Raise the limit and run again to settle more.", "top")
    if any(TEACH.values()):
        lo = lambda lst: [x for x in lst if not x.get("supported")]
        def left_out(lst, show):
            bad = lo(lst)
            return ("<details class=ev><summary>" + f"{len(bad)} item(s) left out by the audit</summary><ul>" +
                    "".join(f"<li>{show(x)}<br><span class=mut>{esc(x.get('issue', ''))}</span></li>" for x in bad) + "</ul></details>") if bad else ""
        first_kt = next((x["section"] for x in TEACH["takeaways"] if x.get("supported") and x["section"] in secno), None)
        if first_kt:
            card(None, "teaching", "", "", "Key takeaways after each section, written from the chapter and checked against the sentences they "
                 "cite. Check that each one says what the section teaches.", f"t-kt{first_kt}",
                 left_out(TEACH["takeaways"], lambda x: " · ".join(prose(p) for p in x["points"])))
        if any(q.get("supported") for q in TEACH["questions"]):
            card(None, "teaching", "", "", "Study questions with model answers. Check each answer, and that the questions fit your course.",
                 "t-studyq", left_out(TEACH["questions"], lambda x: prose(x["question"])))
        if any(g.get("supported") for g in TEACH["glossary"]):
            card(None, "teaching", "", "", "Glossary definitions, taken from the chapter. Check each one.", "t-gloss",
                 left_out(TEACH["glossary"], lambda x: f"<b>{prose(x['term'])}</b>: {prose(x['definition'])}"))
    t = STRUCT["lecturette_title"] or args.title
    rc = pathlib.Path(__file__).parent / "review_center"
    cfg = dict(id=pathlib.Path.cwd().name, title=re.sub(r"\$", "", t), prior=REVIEW.get("decisions", {}),
               prior_comments=REVIEW.get("comments", []), designer_email=REVIEW.get("designer_email", ""),
               stage="professor" if REVIEW.get("decisions") else "designer")
    # reviewer comments travel in this JSON: escape <, > and & so a comment containing </script> cannot end the script tag
    rc_json = json.dumps(cfg).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Review center: {esc(cfg['title'])}</title><style>{CSS}{(rc / 'rc.css').read_text(encoding='utf8')}</style>{MJ}</head>"
            f"<body class=rc>{(rc / 'rc_shell.html').read_text(encoding='utf8').replace('%%TITLE%%', prose(t)).replace('%%DOC%%', left).replace('%%CARDS%%', ''.join(cards))}"
            f"<script>const RC={rc_json};</script><script>{(rc / 'rc.js').read_text(encoding='utf8')}</script></body></html>")


(site / "review_textbook.html").write_text(render_review(), encoding="utf8")
(site / "review_center.html").write_text(render_center(), encoding="utf8")
(site / "final_textbook.html").write_text(render_final(), encoding="utf8")
if not args.no_teaching:
    (site / "final_textbook_teaching.html").write_text(render_final(teaching=True), encoding="utf8")
print(f"MathML conversion fallbacks (kept as MathJax LaTeX): {FALLBACKS['n']}")
print(f"\nTOTAL SPEND THIS RUN: ${llm_util.SPEND['usd']:.4f} over {llm_util.SPEND['calls']} calls (cached calls are free)")
print("wrote work/site/review_textbook.html, final_textbook.html, textbook.json, structure.json")

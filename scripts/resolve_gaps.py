"""LLM resolution of unresolved transcript gaps, with evidence rules and a separate verifier.

  python resolve_gaps.py --dry-run     print the calls it would make; sends nothing
  python resolve_gaps.py               run (needs OLC_SCRIPT_API_KEY in .env)

Reads  work/gaps/gaps.json, work/site/sections.json, work/site/gap_status.json  (written by build_page.py)
Writes work/gaps/resolved.json ; caches every API result in work/llm_cache/ (re-runs are free).

Rules the resolver must follow:
  * choose one engine's reading as-is, OR correct it using a quoted slide token, OR return unintelligible
  * never write new sentences; any correction must quote its evidence
Evidence quotes are checked mechanically (must literally appear in the slide OCR / readings), then a
second model call, which sees only the gap + candidate + evidence, must confirm. Failures become unintelligible.
"""
import argparse, hashlib, json, os, pathlib, re, sys

ap = argparse.ArgumentParser()
ap.add_argument("--dry-run", action="store_true")
import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).parent))
import llm_util
from llm_util import get_key, MODELS  # models and prices: config/models.json

ap.add_argument("--resolver", default=MODELS["resolver"])
ap.add_argument("--verifier", default=MODELS["verifier"])
ap.add_argument("--max-usd", type=float, default=0.60,
                help="most this step may spend; gaps are handled most important first, and any left over are listed as not attempted")
ap.add_argument("--max-calls", type=int, default=None, help=argparse.SUPPRESS)  # older name; ignored
args = ap.parse_args()

G = json.load(open("work/gaps/gaps.json"))
SECTIONS = json.load(open("work/site/sections.json"))
STATUS = {int(k): v for k, v in json.load(open("work/site/gap_status.json")).items()}
TOK, TIMES = G["tokens"], G["times"]
cache_dir = pathlib.Path("work/llm_cache"); cache_dir.mkdir(exist_ok=True)

RESOLVER_SYSTEM = """You help reconstruct a university lecture transcript from three imperfect speech-to-text engines.
You are shown one GAP where the engines disagree, the text just before and after it, and the OCR text of the slides.
Decide what the professor most likely said, using ONLY evidence you can quote.

Rules:
1. Pick exactly one: "whisper", "qwen", "parakeet" (use that engine's reading unchanged), "corrected" (a minimal fix to one
   reading, justified by a quoted slide term/number), or "unintelligible" (no reading is supportable).
2. Never write new sentences or add content that no engine produced. A correction may only swap a mis-heard word or number for
   the slide's term/number (for example a number that appears on the slide).
3. For "corrected", you MUST give at least one evidence item with source "slide" and an exact quote copied from the slide OCR.
4. Engines hallucinate: non-Latin characters, looped words, and fluent sentences unrelated to the surrounding lecture are
   suspect. Prefer "unintelligible" over a smooth guess.
5. Keep the speaker's words; do not polish or summarize."""

VERIFIER_SYSTEM = """You audit a proposed fix to a lecture transcript. You see the gap's original readings, the proposed text,
the quoted evidence, and the slide OCR. Answer whether the proposed text is supported. Mark supported=false if the proposal adds
content not present in any reading, the evidence does not actually support it, or it does not fit the surrounding text."""

RESOLVE_TOOL = {"name": "decide", "description": "Record the decision for this gap.", "strict": True, "input_schema": {
    "type": "object", "required": ["decision", "text", "evidence", "reason"], "additionalProperties": False,
    "properties": {
        "decision": {"enum": ["whisper", "qwen", "parakeet", "corrected", "unintelligible"]},
        "text": {"type": "string", "description": "Final text for the gap ('' if unintelligible)."},
        "evidence": {"type": "array", "items": {"type": "object", "required": ["source", "quote"], "additionalProperties": False, "properties": {
            "source": {"enum": ["slide", "reading", "context"]}, "quote": {"type": "string"}}}},
        "reason": {"type": "string"}}}}
VERIFY_TOOL = {"name": "verdict", "description": "Record the audit verdict.", "strict": True, "input_schema": {
    "type": "object", "required": ["supported", "reason"], "additionalProperties": False,
    "properties": {"supported": {"type": "boolean"}, "reason": {"type": "string"}}}}


def norm(s): return re.sub(r"\s+", " ", s.lower()).strip()


def plausible(reading):
    """False for readings that look hallucinated: non-Latin script or looped words."""
    w = reading.lower().split()
    if not w or re.search(r"[^\x00-ɏ‘-”–—°-¾Ͱ-Ͽ]", reading):
        return False
    return len(w) < 6 or len(set(w)) / len(w) >= 0.5


def slides_text(t):
    """Only the slide on screen at time t plus the one before it (sending every slide wasted most of the input tokens)."""
    cur = max((i for i, s in enumerate(SECTIONS) if s["t0"] <= t), default=0)
    parts = []
    for i in range(max(0, cur - 1), cur + 1):
        s = SECTIONS[i]
        mark = " (ON SCREEN AT THIS TIME)" if i == cur else " (previous slide)"
        parts.append(f"--- slide {s['n']}, {s['t0']:.0f}-{s['t1']:.0f}s{mark} ---\n{s['md']}")
    return "\n".join(parts)


def gap_prompt(g):
    lo, hi = g["lo"], g["hi"]
    return (f"Time ~{g['t']}s.\nTEXT BEFORE: {' '.join(TOK[max(0, lo - 30):lo])}\n"
            f"GAP READINGS:\n  whisper: {g['whisper'] or '(nothing)'}\n  qwen: {g['qwen'] or '(nothing)'}\n  parakeet: {g['parakeet'] or '(nothing)'}\n"
            f"TEXT AFTER: {' '.join(TOK[hi:hi + 30])}\n\nSLIDE OCR:\n{slides_text(g['t'])}")


def call(model, system, tool, user, label):
    """through llm_util (spending limit, one log format); the cache file name of earlier runs is kept, so they stay free"""
    key = hashlib.sha256((model + system + user).encode()).hexdigest()[:24]
    f = cache_dir / f"{key}.json"
    if args.dry_run and not f.exists():
        return None, True
    out, fresh = llm_util.call(model, system, tool, user, max_tokens=2000, label=label, cache_file=f)
    if out is None:  # no answer: treat as no decision (the spend is logged by llm_util; nothing cached)
        return ({"decision": "unintelligible", "text": "", "evidence": [], "reason": "model returned no answer"}
                if "decision" in json.dumps(tool) else {"supported": False, "reason": "model returned no answer"}), fresh
    return out, fresh


SPKF = pathlib.Path("work/speakers/speakers.json")
STUDENT = [(r["start"], r["end"]) for r in json.load(open(SPKF))["ranges"] if r["label"] == "student"] if SPKF.exists() else []
in_student = lambda t: any(a <= t < b for a, b in STUDENT)  # do not spend money reconstructing student audio
todo = [g for g in G["gaps"] if STATUS.get(g["id"], {}).get("status") in ("unresolved", "dropped") and not in_student(g["t"])]


def priority(g):
    """most important first: gaps whose readings carry a number or a slide term, then the longest (most content at stake)"""
    rd = " ".join([g["whisper"], g["qwen"], g["parakeet"]])
    has_num = bool(re.search(r"\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|point)\b", rd, re.I))
    has_slide = any(g.get("slide_hits", {}).get(k) for k in ("whisper", "qwen", "parakeet"))
    return (not has_num, not has_slide, -max(len(r.split()) for r in (g["whisper"], g["qwen"], g["parakeet"])), g["t"])


todo.sort(key=priority)
print(f"{len(todo)} gaps to resolve (status unresolved/dropped), most important first; resolver={args.resolver}, "
      f"verifier={args.verifier}, limit ${args.max_usd:.2f}")
if not args.dry_run:
    get_key()  # fails early with a clear message if .env has no script key

calls, results, start = 0, {}, llm_util.SPEND["usd"]
for g in todo:
    prompt = gap_prompt(g)
    if args.dry_run:
        print(f"\n=== gap {g['id']} t={g['t']}s  W:{g['whisper'][:50]!r} Q:{g['qwen'][:50]!r} P:{g['parakeet'][:40]!r}  (~{len(prompt)//4} prompt tokens)")
        continue
    if llm_util.SPEND["usd"] - start >= args.max_usd:
        results[g["id"]] = dict(status="not attempted", decision="", text="", evidence=[],
                                reason=f"this step's spending limit (${args.max_usd:.2f}) was reached before this gap")
        continue
    d, fresh = call(args.resolver, RESOLVER_SYSTEM, RESOLVE_TOOL, prompt, f"gap resolution {g['id']}"); calls += fresh
    status, why = "accepted", d["reason"]
    # --- mechanical evidence checks (free)
    slide_blob = norm(slides_text(g["t"]))
    readings = norm(" ".join([g["whisper"], g["qwen"], g["parakeet"]]))
    if d["decision"] == "corrected" and not any(e["source"] == "slide" and norm(e["quote"]) in slide_blob for e in d["evidence"]):
        status, why = "rejected", "correction without a quote that literally appears on the slide"
    elif d["decision"] in ("whisper", "qwen", "parakeet") and norm(d["text"]) != norm(g[d["decision"]]):
        status, why = "rejected", "text does not match the chosen engine's reading"
    elif d["decision"] != "unintelligible":
        # dropped-content guard: a short pick over a long, plausible reading silently loses lecture content
        # (audit 2026-11-11: 13 accepted picks kept <=40% of a reading of 8+ words, ~254 words lost). Reject, so the
        # page keeps the first-pass reading flagged for review instead.
        ref = max((len(r.split()) for r in (g["whisper"], g["qwen"], g["parakeet"]) if plausible(r)), default=0)
        if ref >= 8 and len(d["text"].split()) <= 0.4 * ref:
            status, why = "rejected", f"drops content: keeps {len(d['text'].split())} of {ref} words; first-pass reading kept and flagged"
    # --- independent verifier (only for non-trivial decisions)
    if status == "accepted" and d["decision"] != "unintelligible":
        vprompt = (f"Original readings:\n  whisper: {g['whisper']}\n  qwen: {g['qwen']}\n  parakeet: {g['parakeet']}\n"
                   f"Text before: {' '.join(TOK[max(0, g['lo'] - 30):g['lo']])}\nText after: {' '.join(TOK[g['hi']:g['hi'] + 30])}\n"
                   f"PROPOSED: {d['text']}\nEVIDENCE: {json.dumps(d['evidence'])}\n\nSlide OCR:\n{slides_text(g['t'])}")
        v, fresh = call(args.verifier, VERIFIER_SYSTEM, VERIFY_TOOL, vprompt, f"gap check {g['id']}"); calls += fresh
        if not v["supported"]:
            status, why = "rejected", "verifier: " + v["reason"]
    final = d["text"] if status == "accepted" and d["decision"] != "unintelligible" else ""
    results[g["id"]] = dict(status=status if final or status == "rejected" else "unintelligible", decision=d["decision"],
                            text=final, evidence=d["evidence"], reason=why)
    print(f"gap {g['id']:2} {d['decision']:14} -> {results[g['id']]['status']:14} {final[:60]!r}  | {why[:90]}")

if not args.dry_run:
    with open("work/gaps/resolved.json", "w", encoding="utf8") as fh:
        json.dump(results, fh, indent=1)
    left = sum(r["status"] == "not attempted" for r in results.values())
    print(f"\n{calls} new API calls, ${llm_util.SPEND['usd'] - start:.2f}. " +
          (f"{left} gap(s) not attempted (spending limit); they stay flagged for review. " if left else "") + "wrote work/gaps/resolved.json")

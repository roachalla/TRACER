"""Transcript-only output, for videos the full pipeline cannot handle well (type 1, classroom recordings). Run from inside a run
folder, after asr, gaps and speakers. No images, no OCR, no MathML; spoken math stays as words.
  transcript_only.py --prep      writes work/site/sections.json + gap_status.json so resolve_gaps.py can run (no slide evidence)
  transcript_only.py [TITLE]     writes work/site/final_transcript.html (clean) and review_transcript.html (flags)
The rule-based gap resolution mirrors build_page.py (two of three engines agree, else Whisper, flagged)."""
import html, json, pathlib, re, sys

site = pathlib.Path("work/site"); site.mkdir(parents=True, exist_ok=True)
G = json.load(open("work/gaps/gaps.json", encoding="utf8"))
TOK, TIMES, GAPS = G["tokens"], G["times"], {g["lo"]: g for g in G["gaps"]}
SEG = json.load(open("work/asr/whisper_segments.json", encoding="utf8"))
DUR = SEG[-1]["end"] if SEG else (TIMES[-1] if TIMES else 0)
FILLERS = {"uh", "um", "mm", "hmm", "okay", "yeah", "ah", "oh", "mhm"}
kk = lambda r: [re.sub(r"[^\w.]", "", w.lower()).strip(".") for w in r.split() if re.sub(r"[^\w.]", "", w.lower()) not in FILLERS]
bad = lambda r: bool(re.search(r"[^\x00-\x7f]", r)) or any(a == b == c for a, b, c in zip(kk(r), kk(r)[1:], kk(r)[2:]))


def rule(g):
    r = {"whisper": g["whisper"], "qwen": g["qwen"], "parakeet": g["parakeet"]}
    ok = {k: v for k, v in r.items() if v and not bad(v)}
    alts = " | ".join(f"{k[0].upper()}: {v or '-'}" for k, v in r.items())
    if not ok:
        return "", "dropped", alts
    ks = {k: kk(v) for k, v in ok.items()}
    for a, b in (("whisper", "parakeet"), ("qwen", "parakeet"), ("whisper", "qwen")):
        if a in ks and b in ks and ks[a] == ks[b]:
            return ok[a], "majority", alts
    pick = "whisper" if "whisper" in ok else next(iter(ok))
    return ok[pick], "unresolved", alts


if "--prep" in sys.argv:
    json.dump([dict(n=1, t0=0, t1=DUR + 1, md="")], open(site / "sections.json", "w"), indent=1)
    st = {}
    for g in G["gaps"]:
        text, status, _ = rule(g)
        st[g["id"]] = dict(status=status, text=text)
    json.dump(st, open(site / "gap_status.json", "w"), indent=1)
    print(f"transcript-only prep: {len(G['gaps'])} gaps, {sum(v['status'] in ('unresolved', 'dropped') for v in st.values())} for the resolver")
    sys.exit(0)

title = next((a for a in sys.argv[1:] if not a.startswith("--")), "Lecture transcript")
LLM = {}
if pathlib.Path("work/gaps/resolved.json").exists():
    LLM = {int(k): v for k, v in json.load(open("work/gaps/resolved.json", encoding="utf8")).items()}
spf = pathlib.Path("work/speakers/speakers.json")
STUDENT = [(r["start"], r["end"]) for r in json.load(open(spf))["ranges"] if r["label"] == "student"] if spf.exists() else []
is_student = lambda t: any(a <= t < b for a, b in STUDENT)

items, i = [], 0  # (time, text, status, alternatives)
while i < len(TOK):
    if i in GAPS:
        g = GAPS[i]
        text, status, alts = rule(g)
        r = LLM.get(g["id"])
        if r and r["status"] == "accepted" and r["text"]:
            text, status = r["text"], "llm"
        elif r and r["status"] in ("unintelligible", "rejected") and not text.strip():
            text, status = "[inaudible]", "unintelligible"
        if text.strip():
            items.append((TIMES[min(i, len(TIMES) - 1)], text, status, alts))
        i = max(g["hi"], i + 1)
    else:
        if re.sub(r"[^\w]", "", TOK[i].lower()) not in FILLERS:
            items.append((TIMES[i], TOK[i], "attested", ""))
        i += 1

paras, cur = [], None  # paragraphs split at speaker changes and after ~70 words at a sentence end
for t, text, st, alts in items:
    spk = is_student(t)
    if cur is None or cur["student"] != spk or (cur["n"] >= 70 and re.search(r"[.?!]$", cur["parts"][-1][0])):
        cur = dict(t=t, student=spk, parts=[], n=0); paras.append(cur)
    cur["parts"].append((text, st, alts)); cur["n"] += len(text.split())

ts = lambda t: f"{int(t // 60)}:{int(t % 60):02d}"
CSS = """:root{--ink:#1d3a5c;--txt:#2f3b48;--mut:#5f6b78;--paper:#fff;--tint:#eef6f8;--line:#dbe7ec;--acc:#1f6e9b;--amber:#fff1c7;--red:#ffd9d4}
@media(prefers-color-scheme:dark){:root{--ink:#cfe3f2;--txt:#e3eaf0;--mut:#a3b2bf;--paper:#12191f;--tint:#18252d;--line:#2a3b46;--acc:#6fb7e0;--amber:#5a4712;--red:#6b2a22}}
body{margin:0;background:var(--paper);color:var(--txt);font:19px/1.75 Georgia,'Times New Roman',serif}
.skip{position:absolute;left:-999px}.skip:focus{left:8px;top:8px;background:#fff;color:#000;padding:8px}
header,main,footer{max-width:760px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
header{padding-block:36px 8px}h1{font:700 34px/1.2 system-ui,'Segoe UI',sans-serif;color:var(--ink);margin:0}
.eyebrow{font:600 13px system-ui,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);margin:0 0 8px}
.note{font:15px/1.5 system-ui,sans-serif;color:var(--mut);background:var(--tint);border-radius:8px;padding:12px 16px;margin:18px 0 8px}
p{margin:0 0 18px}.ts{font:600 13px system-ui,sans-serif;color:var(--mut);margin-right:8px;font-variant-numeric:tabular-nums}
.who{font:700 13px system-ui,sans-serif;letter-spacing:.05em;text-transform:uppercase;color:var(--acc);margin-right:6px}
p.student{border-left:3px solid var(--acc);padding-left:14px}
.unresolved{background:var(--amber)}.unintelligible{background:var(--red)}.llm{text-decoration:underline dotted var(--acc)}
footer{font:14px system-ui,sans-serif;color:var(--mut);border-top:1px solid var(--line);padding-block:18px 40px;margin-top:30px}"""


def page(review):
    body = []
    for p in paras:
        words = []
        for text, st, alts in p["parts"]:
            e = html.escape(text)
            if review and st in ("unresolved", "unintelligible", "llm"):
                e = f"<span class='{st}' title=\"{html.escape(alts, quote=True)}\">{e}</span>"
            words.append(e)
        who = "<span class=who>Student</span>" if p["student"] else ""
        body.append(f"<p{' class=student' if p['student'] else ''}><span class=ts>{ts(p['t'])}</span>{who}{' '.join(words)}</p>")
    n_flag = sum(st in ("unresolved", "unintelligible") for p in paras for _, st, _ in p["parts"])
    note = ("<p class=note>This video was processed as a transcript only: slides, images and equations are not included, and "
            "spoken math is written as words. Time stamps link each paragraph to the recording.</p>")
    if review:
        note += (f"<p class=note><b>Review version.</b> <span class=unresolved>Amber</span> = the speech engines disagreed and no fix "
                 f"could be supported ({n_flag} passages); <span class=unintelligible>red</span> = inaudible; dotted underline = "
                 "settled by the resolver. Hover a highlight for what each engine heard.</p>")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}{' (review)' if review else ''}</title><style>{CSS}</style></head><body>"
            f"<a class=skip href='#main'>Skip to transcript</a><header><p class=eyebrow>{'Review · ' if review else ''}Lecture transcript</p>"
            f"<h1>{html.escape(title)}</h1>{note}</header><main id=main>{''.join(body)}</main>"
            f"<footer>{html.escape(title)} · transcript</footer></body></html>")


# ---- captions (WebVTT): <=2 lines of <=42 characters, 1-6 s per cue, broken at sentence ends where possible
words = []  # (time, word, status, student)
for k, (t, text, st, alts) in enumerate(items):
    ws = text.split()
    t_next = items[k + 1][0] if k + 1 < len(items) else t + 0.4 * len(ws)
    step = max(min((t_next - t) / max(len(ws), 1), 0.6), 0.05)
    words += [(t + j * step, w, st, is_student(t)) for j, w in enumerate(ws)]
cues, cur = [], None
for t, w, st, stu in words:
    start_new = cur is None or stu != cur["student"] or len(cur["text"]) + len(w) + 1 > 84 or t - cur["start"] > 6.0 \
        or (t - cur["start"] >= 1.2 and re.search(r"[.?!]$", cur["text"]))
    if start_new:
        cur = dict(start=t, end=t, text=("Student: " if stu and (not cues or not cues[-1]["student"]) else "") + w,
                   student=stu, unsure=st in ("unresolved", "unintelligible"))
        cues.append(cur)
    else:
        cur["text"] += " " + w
        cur["unsure"] |= st in ("unresolved", "unintelligible")
    cur["end"] = t + 0.5
for a, b in zip(cues, cues[1:]):
    a["end"] = min(max(a["end"], a["start"] + 1.0), b["start"], a["start"] + 6.0)
if cues:
    cues[-1]["end"] = max(cues[-1]["end"], cues[-1]["start"] + 1.0)


def two_lines(s):
    if len(s) <= 42:
        return s
    mid = len(s) // 2
    cut = min((i for i, ch in enumerate(s) if ch == " "), key=lambda i: abs(i - mid), default=mid)
    return s[:cut] + "\n" + s[cut + 1:]


vt = lambda x: f"{int(x // 3600):02d}:{int(x % 3600 // 60):02d}:{x % 60:06.3f}"
(site / "captions.vtt").write_text("WEBVTT\n\n" + "\n".join(f"{i}\n{vt(c['start'])} --> {vt(c['end'])}\n{two_lines(c['text'])}\n"
                                                         for i, c in enumerate(cues, 1)), encoding="utf8")
(site / "transcript.txt").write_text(f"{title}\n\n" + "\n\n".join(
    f"[{ts(p['t'])}] " + ("Student: " if p["student"] else "") + " ".join(x[0] for x in p["parts"]) for p in paras) + "\n", encoding="utf8")
unsure = [c for c in cues if c["unsure"]]
json.dump([dict(time=ts(c["start"]), text=c["text"]) for c in unsure], open(site / "captions_to_check.json", "w"), indent=1)

(site / "final_transcript.html").write_text(page(False), encoding="utf8")
(site / "review_transcript.html").write_text(page(True).replace(
    "</header>", (f"<p class=note><b>Captions to check:</b> {len(unsure)} of {len(cues)} caption cues contain words the speech "
                  "engines disagreed on: " + "; ".join(f"{c['time'] if 'time' in c else ts(c['start'])}" for c in unsure[:40])
                  + ("…" if len(unsure) > 40 else "") + "</p></header>") if unsure else "</header>", 1), encoding="utf8")
if "--prep" not in sys.argv and not pathlib.Path("work/site/v2.json").exists():
    json.dump({"mode": "transcript_only", "title": title}, open(site / "mode.json", "w"))
print(f"wrote final_transcript.html, review_transcript.html, transcript.txt, captions.vtt: {len(paras)} paragraphs, "
      f"{sum(p['n'] for p in paras)} words, {len(cues)} caption cues ({len(unsure)} to check)")

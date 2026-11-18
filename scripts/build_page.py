"""Prototype: build one lecturette HTML page (slide image + Mathpix content + transcript per slide).
Run with the envs/whisper python (needs cv2). No LLM. Usage: build_page.py [title]"""
import glob, html, json, os, pathlib, re, shutil, sys
import cv2

args = [a for a in sys.argv[1:] if not a.startswith("--out=")]
title = args[0] if args else "Lecture"
OUT_NAME = next((a[6:] for a in sys.argv[1:] if a.startswith("--out=")), "review_draft.html")  # review_raw.html (stage 7) or review_draft.html (stage 9)
ROOT = pathlib.Path(".")
crop_dir, mpx_dir, asr_dir = ROOT / "work/frames_crop", ROOT / "work/mathpix", ROOT / "work/asr"
site = ROOT / "work/site"
(site / "img").mkdir(parents=True, exist_ok=True)
STEP = 20  # seconds between extracted frames

# ---- slide runs (consecutive frames changing <=2% belong to one slide state)
META_F = ROOT / "work/frames_meta.json"
META = json.load(open(META_F)) if META_F.exists() else {"mode": "slides"}
BOARD = META["mode"] == "board"
seg = json.load(open(asr_dir / "whisper_segments.json"))
duration = seg[-1]["end"]
if BOARD:  # windows and their best board frame come from extract_frames.py --mode board
    files = [str(ROOT / "work/frames_final" / f"c_{w['key']}.jpg") for w in META["windows"]]
    runs = [(i, i) for i in range(len(files))]
else:
    files = sorted(glob.glob(str(crop_dir / "*.jpg")))
    g = [cv2.resize(cv2.imread(f, 0), (720, 540)) for f in files]
    runs, start = [], 0
    for i in range(len(files)):
        if i == len(files) - 1 or (cv2.absdiff(g[i], g[i + 1]) > 40).mean() * 100 > 2.0:
            runs.append((start, i)); start = i + 1

# ---- transcript cleanup (rule-based, no LLM)
FILL = re.compile(r"\b(uh|um|erm|hmm|mm-hmm)\b[,.]?\s*", re.I)


def clean(t):
    if re.search(r"[^\x00-\x7f]", t):
        return ""  # hallucination signature (e.g. Korean/Cyrillic)
    t = FILL.sub("", t)
    t = re.sub(r"\b(\w+)(\s+\1){2,}\b", r"\1", t, flags=re.I)  # "so so so so" -> "so"
    return re.sub(r"\s+", " ", t).strip()


def esc(s):
    return html.escape(s)


def md_block(md):
    """Mathpix text -> HTML paragraphs; $...$ and $$...$$ left for MathJax."""
    parts, out = re.split(r"(\$\$.*?\$\$)", md, flags=re.S), []
    for p in parts:
        if p.startswith("$$"):
            out.append(f"<div class=eq>{esc(p)}</div>")
        else:
            out += [f"<p>{esc(l.strip())}</p>" for l in p.split("\n") if l.strip()]
    return "\n".join(out)



# ---- version 2 (archaeology) transcript: resolve gaps by rule, keep alternatives
G = json.load(open(ROOT / "work/gaps/gaps.json"))
TOK, TIMES, GAPS = G["tokens"], G["times"], {g["lo"]: g for g in G["gaps"]}
FILLERS = {"uh", "um", "mm", "hmm", "okay", "yeah", "ah", "oh", "mhm"}
kk = lambda r: [re.sub(r"[^\w.]", "", w.lower()).strip(".") for w in r.split()
                if re.sub(r"[^\w.]", "", w.lower()) not in FILLERS]
bad = lambda r: bool(re.search(r"[^\x00-\x7f]", r)) or any(a == b == c for a, b, c in zip(kk(r), kk(r)[1:], kk(r)[2:]))


def resolve(g):
    r = {"whisper": g["whisper"], "qwen": g["qwen"], "parakeet": g["parakeet"]}
    ok = {k: v for k, v in r.items() if v and not bad(v)}
    alts = " | ".join(f"{k[0].upper()}: {v or '-'}" for k, v in r.items())
    if not ok:
        return "", "dropped", alts
    ks = {k: kk(v) for k, v in ok.items()}
    for a, b in (("whisper", "parakeet"), ("qwen", "parakeet"), ("whisper", "qwen")):
        if a in ks and b in ks and ks[a] == ks[b]:
            return ok[a], "majority", alts
    TECH = {"cosh", "hyperbolic", "tanh", "density", "rho", "omega", "eta", "amplitude", "amplitudes", "dispersion", "attenuation"}
    strong = {k: len([h for h in g["slide_hits"].get(k, []) if re.search(r"\d", h) or h in TECH]) for k in ok}
    best = max(strong, key=strong.get)
    others = sorted(strong.values())[:-1]
    if strong[best] > 0 and (not others or strong[best] > others[-1]):
        return ok[best], "slide", alts
    pick = "whisper" if "whisper" in ok else next(iter(ok))
    return ok[pick], "unresolved", alts


LLMF = ROOT / "work/gaps/resolved.json"
LLM = {int(k): v for k, v in json.load(open(LLMF)).items()} if LLMF.exists() else {}
GAP_STATUS = {}
ITEMS, i = [], 0  # (time, html, status)
while i < len(TOK):
    if i in GAPS:
        g = GAPS[i]
        text, status, alts = resolve(g)
        GAP_STATUS[g["id"]] = dict(status=status, text=text)  # rule-based status (input to resolve_gaps.py)
        if g["id"] in LLM:
            r = LLM[g["id"]]
            if r["status"] == "accepted" and r["text"]:
                text, status = r["text"], "llm"
                alts += " || LLM: " + r["reason"]
            elif r["status"] in ("unintelligible", "rejected"):
                # keep the first-pass (rule-based) reading, flagged; only show [unintelligible] if there is none
                alts += " || LLM could not support a fix: " + r["reason"]
                if text.strip():
                    status = "unresolved"
                else:
                    text, status = "[unintelligible]", "unintelligible"
        if text.strip():
            ITEMS.append((TIMES[min(i, len(TIMES) - 1)],
                          f"<span class='g {status}' title=\"{html.escape(alts, quote=True)}\">{html.escape(text)}</span>", status))
        i = max(g["hi"], i + 1)
    else:
        w = TOK[i]
        if re.sub(r"[^\w]", "", w.lower()) not in FILLERS:
            ITEMS.append((TIMES[i], html.escape(w), "attested"))
        i += 1


SPKF = ROOT / "work/speakers/speakers.json"
STUDENT = [(r["start"], r["end"]) for r in json.load(open(SPKF))["ranges"] if r["label"] == "student"] if SPKF.exists() else []


def is_student(t):
    return any(a <= t < b for a, b in STUDENT)


def speech_html(t0, t1):
    out, n, cur = [], 0, None
    for t, h, st in ITEMS:
        if not (t0 <= t < t1):
            continue
        spk = is_student(t)
        if spk != cur:  # speaker change: close the paragraph, open a new one
            if cur is not None:
                out.append("</p>")
            out.append("<p class=student><span class=who>Student</span> " if spk else "<p>")
            cur, n = spk, 0
        out.append(h); n += 1
        if n >= 45 and re.search(r"[.?!]</span>$|[.?!]$", h):
            out.append("</p>" + ("<p class=student>" if spk else "<p>")); n = 0
    return "".join(x if x.startswith(("<p", "</p")) else x + " " for x in out) + ("</p>" if out else "")


sections = []
for n, (a, b) in enumerate(runs):
    key = pathlib.Path(files[b]).stem.replace("c_", "")
    mj = mpx_dir / f"c_{key}.json"
    if BOARD:
        t0 = META["windows"][n]["t0"] if n else 0
        t1 = META["windows"][n]["t1"] if n < len(runs) - 1 else duration + 1
    else:
        t0 = a * STEP if n else 0
        t1 = (b + 1) * STEP if n < len(runs) - 1 else duration + 1
    shutil.copy(files[b], site / "img" / f"slide_{key}.jpg")
    md, figs = "", []
    if mj.exists():
        d = json.load(open(mj))
        md = d.get("text", "")
        # slide-master furniture (logos, footers, page numbers) is not content
        pi = {w.lower() for l in d.get("line_data", []) if l.get("type") == "page_info"
              for w in re.findall(r"[A-Za-z0-9&]+", (l.get("text") or "").replace("\\&", "&"))}
        if pi:
            def furniture(x):
                ws = re.findall(r"[A-Za-z0-9&]+", x.replace("\\&", "&").lower())
                return bool(ws) and all(w in pi for w in ws)
            md = "\n".join(x for x in md.split("\n") if not furniture(x))
        im = cv2.imread(files[b])
        for k, l in enumerate(d.get("line_data", [])):
            if l.get("type") in ("diagram", "chart"):
                xs, ys = [p[0] for p in l["cnt"]], [p[1] for p in l["cnt"]]
                crop = im[max(min(ys), 0):max(ys), max(min(xs), 0):max(xs)]
                if BOARD and crop.size >= 0.4 * im.size:
                    continue  # on a chalkboard Mathpix often calls a whole board area a 'diagram'; the board is already text + equations
                name = f"figure_{key}_{k}.jpg"
                cv2.imwrite(str(site / "img" / name), crop); figs.append(name)
    sections.append(dict(n=n + 1, key=key, t0=t0, t1=t1, md=md, figs=figs, text=speech_html(t0, t1)))


if BOARD:  # the same sketch is photographed in several windows: keep only the latest (most complete) copy
    def sig(f):
        x = cv2.resize(cv2.imread(str(site / "img" / f), 0), (96, 96)).astype("float32")
        return (x - x.mean()) / (x.std() + 1e-6)
    for i, s in enumerate(sections):
        later = [(f, sig(f)) for t in sections[i + 1:i + 4] for f in t["figs"]]
        keep = []
        for f in s["figs"]:
            a = sig(f)
            if any(float((a * b).mean()) >= 0.6 for _, b in later):
                print(f"board: figure {f} repeats in a later window; dropped")
            else:
                keep.append(f)
        s["figs"] = keep

LAST = {"title": "", "q": ""}


def heading(s):
    lines = [l.strip() for l in s["md"].split("\n") if l.strip() and not l.startswith("$$")]
    first = re.sub(r"^#+\s*", "", lines[0]) if lines else ""
    m = re.match(r"^(\d+\.\d+)\)?\.?\s*(.*)$", first)
    if m:
        LAST["q"] = m.group(1)
        rest = re.sub(r"^(hint)\s*:?\s*", lambda x: "Hint: ", m.group(2), flags=re.I).strip()
        LAST["title"] = f"Review question {m.group(1)}" + (f" - {rest}" if rest else "")
        return LAST["title"]
    sol = re.match(r"^\(?(solution)\)?", first, re.I)
    if sol:
        LAST["title"] = f"Solution to review question {LAST['q']}" if LAST["q"] else "Solution"
        return LAST["title"]
    if first and len(first) <= 60 and not first.endswith((":", ",")):
        LAST["title"] = first
        return first
    return (LAST["title"] + " (continued)") if LAST["title"] else f"Slide {s['n']}"


body = []
for s in sections:
    s['heading'] = heading(s)
    figs = "".join(f"<img class=fig src='img/{f}' alt=''>" for f in s["figs"])
    body.append(f"""<section>
<h2>{esc(s['heading'])} <small>{int(s['t0']//60)}:{int(s['t0']%60):02d}</small></h2>
<div class=cols><div class=slide><img src='img/slide_{s['key']}.jpg' alt='Slide {s['n']}'></div>
<div class=txt><div class=ocr>{md_block(s['md'])}{figs}</div>
<div class=speech>{s['text']}</div></div></div></section>""")

(site / OUT_NAME).write_text(f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<script>window.MathJax={{tex:{{inlineMath:[['$','$']],displayMath:[['$$','$$']]}},chtml:{{scale:1.25}}}}</script>
<script async src="https://cdnjs.cloudflare.com/ajax/libs/mathjax/3.2.2/es5/tex-mml-chtml.min.js" crossorigin="anonymous" integrity="sha384-M5jmNxKC9EVnuqeMwRHvFuYUE8Hhp0TgBruj/GZRkYtiMrCRgH7yvv5KY+Owi7TW" referrerpolicy="no-referrer"></script>
<style>:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#ddd;--card:#f7f7f7}}
@media(prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--mut:#aaa;--line:#444;--card:#202020}}}}
body{{font:17px/1.65 Georgia,serif;margin:0 auto;max-width:1200px;padding:24px 16px;background:var(--bg);color:var(--fg)}}
h1{{font-family:system-ui;margin-bottom:4px}}h2{{font-family:system-ui;font-size:20px;border-top:1px solid var(--line);padding-top:18px;margin-top:34px}}
small{{color:var(--mut);font-weight:400;font-size:13px}}.cols{{display:grid;grid-template-columns:minmax(0,4fr) minmax(0,9fr);gap:24px}}
.slide img{{width:100%;border:1px solid var(--line);border-radius:6px;position:sticky;top:12px}}
.ocr{{background:var(--card);padding:6px 14px;border-radius:6px;font-size:17px}}.ocr p{{margin:6px 0}}
.g.majority{{background:#e8f3e0}}.g.slide{{background:#dbeaff}}.g.unresolved{{background:#ffe6c2}}.g.llm{{background:#e6dcff}}.g.unintelligible{{background:#ffc9c9}}.student{{border-left:3px solid #7aa874;padding-left:10px;color:var(--mut)}}.who{{font:600 11px system-ui;text-transform:uppercase;letter-spacing:.05em;margin-right:6px}}.legend{{font:13px system-ui;color:var(--mut)}}.legend span{{padding:0 6px;border-radius:3px;margin-right:6px;color:#111}}@media(prefers-color-scheme:dark){{.g.majority{{background:#2c4a24;color:#fff}}.g.slide{{background:#1d3b66;color:#fff}}.g.unresolved{{background:#6b4a10;color:#fff}}.g.llm{{background:#43307a;color:#fff}}.g.unintelligible{{background:#7a2020;color:#fff}}}}
.fig{{max-width:100%;display:block;margin:8px 0}}.eq{{margin:10px 0}}mjx-container[display=true]{{margin:.6em 0!important}}
@media(max-width:760px){{.cols{{grid-template-columns:1fr}}.slide img{{position:static}}}}</style>
<h1>{esc(title)}</h1>
<p class=legend>Transcript (review): <span style='background:#e8f3e0'>two of three engines agree</span><span style='background:#dbeaff'>chosen by slide content</span><span style='background:#e6dcff'>LLM-resolved with evidence</span><span style='background:#ffc9c9'>unintelligible</span><span style='background:#ffe6c2'>unresolved (hover for all readings)</span> Unshaded = Whisper and Qwen agree. Hallucinated spans and fillers removed.</p>
{''.join(body)}""", encoding="utf8")
print("sections:", [(s['n'], s['key'], f"{s['t0']}-{s['t1']:.0f}s", len(s['text']), s['figs']) for s in sections])
print("wrote", site / OUT_NAME)

json.dump(GAP_STATUS, open(site / "gap_status.json", "w"), indent=1)
json.dump([dict(n=x["n"], t0=x["t0"], t1=x["t1"], md=x["md"]) for x in sections], open(site / "sections.json", "w"), indent=1)


# ---- export version 2 as numbered, status-tagged chunks (input to textbook.py)
RANK = ["attested", "majority", "slide", "llm", "unresolved", "unintelligible"]
V2 = []
for sec in sections:
    chunks, cur, toks_n = [], None, 0
    for t, h, st in ITEMS:
        if not (sec["t0"] <= t < sec["t1"]):
            continue
        txt = html.unescape(re.sub(r"<[^>]+>", "", h))
        spk = "student" if is_student(t) else "professor"
        flagged = st in ("unresolved", "unintelligible")
        if (cur is None or cur["speaker"] != spk or cur["flagged"] != flagged or toks_n >= 40
                or (toks_n >= 25 and re.search(r"[.?!]$", cur["text"]))):
            cur = dict(id=f"s{sec['n']}c{len(chunks) + 1}", t=round(t, 1), text="", status=st, speaker=spk, flagged=flagged)
            chunks.append(cur); toks_n = 0
        cur["text"] = (cur["text"] + " " + txt).strip(); toks_n += len(txt.split())
        if RANK.index(st) > RANK.index(cur["status"]):
            cur["status"] = st
    for c in chunks:
        c.pop("flagged")
    V2.append(dict(n=sec["n"], heading=sec["heading"], t0=sec["t0"], t1=sec["t1"], md=sec["md"],
                   figs=sec["figs"], key=sec["key"], chunks=chunks))
json.dump(V2, open(site / "v2.json", "w"), indent=1)
json.dump({"mode": META["mode"]}, open(site / "mode.json", "w"))

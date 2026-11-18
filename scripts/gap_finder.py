"""Gap finder (no LLM, no API): align Whisper vs Qwen (Parakeet = third vote), flag disagreements,
check each reading against slide OCR vocabulary, flag hallucination signatures.
Usage: gap_finder.py [asr_dir] [mathpix_dir] [out_dir]"""
import difflib, glob, html, json, pathlib, re, sys

asr = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "work/asr")
mpx = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "work/mathpix")
out = pathlib.Path(sys.argv[3] if len(sys.argv) > 3 else "work/gaps")
out.mkdir(parents=True, exist_ok=True)

NUM = {'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
       'six': '6', 'seven': '7', 'eight': '8', 'nine': '9'}
FILLER = {'uh', 'um', 'mm', 'hmm', 'okay', 'yeah', 'ah', 'oh', 'mhm'}
STOP = set("the a an of to in is are and for this that it we on at be as by or with from then so can".split())


def merge_point(s):  # "four point nine" -> "4.9"
    d = '|'.join(NUM)
    pat = rf'\b({d})((?:\s+(?:{d}))*)\s+point\s+((?:(?:{d})\s*)+)\b'

    def rep(m):
        a = ''.join(NUM[w.lower()] for w in re.findall(d, m.group(1) + m.group(2), flags=re.I))
        b = ''.join(NUM[w.lower()] for w in re.findall(d, m.group(3), flags=re.I))
        return a + '.' + b
    return re.sub(pat, rep, s, flags=re.I)


def key(tok):
    k = re.sub(r'[^\w.]', '', tok.lower()).strip('.')
    return NUM.get(k, k)


def merge_sub(s):  # "z one" -> "z1" (spoken subscripts)
    return re.sub(r'\b([zZ])\s+(one|two|1|2)\b', lambda m: m.group(1) + NUM.get(m.group(2).lower(), m.group(2)), s)


def toks(s):
    return merge_sub(merge_point(s)).split()


# ---------- sources
seg = json.load(open(asr / "whisper_segments.json"))
W, wtime = [], []  # whisper tokens + approximate time of each
for s in seg:
    t = toks(s["text"])
    for i, w in enumerate(t):
        W.append(w)
        wtime.append(s["start"] + (s["end"] - s["start"]) * (i / max(len(t), 1)))
Q = toks((asr / "qwen.txt").read_text(encoding="utf8"))
P = toks((asr / "parakeet.txt").read_text(encoding="utf8"))
kW, kQ, kP = ([key(x) for x in L] for L in (W, Q, P))

# ---------- OCR vocabulary (spoken forms for LaTeX symbols)
SPOKEN = {r'\rho': 'density rho', r'\omega': 'omega', r'\eta': 'eta surface elevation',
          r'\phi': 'phi potential', r'\cosh': 'cosh cosine hyperbolic',
          r'\tanh': 'tanh tangent hyperbolic', r'\partial': 'partial derivative', r'\pi': 'pi'}
vocab = set()
for f in glob.glob(str(mpx / "*.md")):
    txt = pathlib.Path(f).read_text(encoding="utf8")
    for cmd, say in SPOKEN.items():
        if cmd in txt:
            vocab |= set(say.split())
    plain = re.sub(r'\$\$.*?\$\$|\$.*?\$', ' ', txt, flags=re.S)
    vocab |= {key(w) for w in re.findall(r"[A-Za-z0-9.]+", plain)}
    vocab |= set(re.findall(r'\d+\.\d+', txt)) | {'z1', 'z2', 'h', 'k', 'g'}
vocab -= STOP
vocab.discard('')


def support(reading):
    ks = [key(w) for w in reading if key(w) and key(w) not in STOP and key(w) not in FILLER]
    return [k for k in ks if k in vocab], len(ks)


def nonlatin(r):
    return bool(re.search(r'[^\x00-\x7f]', ' '.join(r)))


def looped(r):
    k = [key(x) for x in r]
    return any(k[i] == k[i + 1] == k[i + 2] for i in range(len(k) - 2)) or (len(k) >= 4 and len(set(k)) == 1)


def fillerish(r):
    return bool(r) and all(key(x) in FILLER or not key(x) for x in r)


# ---------- alignment
def ops(a, b):
    return difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()


oQ, oP = ops(kW, kQ), ops(kW, kP)


def slice_of(o, b, lo, hi):  # tokens of b aligned to whisper range [lo, hi)
    res = []
    for tag, i1, i2, j1, j2 in o:
        if tag == 'equal':
            for off in range(i2 - i1):
                if lo <= i1 + off < hi:
                    res.append(b[j1 + off])
        elif (i1 < hi and i2 > lo) or (i1 == i2 and lo <= i1 <= hi):
            res += b[j1:j2]
    return res


# Whisper is compared against Qwen; if Qwen failed on this audio (far fewer words: it can give up on very muffled sound),
# compare against Parakeet instead, so one failed engine does not turn the rest of the lecture into one giant disagreement.
QWEN_OK = len(kQ) >= 0.5 * len(kW)
PAIR = oQ if QWEN_OK else oP
if not QWEN_OK:
    print(f"NOTE: Qwen returned {len(kQ)} words against Whisper's {len(kW)}; it likely failed on this audio. "
          f"Comparing Whisper with Parakeet instead (Qwen kept as the third reading).")
raw = [(i1, i2) for tag, i1, i2, j1, j2 in PAIR if tag != 'equal']
regions = []
for r in raw:  # merge conflicts separated by fewer than 3 agreed tokens
    if regions and r[0] - regions[-1][1] < 3:
        regions[-1] = (regions[-1][0], max(r[1], regions[-1][1]))
    else:
        regions.append(r)
# a very long disagreement is split into ~30-word pieces: each keeps its own time (sections, captions) and stays small enough
# for the resolver to judge (an audit found long merged gaps were where content got dropped)
MAXG = 30
split = []
for lo, hi in regions:
    while hi - lo > MAXG + 10:
        split.append((lo, lo + MAXG)); lo += MAXG
    split.append((lo, hi))
regions = split


def kk(r):
    return [key(x) for x in r if key(x) and key(x) not in FILLER]


gaps = []
for n, (lo, hi) in enumerate(regions):
    w, q, p = W[lo:hi], slice_of(oQ, Q, lo, hi), slice_of(oP, P, lo, hi)
    agree = ('whisper+parakeet' if kk(w) == kk(p) else 'qwen+parakeet' if kk(q) == kk(p) else
             'whisper+qwen' if kk(w) == kk(q) else 'none')
    sup = {name: support(r) for name, r in (('whisper', w), ('qwen', q), ('parakeet', p))}
    flags = [name for name, r in (('whisper', w), ('qwen', q), ('parakeet', p)) if nonlatin(r) or looped(r)]
    gtype = ('hallucination-suspect' if flags else
             'filler/noise' if all(fillerish(r) or not r for r in (w, q, p)) else
             'two-of-three' if agree != 'none' else 'unresolved')
    t0 = wtime[min(lo, len(wtime) - 1)]
    gaps.append(dict(id=n, t=round(t0, 1), lo=lo, hi=hi, type=gtype, agree=agree,
                     whisper=' '.join(w), qwen=' '.join(q), parakeet=' '.join(p), halluc_in=flags,
                     slide_hits={k: v[0] for k, v in sup.items()},
                     slide_frac={k: (round(len(v[0]) / v[1], 2) if v[1] else None) for k, v in sup.items()}))

attested = len(W) - sum(hi - lo for lo, hi in regions)
summary = dict(whisper_tokens=len(W), qwen_tokens=len(kQ), parakeet_tokens=len(kP), compared_with="qwen" if QWEN_OK else "parakeet",
               attested_whisper_qwen_agree=attested,
               pct_attested=round(100 * attested / len(W), 1), gaps=len(gaps),
               by_type={t: sum(g['type'] == t for g in gaps) for t in sorted({g['type'] for g in gaps})})
json.dump(dict(summary=summary, gaps=gaps, tokens=W, times=[round(x, 1) for x in wtime]), open(out / "gaps.json", "w"), indent=1)
print(json.dumps(summary, indent=1))

# ---------- HTML view
esc = html.escape
cls = {'hallucination-suspect': 'hal', 'two-of-three': 'two', 'unresolved': 'unr', 'filler/noise': 'fil'}
body, pos = [], 0
for g in gaps:
    body.append(esc(' '.join(W[pos:g['lo']])))
    tip = esc(f"#{g['id']} t={g['t']}s | W: {g['whisper']} | Q: {g['qwen']} | P: {g['parakeet']}", quote=True)
    body.append(f"<span class='gap {cls[g['type']]}' title=\"{tip}\"><sup>{g['id']}</sup>{esc(g['whisper']) or '∅'}</span>")
    pos = g['hi']
body.append(esc(' '.join(W[pos:])))
rows = ''.join(
    f"<tr class='{cls[g['type']]}'><td>{g['id']}</td><td>{g['t']}</td><td>{g['type']}<br><small>{g['agree']}</small></td>"
    f"<td>{esc(g['whisper']) or '∅'}</td><td>{esc(g['qwen']) or '∅'}</td><td>{esc(g['parakeet']) or '∅'}</td>"
    f"<td><small>{esc(', '.join(sorted({h for v in g['slide_hits'].values() for h in v})) or '-')}</small></td></tr>"
    for g in gaps)
(out / "gaps.html").write_text(f"""<!doctype html><meta charset=utf-8><title>Gap finder</title>
<style>body{{font:15px/1.6 system-ui;margin:20px;max-width:1300px;background:#fff;color:#111}}
.gap{{padding:0 2px;border-radius:3px}}.hal{{background:#ffb3b3}}.two{{background:#fff0a8}}.unr{{background:#ffcf99}}.fil{{background:#ddd}}
table{{border-collapse:collapse;font-size:13px}}td,th{{border:1px solid #bbb;padding:3px 8px;vertical-align:top}}sup{{font-size:9px;color:#666}}
@media(prefers-color-scheme:dark){{body{{background:#161616;color:#eee}}.hal{{background:#7a2020}}.two{{background:#6b5a00}}.unr{{background:#7a4a10}}.fil{{background:#444}}td,th{{border-color:#555}}}}</style>
<h1>Gap finder: Whisper vs Qwen (+Parakeet vote)</h1>
<p><b>{summary['pct_attested']}%</b> of Whisper tokens agree with Qwen. {summary['gaps']} gaps: {summary['by_type']}.<br>
Text below is Whisper's; shaded spans are gaps (hover for all three readings). Red = non-Latin/looping, yellow = two of three agree, orange = unresolved, grey = filler.</p>
<p>{' '.join(body)}</p><h2>Gaps</h2>
<table><tr><th>#</th><th>t (s)</th><th>type</th><th>Whisper</th><th>Qwen</th><th>Parakeet</th><th>slide-vocab hits</th></tr>{rows}</table>""",
                                 encoding="utf8")
print("wrote", out / "gaps.html")

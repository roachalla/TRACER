"""Build work/asr/compare.html: timing table + transcripts side by side, with word-level diff vs a chosen reference."""
import difflib, html, json, pathlib, re, sys
d = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "work/asr")
models = [m for m in ("parakeet", "whisper", "qwen") if (d / f"{m}.txt").exists()]
txt = {m: (d / f"{m}.txt").read_text(encoding="utf8") for m in models}
info = {m: json.load(open(d / f"{m}.json")) for m in models}
ref = models[0]
tok = lambda s: re.findall(r"\S+", s)
norm = lambda w: re.sub(r"[^\w]", "", w.lower())

def marked(m):
    a, b = tok(txt[ref]), tok(txt[m])
    sm = difflib.SequenceMatcher(None, [norm(w) for w in a], [norm(w) for w in b], autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        seg = html.escape(" ".join(b[j1:j2]))
        out.append(seg if tag == "equal" or m == ref else f"<mark>{seg}</mark>" if seg else "")
    return " ".join(x for x in out if x)

rows = "".join(
    f"<tr><td>{m}</td><td>{i['load_s']}</td><td>{i['transcribe_s']}</td><td>{i['realtime_factor']}x</td><td>{i['words']}</td></tr>"
    for m, i in info.items())
cols = "".join(f"<div class=col><h2>{m}</h2><p>{marked(m)}</p></div>" for m in models)
open(d / "compare.html", "w", encoding="utf8").write(f"""<!doctype html><meta charset=utf-8><title>ASR comparison</title>
<style>body{{font:15px/1.6 system-ui;margin:20px;background:#fff;color:#111}}table{{border-collapse:collapse;margin-bottom:20px}}
td,th{{border:1px solid #bbb;padding:4px 10px}}.grid{{display:grid;grid-template-columns:repeat({len(models)},1fr);gap:16px}}
.col{{border:1px solid #ccc;padding:0 12px;border-radius:6px}}mark{{background:#ffe08a}}
@media(prefers-color-scheme:dark){{body{{background:#161616;color:#eee}}td,th,.col{{border-color:#555}}mark{{background:#6b5a00;color:#fff}}}}</style>
<h1>ASR comparison</h1><p>Highlighted words differ from <b>{ref}</b> (reference is only for diffing, not ground truth).</p>
<table><tr><th>model</th><th>load s</th><th>transcribe s</th><th>real-time factor</th><th>words</th></tr>{rows}</table>
<div class=grid>{cols}</div>""")
print("wrote", d / "compare.html")

"""Word versions of the textbook, for reuse in other products. Run from inside a run folder after textbook.py. Free (pandoc).
  make_docx.py [--teaching]
Writes work/site/textbook.docx (and textbook_teaching.docx with --teaching): one document, equations as real, editable Word
equations (OMML, converted by pandoc from the same LaTeX the web page uses), numbered like the web page, figures with alt text,
captions and long descriptions in the text. Same rules as the student web edition: no review flags."""
import json, pathlib, re, shutil, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from checks import incomplete  # unfinished slide equations are not shown to students

site = pathlib.Path("work/site")
tb = json.load(open(site / "textbook.json", encoding="utf8"))
st = json.load(open(site / "structure.json", encoding="utf8"))
teach = json.load(open(site / "teaching.json", encoding="utf8")) if (site / "teaching.json").exists() else {}
mode = json.load(open(site / "mode.json"))["mode"] if (site / "mode.json").exists() else "slides"
PANDOC = shutil.which("pandoc")
_rv = site / "review_edits.json"
REVIEWED = json.load(open(_rv, encoding="utf8")).get("professor_reviewed", False) if _rv.exists() else False


def md_text(s):
    """chapter sentence -> markdown: keep $...$ math, escape markdown characters outside it"""
    parts = re.split(r"(\$[^$]+\$)", s)
    return "".join(p if i % 2 else re.sub(r"([*_`#\[\]<>|])", r"\\\1", p) for i, p in enumerate(parts))





def disp_eq(latex, num):
    latex = re.sub(r"\\tag\{[^}]*\}", "", latex).strip()
    return f"\n$$ {latex} \\qquad ({num}) $$\n"


def fig_md(f, fm, n):
    if not fm or fm.get("function") == "decorative":
        return ""
    img = site / "img" / (f"disp_{f}" if (site / "img" / f"disp_{f}").exists() else f)
    alt = (fm.get("alt") or "").replace('"', "'")
    out = [f'\n![Figure {n}. {md_text(fm.get("caption", ""))}]({img.as_posix()}){{fig-alt="{alt}"}}\n']
    ld = fm.get("long_description") or []
    if isinstance(ld, str):
        ld = [ld]
    if ld:
        out.append(f"\n*Description of Figure {n}:*\n\n" + "\n".join(f"- {md_text(x)}" for x in ld) + "\n")
    return "".join(out)


def build(teaching):
    take = {x["section"]: x for x in teach.get("takeaways", []) if x.get("supported")} if teaching else {}
    L = [f"---\ntitle: \"{(st.get('lecturette_title') or 'Lecture').replace(chr(34), '')}\"\nlang: en\n---\n"]
    if not REVIEWED:  # same as the web edition: a draft until the instructor's review is applied
        L.append("**Draft.** This document was generated from a lecture recording and has not yet been reviewed by the instructor. "
                 "It may contain errors.\n")
    objs = st.get("objectives") or []
    if objs:
        L.append("## Learning objectives\n\nBy the end of this lecturette, you will be able to:\n\n" + "\n".join(f"- {md_text(o)}" for o in objs) + "\n")
    fig_no = 0
    for no, r in enumerate([r for r in tb if r["sentences"]], 1):
        L.append(f"\n## {no} {md_text(r.get('title') or r['heading'])}\n")
        eqs, k, para = r["equations"], 0, []
        placed = set(r.get("placed_figs", []))
        for f in [f for f in r["figs"] if f not in placed]:  # figures the text does not point to: at the start of the section
            fig_no += 1; L.append(fig_md(f, r["fig_meta"].get(f), fig_no))
        for s in r["sentences"]:
            if s["new_paragraph"] and para:
                L.append("\n" + " ".join(para) + "\n"); para = []
            para.append(md_text(s["text"]))
            if s["equations"] or s.get("figures"):
                L.append("\n" + " ".join(para) + "\n"); para = []
            for e in s["equations"]:
                if incomplete(eqs[e - 1]):
                    continue
                k += 1; L.append(disp_eq(eqs[e - 1], f"{no}.{k}"))
            for j in s.get("figures") or []:
                if 0 < j <= len(r["figs"]):
                    fig_no += 1; L.append(fig_md(r["figs"][j - 1], r["fig_meta"].get(r["figs"][j - 1]), fig_no))
        if para:
            L.append("\n" + " ".join(para) + "\n")
        for e in r.get("unplaced_show", r.get("unplaced_equations", [])) if mode != "board" else []:
            if incomplete(eqs[e - 1]):
                continue
            k += 1; L.append(disp_eq(eqs[e - 1], f"{no}.{k}"))
        if r["n"] in take:
            L.append("\n**Key takeaways**\n\n" + "\n".join(f"- {md_text(p)}" for p in take[r["n"]]["points"]) + "\n")
    if teaching:
        qs = [q for q in teach.get("questions", []) if q.get("supported")]
        if qs:
            L.append("\n## Study questions\n\n" + "\n".join(f"{i}. {md_text(q['question'])}" for i, q in enumerate(qs, 1)) + "\n")
            L.append("\n## Answers\n\n" + "\n".join(f"{i}. {md_text(q['answer'])}" for i, q in enumerate(qs, 1)) + "\n")
        gl = sorted([g for g in teach.get("glossary", []) if g.get("supported")], key=lambda g: g["term"].lower())
        if gl:
            L.append("\n## Glossary\n\n" + "\n".join(f"{md_text(g['term'])}\n:   {md_text(g['definition'])}\n" for g in gl))
    return "\n".join(L)


if not PANDOC:
    sys.exit("pandoc not found: install it from https://pandoc.org to make the Word version")
for teaching in ([False, True] if "--teaching" in sys.argv else [False]):
    name = "textbook_teaching" if teaching else "textbook"
    src = site / f"{name}.md"
    src.write_text(build(teaching), encoding="utf8")
    r = subprocess.run([PANDOC, str(src), "-f", "markdown+tex_math_dollars+implicit_figures+link_attributes", "-o", str(site / f"{name}.docx")],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"pandoc failed for {name}: {r.stderr[-800:]}")
    if r.stderr.strip():
        print("pandoc notes:", r.stderr.strip()[:400])
    print(f"wrote work/site/{name}.docx")

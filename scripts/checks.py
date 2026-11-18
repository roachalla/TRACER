"""Pure text checks used by textbook.py and make_docx.py: numbers (written or spoken), slide OCR splitting,
equation comparison and unfinished-equation detection. No file or network access, so they can be tested on their own
(tests/test_checks.py)."""
import re


_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                                         "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}


def spoken_numbers(s):
    """'twenty five hundred' -> 2500, 'one hundred and ten' -> 110, 'point nine' -> 0.9 (speech-to-text writes numbers as words)"""
    out, cur, total, live, dec = [], 0, 0, False, None
    def flush():
        nonlocal cur, total, live, dec
        if live or dec:  # a bare "point" ("at that point") is not a number; "point nine" is 0.9
            out.append(f"{total + cur}.{dec}" if dec else str(total + cur))
        cur, total, live, dec = 0, 0, False, None
    for w in re.findall(r"[a-z]+|\S", s.lower()):
        if w in _UNITS and dec is not None:
            dec += str(_UNITS[w]); continue
        if w in _UNITS:
            cur += _UNITS[w]; live = True
        elif w in _TENS:
            cur += _TENS[w]; live = True
        elif w == "hundred":
            cur = max(cur, 1) * 100; live = True
        elif w == "thousand":
            total += max(cur, 1) * 1000; cur = 0; live = True
        elif w == "point":
            dec = ""
        elif w == "and" and live:
            continue
        else:
            flush()
    flush()
    return set(out)


def nums(s):
    return set(re.findall(r"\d+(?:\.\d+)?", s)) | spoken_numbers(s)


def split_md(md):
    """slide OCR -> (text lines, equation blocks). Equations are the $$...$$ blocks, numbered from 1."""
    eqs = [m.strip("$ \n") for m in re.findall(r"\$\$.*?\$\$", md, flags=re.S)]
    text = []
    for l in (l.strip() for l in re.sub(r"\$\$.*?\$\$", "\n", md, flags=re.S).split("\n")):
        if not l:
            continue
        m = re.fullmatch(r"\$([^$]+)\$\s*([A-Za-z][A-Za-z ,'-]*)?", l)  # a line that is one equation, optionally followed by its label
        if m and len(m.group(1)) > 12:
            eqs.append(m.group(1).strip() + (r"\qquad \text{" + m.group(2).strip() + "}" if m.group(2) else ""))
        else:
            text.append(l)
    return text, eqs


def toks(md):
    return set(re.findall(r"[A-Za-z]{2,}|\d+(?:\.\d+)?", md))


REL = re.compile(r"=|<|>|\\equiv|\\approx|\\propto|\\sim|\\geq|\\leq|\\neq|\\ge\b|\\le\b|\\rightarrow|\\to\b")


def eq_norm(e):
    e = re.sub(r"\\(equiv|approx|simeq)", "=", e)  # OCR reads the same board relation either way
    e = re.sub(r"\\(rightarrow|to)(?![a-zA-Z])", "→", e)  # \to and \rightarrow are the same arrow
    e = re.sub(r"\\(left|right|displaystyle|dfrac|tfrac|mathrm|text|operatorname|qquad|quad|,|;|!|:)", "", e).replace("\\frac", "")
    return re.sub(r"[\s{}~]", "", e)


def covered(a, b):
    """share of a's characters found in b in runs of 3+ characters ('P=I' is inside 'P=IV'; 'P=IV' is not inside 'P=I^2R')"""
    import difflib
    if not a:
        return 1.0
    return sum(m.size for m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks() if m.size >= 3) / len(a)


def incomplete(latex):
    """a slide equation left unfinished ('C_0 = L_0/T =', 'h =') or empty: never shown to students (listed for the designer)"""
    t = re.sub(r"\\(?:qquad|quad|,|;|!)|\\text\{\s*\}|[\s{}~]", "", latex or "")
    return not t or t.endswith(("=", "\\approx", "\\equiv", "\\sim"))

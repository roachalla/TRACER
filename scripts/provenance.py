"""Free second check on sentence provenance (idea from the AWS / UTS lecture pipeline): TF-IDF cosine between each written
sentence and every transcript chunk of the lecture. A sentence whose cited chunks match it poorly, while some other chunk
matches it clearly better, is flagged for review. No LLM, no cost."""
import math, re
from collections import Counter

STOP = set("""a an the and or but if then so of to in on at by for with from as is are was were be been being it its this that these
those we you i he she they them our your there here what which who how when where not no do does did can could will would shall
should may might must have has had just also very really okay ok now let lets us me my all any some one about into than too""".split())
WORDNUM = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
           "nine": "9", "ten": "10", "twenty": "20", "hundred": "100", "thousand": "1000"}


def words(text):
    text = re.sub(r"\\[A-Za-z]+", " ", text)          # LaTeX commands
    text = re.sub(r"[{}$^_\\]", " ", text.lower())
    out = []
    for w in re.findall(r"[a-z]+|\d+(?:\.\d+)?", text):
        w = WORDNUM.get(w, w)
        if w in STOP or (len(w) < 2 and not w.isdigit()):
            continue
        out.append(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


class Index:
    def __init__(self, chunks):
        """chunks: list of dicts with 'id' and 'text' (the whole lecture, professor speech)"""
        self.ids = [c["id"] for c in chunks]
        docs = [Counter(words(c["text"])) for c in chunks]
        df = Counter(w for d in docs for w in d)
        n = len(docs)
        self.idf = {w: math.log((1 + n) / (1 + k)) + 1 for w, k in df.items()}
        self.vecs = [self._vec(d) for d in docs]

    def _vec(self, counts):
        v = {w: (1 + math.log(c)) * self.idf.get(w, 1.0) for w, c in counts.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    def sims(self, text):
        q = self._vec(Counter(words(text)))
        return {i: sum(x * cv.get(w, 0.0) for w, x in q.items()) for i, cv in zip(self.ids, self.vecs)}


def check(index, sentence, cited, low=0.12, margin=0.15):
    """Returns None or a flag string. cited: list of chunk ids the sentence cites."""
    cited = [c for c in cited if c in index.ids]
    if not cited or len(words(sentence)) < 4:
        return None
    s = index.sims(sentence)
    got = max(s[c] for c in cited)
    best = max(s, key=s.get)
    if got < low and best not in cited and s[best] - got >= margin:
        return f"provenance: matches passage {best} ({s[best]:.2f}) better than the passages it cites ({got:.2f})"
    return None

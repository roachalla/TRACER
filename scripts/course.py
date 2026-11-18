"""Course settings (config/course.json, overridden by the git-ignored config/course.local.json) and how video file names map
to course, week and lecture. Recognized file names:
  W6_Z2_T1_Title           -> Week 6 > Lecture 2 > Lecturette 1
  ABCD123_09-15_T2_Title   -> course ABCD 123 > Session 09-15 > Lecturette 2
  anything else            -> no week or lecture"""
import json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = {}
for _f in ("course.json", "course.local.json"):
    _p = ROOT / "config" / _f
    if _p.exists():
        CFG.update({k: v for k, v in json.loads(_p.read_text(encoding="utf8")).items() if not k.startswith("_")})

SUBJECT = CFG.get("subject", "science and engineering")
SUBJECT_SHORT = CFG.get("subject_short", "science/engineering")
CODE = r"[A-Z]{2,5}\d{3,4}"  # a course code at the start of a file name, e.g. ABCD123


def slug(stem):
    for p in CFG.get("strip_prefix", []):
        stem = stem.replace(p, "")
    return stem


def title_from(slug_):
    return re.sub(rf"^(?:{CODE}_)?(?:W\d+_Z\d+_T\d+_|[\d-]+_T\d+_|T\d+_)", "", slug_).replace("_", " ")


def course_name(name):
    m = re.match(r"([A-Z]{2,5})(\d{3,4})_", name)
    return f"{m[1]} {m[2]}" if m else CFG.get("course", "Course")


def place(name):
    """file or folder name -> (top, sub, lecturette number, rest of the name), or None"""
    m = re.match(r"W(\d+)_Z(\d+)_T(\d+)_?(.*)", name)
    if m:
        return f"Week {int(m[1])}", f"Lecture {int(m[2])}", int(m[3]), m[4]
    m = re.match(rf"({CODE})_([\d-]+)_T(\d+)_?(.*)", name)
    if m:
        return course_name(name), f"Session {m[2]}", int(m[3]), m[4]
    return None

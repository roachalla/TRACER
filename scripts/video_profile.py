"""Stage 0: what kind of video is this? Run from inside a run folder.
  video_profile.py VIDEO [--type auto|1|2|3|4|5]

Types (each needs different processing):
  1 classroom recording: a room camera films a projected screen and/or board with the instructor in front
  2 board only: a chalk/whiteboard with no instructor in view (or only a writing hand)
  3 screen share only (Zoom etc.)
  4 screen share with a webcam picture-in-picture in a corner
  5 edited video or PowerPoint recording: full-frame slides, no overlay
One cheap vision call looks at 4 frames (about $0.001). --type N sets the type (the user chose), but the call still runs to
find a webcam picture, which is always hidden from the slide images (it can show students). Type 1 skips the call.
Writes work/profile.json: type, name, mode (slides | board | transcript_only), webcam (auto | none), reason."""
import json, pathlib, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

NAMES = {1: "classroom recording", 2: "board only", 3: "screen share", 4: "screen share with webcam corner",
         5: "edited video or PowerPoint recording"}
# type -> (pipeline mode, webcam handling). Type 1 is not supported for full processing: the instructor blocks the screen
# and the screen is small and tilted, so slides and figures come out unreliable. It is offered as transcript only.
SETTINGS = {1: ("transcript_only", "none"), 2: ("board", "none"), 3: ("slides", "none"), 4: ("slides", "top-right"), 5: ("slides", "none")}

SYSTEM = """You classify a lecture video from four frames sampled across it. Choose exactly one type:
1 = classroom recording: a camera in a room films a projection screen and/or a board; the room, screen edges or the instructor
    are visible.
2 = board only: a chalkboard or whiteboard fills the frame and no instructor is visible (a writing hand is fine).
3 = screen share: slides or a computer screen fill the frame, with meeting-software look (e.g. Zoom) and no webcam picture.
4 = screen share with a webcam: like 3, plus a small picture-in-picture of a person or room in a corner.
5 = edited video or slide recording: full-frame slides, no webcam overlay, no meeting-software look.
If 3 and 5 are hard to tell apart, either is fine (they are processed the same way). Give the corner of any webcam overlay."""
TOOL = {"name": "classify", "description": "Video type.", "strict": True, "input_schema": {
    "type": "object", "required": ["type", "webcam_corner", "confidence", "reason"], "additionalProperties": False,
    "properties": {"type": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                   "webcam_corner": {"type": "string", "enum": ["none", "top-right", "top-left", "bottom-right", "bottom-left"]},
                   "confidence": {"type": "string", "enum": ["high", "low"]}, "reason": {"type": "string"}}}}


def frames(video, n=4):
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                               capture_output=True, text=True).stdout.strip() or 0)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="olc_profile_"))
    out = []
    for i, q in enumerate((0.15, 0.4, 0.65, 0.9)[:n]):
        f = tmp / f"p{i}.jpg"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{dur * q:.1f}", "-i", video, "-frames:v", "1",
                        "-vf", "scale=768:-2", "-q:v", "4", str(f)], check=True)
        out.append(f)
    return out


def main():
    video = sys.argv[1]
    choice = sys.argv[sys.argv.index("--type") + 1] if "--type" in sys.argv else "auto"
    pathlib.Path("work").mkdir(exist_ok=True)
    out = None
    # the detection runs even when the user chose a type (about $0.001): a webcam picture can show students, so a picture-in-picture
    # must be hidden from the slide images whatever type was picked (an audit found a room camera in the images of a "type 3" run)
    if choice != "1":
        import llm_util
        try:
            out, _ = llm_util.call(llm_util.MODELS.get("profiler", llm_util.MODELS["judge"]), SYSTEM, TOOL,
                                   "Classify this lecture video from the four frames.", effort="low", max_tokens=600,
                                   label="video profile", images=frames(video))
        except SystemExit:  # no API key (free-only run) or spending limit: fall back to the defaults below
            out = None
    if choice != "auto":
        t, how, reason, conf = int(choice), "chosen by the user", "", "high"
    elif not out:
        t, how, reason, conf = 4, "default (could not detect)", "no API key or no answer; assumed screen share with webcam", "low"
    else:
        t, how, reason, conf = out["type"], "detected", out["reason"], out["confidence"]
    mode, webcam = SETTINGS[t]
    seen = out["webcam_corner"] if out else "none"
    if t == 4:
        webcam = seen if seen != "none" else "top-right"  # which corner to white out
    elif seen != "none" and t in (2, 3, 5):
        webcam = seen
        reason = (reason + " " if reason else "") + (f"A webcam picture was detected in the {seen} corner although type {t} was chosen: "
                                                     "it is hidden from the slide images (this looks like type 4).")
        print(f"WARNING: a webcam picture was found in the {seen} corner; it will be hidden from the slide images. "
              f"If this video is a screen share with a webcam, choose type 4 next time.")
    prof = dict(type=t, name=NAMES[t], mode=mode, webcam=webcam, how=how, confidence=conf, reason=reason)
    json.dump(prof, open("work/profile.json", "w"), indent=1)
    print(f"VIDEO TYPE: {t} ({NAMES[t]}), {how}, confidence {conf}. Processing: {mode}. {reason}")


if __name__ == "__main__":
    main()

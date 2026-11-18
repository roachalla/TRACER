"""Send frames to Mathpix /v3/text and save raw JSON + text. Creds come from env vars only."""
import base64, glob, json, os, pathlib, sys, urllib.error, urllib.request

def _dotenv():
    vals = {}
    f = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1); vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


_e = _dotenv()
app_id = os.environ.get("MATHPIX_APP_ID") or os.environ.get("MathPix_App_ID") or _e.get("MATHPIX_APP_ID")
app_key = os.environ.get("MATHPIX_APP_KEY") or os.environ.get("MathPix_API_Key") or _e.get("MATHPIX_APP_KEY")
if not app_id or not app_key:
    sys.exit("Need Mathpix credentials: MathPix_App_ID / MathPix_API_Key env vars, or MATHPIX_APP_ID / MATHPIX_APP_KEY in .env")
PRICE_PER_REQUEST = 0.002  # USD, Mathpix image OCR (first million requests); estimate from their pricing page
n_req = 0

src = sys.argv[1] if len(sys.argv) > 1 else "work/frames_final"
dst = sys.argv[2] if len(sys.argv) > 2 else "work/mathpix"
os.makedirs(dst, exist_ok=True)
DONE = pathlib.Path(dst) / ".ocr_done"  # written only when every slide was read; run_video.py checks it
DONE.unlink(missing_ok=True)


def read(path):
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    body = {"src": "data:image/jpeg;base64," + b64, "formats": ["text", "latex_styled"], "math_inline_delimiters": ["$", "$"],
            "math_display_delimiters": ["$$", "$$"], "include_line_data": True}
    req = urllib.request.Request("https://api.mathpix.com/v3/text", data=json.dumps(body).encode(),
                                 headers={"app_id": app_id, "app_key": app_key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


import time
failed = []
for f in sorted(glob.glob(os.path.join(src, "*.jpg"))):
    name = os.path.splitext(os.path.basename(f))[0]
    out_json = os.path.join(dst, name + ".json")
    if os.path.exists(out_json) and os.path.getmtime(out_json) >= os.path.getmtime(f):
        continue  # already read (a resumed run does not pay twice)
    res = None
    for attempt in range(3):
        try:
            res = read(f); break
        except urllib.error.HTTPError as e:  # report the status only: response bodies can echo request details
            why = f"HTTP {e.code}"
        except Exception as e:
            why = type(e).__name__
        time.sleep(2 * (attempt + 1))
    n_req += 1 if res is not None else 0
    if res is None or res.get("error"):
        failed.append(name); print(name, "FAILED", why if res is None else f"Mathpix error: {res.get('error')}"); continue
    with open(out_json, "w", encoding="utf8") as fh:
        json.dump(res, fh, indent=1)
    with open(os.path.join(dst, name + ".md"), "w", encoding="utf8") as fh:
        fh.write(res.get("text", ""))
    print(name, "ok", "conf=", res.get("confidence"))

print(f"[cost] Mathpix: {n_req} requests x ${PRICE_PER_REQUEST} = ${n_req * PRICE_PER_REQUEST:.3f} (estimate)")
if failed:
    sys.exit(f"Mathpix could not read {len(failed)} slide(s): {', '.join(failed)}. Run again to retry them (slides already read are kept).")
DONE.write_text("ok", encoding="utf8")

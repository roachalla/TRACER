"""TRACER (Transcribe · Reconstruct · Audit · Caption · Encode · Review): a small desktop app around the pipeline in scripts/.   Run:  python olc_gui.py

Pick a video (or a folder of videos), pick where results go, enter your own API keys, press Start.
Keys are kept in memory for the session, or in your system credential vault if you tick "Remember"
(Windows Credential Manager / macOS Keychain via the `keyring` package). They are never written to a
file or to the log, and are handed to the pipeline only as environment variables of its own process."""
import json, os, pathlib, queue, re, shutil, subprocess, sys, threading, time, webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import keyring
    keyring.get_keyring()
except Exception:  # no vault on this machine: keys stay session-only
    keyring = None

ROOT = pathlib.Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from olc_paths import env_python  # noqa: E402
HOME = pathlib.Path.home()
SERVICE = "OLC-lecture-to-textbook"
SETTINGS = HOME / ".olc_lecture_app.json"  # non-secret preferences only (folders, limit)
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
# Flat white design, "Meadow Green" palette (lime -> green -> teal -> blue -> navy)
MEADOW = ["#DCEC94", "#B8E290", "#97D58A", "#76C793", "#57B39A", "#3B9E9F", "#1E88AF", "#1F70A0", "#255E91", "#1E4C72"]
BG, INK, TXT, MUT, LINE = "#FFFFFF", "#1E3550", "#3D4B5C", "#6B7785", "#E6ECEF"
PRIMARY, PRIMARY_HOVER = MEADOW[7], MEADOW[8]
# step cards use the palette colours themselves (saturated), with dark navy text on all of them (contrast >= 4.5:1)
TINTS = {"lime": (MEADOW[0], INK), "green": (MEADOW[1], INK), "mint": (MEADOW[2], INK), "teal": (MEADOW[3], INK), "blue": (MEADOW[4], INK)}
CARD_MUT = "#22364C"   # secondary text on the coloured cards (grey would be unreadable on saturated colour)
OUTPUTS = [("textbook", "Textbook (web page)"), ("teaching", "Teaching edition (takeaways, study questions, glossary)"),
           ("word", "Word document (.docx, editable equations)"), ("transcript", "Transcript and captions (.vtt)")]
TEAL, TEAL2, CYAN = MEADOW[9], MEADOW[7], MEADOW[5]   # names kept for the log colours below
TYPES = [("auto", "Detect automatically"), ("1", "1 · Classroom recording (screen or board, instructor in front)"),
         ("2", "2 · Board only, no instructor in view"), ("3", "3 · Screen share (e.g. Zoom), no webcam"),
         ("4", "4 · Screen share with a webcam picture in a corner"), ("5", "5 · Edited video or PowerPoint recording")]
TYPE_NOTES = {"auto": "The type is detected from four frames before processing (about $0.001). If it finds a classroom recording, you are asked first.",
              "1": "Classroom recordings usually don't work well: the instructor blocks the screen. You'll be offered a transcript only (no slides, images or math).",
              "2": "Chalk or whiteboard: dense frames, sections by topic.", "3": "Slides, full frame.",
              "4": "Slides; the webcam corner is hidden from the slide images.", "5": "Slides, full frame; transitions are ignored."}

# seconds of processing per minute of video, measured on an RTX 4000 Ada laptop (stage names as printed by run_video.py)
STAGE_SECS = {"frames": 0.3, "mathpix ocr": 0.5, "vision-correct OCR (Claude)": 1.8, "asr whisper": 16.1, "asr qwen": 18.7,
              "asr parakeet": 3.1, "gap finder": 0.5, "speaker split": 8.8, "review page": 0.2, "resolve gaps (Claude)": 26.7,
              "review page with LLM fixes": 0.2, "textbook (Claude)": 9.0, "transcript prep": 0.05, "transcript page": 0.1,
              "Word version": 0.2, "sorting outputs": 0.1}
TEXTBOOK_STAGES = {"frames", "mathpix ocr", "vision-correct OCR (Claude)", "review page", "review page with LLM fixes", "textbook (Claude)",
                   "Word version"}
PAID = {"mathpix ocr", "vision-correct OCR (Claude)", "resolve gaps (Claude)", "textbook (Claude)"}
FRIENDLY = {"frames": "Finding slides", "mathpix ocr": "Reading slides (OCR)", "vision-correct OCR (Claude)": "Checking handwriting",
            "asr whisper": "Transcribing (Whisper)", "asr qwen": "Transcribing (Qwen)", "asr parakeet": "Transcribing (Parakeet)",
            "gap finder": "Comparing transcripts", "speaker split": "Separating speakers", "review page": "Building review page",
            "resolve gaps (Claude)": "Resolving unclear passages", "review page with LLM fixes": "Updating review page",
            "textbook (Claude)": "Writing the textbook", "video type": "Identifying the video type",
            "transcript prep": "Preparing the transcript", "transcript page": "Writing the transcript and captions",
            "Word version": "Making the Word version", "sorting outputs": "Sorting the results into folders"}
COST_PER_MIN = 0.07    # USD per video minute, full textbook (measured runs: $0.05-0.08 per minute, more for noisy audio)
COST_PER_VIDEO = 0.08  # USD per video regardless of length (figures, titles, teaching layer, peer review, equation repair)
COST_PER_MIN_TRANSCRIPT = 0.035  # transcript and captions only (measured on a 7-minute test)
OVERHEAD = 60          # seconds per video (loading the speech models)


def fmt_dur(sec):
    sec = int(max(sec, 0))
    h, m = divmod(sec // 60, 60)
    return f"{h} h {m:02d} min" if h else (f"{m} min" if m else f"{sec} s")


def probe_duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def setup_report():
    """(ok, what, how_to_fix) for every component the pipeline needs."""
    rows = [(bool(shutil.which("ffmpeg")), "ffmpeg", "Install ffmpeg and add it to PATH (https://ffmpeg.org)"),
            (bool(shutil.which("ffprobe")), "ffprobe", "Comes with ffmpeg"),
            (env_python("whisper").exists(), "Whisper + OpenCV environment",
             "Create envs/whisper (see README, Setup)"),
            (env_python("parakeet").exists(), "Parakeet environment",
             "Create envs/parakeet (see README, Setup)"),
            (env_python("qwen").exists(), "Qwen3-ASR environment",
             "Create envs/qwen (see README, Setup)"),
            (env_python("speaker").exists(), "Speaker-split environment",
             "Create envs/speaker (see README, Setup)"),
            (all((ROOT / "models" / f).exists() for f in ("parakeet-tdt-0.6b-v3", "qwen3-asr-1.7b"))
             or bool(os.environ.get("OLC_MODELS_DIR")), "Speech models (Parakeet, Qwen3-ASR)",
             "Run: envs\\parakeet\\Scripts\\python scripts\\get_models.py (see README, Setup)")]
    try:
        import anthropic  # noqa: F401
        rows.append((True, "anthropic package", ""))
    except Exception:
        rows.append((False, "anthropic package", "pip install anthropic"))
    return rows


class App(tk.Tk):
    def __init__(self, shot=None):
        super().__init__()
        self.title("TRACER · Accessible math from lecture capture")
        self.geometry("880x1000"); self.minsize(800, 820); self.configure(bg=BG)
        self.videos, self.durs, self.proc, self.cancelled = [], {}, None, False
        self.q = queue.Queue(); self.log_lines = []; self.secrets = set()
        self.cfg = self.load_cfg()
        self.style_ui(); self.build(); self.load_keys(); self.refresh_setup(); self.update_estimate()
        self.after(100, self.pump)
        if shot:
            self.after(1500, lambda: self.take_shot(shot))

    # ---------- settings & keys ----------
    def load_cfg(self):
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_cfg(self):
        try:
            SETTINGS.write_text(json.dumps({"out": self.out_var.get(), "limit": self.limit_var.get(), "in_dir": self.cfg.get("in_dir", ""),
                                            "vtype": self.vtype(), "outputs": self.outputs(), "rates": self.cfg.get("rates", {})}),
                                encoding="utf-8")
        except Exception:
            pass

    def load_keys(self):
        if not keyring:
            self.remember.set(False); self.remember_cb.state(["disabled"])
            self.vault_note.set("No credential vault found on this computer: keys are kept for this session only.")
            return
        got = False
        for var, name in ((self.k_claude, "anthropic"), (self.k_mpid, "mathpix_id"), (self.k_mpkey, "mathpix_key")):
            try:
                v = keyring.get_password(SERVICE, name)
            except Exception:
                v = None
            if v:
                var.set(v); got = True
        if got:
            self.remember.set(True); self.vault_note.set("Saved keys loaded from your system credential vault.")

    def store_keys(self):
        if not keyring:
            return
        try:
            for var, name in ((self.k_claude, "anthropic"), (self.k_mpid, "mathpix_id"), (self.k_mpkey, "mathpix_key")):
                if self.remember.get() and var.get().strip():
                    keyring.set_password(SERVICE, name, var.get().strip())
                else:
                    try:
                        keyring.delete_password(SERVICE, name)
                    except Exception:
                        pass
        except Exception as e:
            self.vault_note.set(f"Could not use the credential vault ({type(e).__name__}); keys kept for this session only.")

    def forget_keys(self):
        if keyring:
            for name in ("anthropic", "mathpix_id", "mathpix_key"):
                try:
                    keyring.delete_password(SERVICE, name)
                except Exception:
                    pass
        for v in (self.k_claude, self.k_mpid, self.k_mpkey):
            v.set("")
        self.remember.set(False); self.vault_note.set("Saved keys removed.")

    # ---------- UI ----------
    def style_ui(self):
        """Flat: white ground, borderless pastel panels, one solid blue primary button, no bevels or outlines."""
        s = ttk.Style(self); s.theme_use("clam")
        F = "Segoe UI"
        s.configure(".", font=(F, 10), background=BG, foreground=TXT, bordercolor=BG, lightcolor=BG, darkcolor=BG, focuscolor=PRIMARY)
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=TXT)
        s.configure("Title.TLabel", background=BG, foreground=INK, font=(F, 26, "bold"))
        s.configure("Sub.TLabel", background=BG, foreground=MUT, font=(F, 11))
        s.configure("Est.TLabel", background=BG, foreground=INK, font=(F, 10, "bold"))
        s.configure("LogH.TLabel", background=BG, foreground=INK, font=(F, 12, "bold"))
        for name, (tint, deep) in TINTS.items():  # one flat pastel panel style per palette tint
            s.configure(f"{name}.TFrame", background=tint)
            s.configure(f"{name}.TLabel", background=tint, foreground=TXT)
            s.configure(f"{name}.H.TLabel", background=tint, foreground=deep, font=(F, 12, "bold"))
            s.configure(f"{name}.Mut.TLabel", background=tint, foreground=CARD_MUT, font=(F, 9))
            s.configure(f"{name}.TCheckbutton", background=tint, foreground=TXT)
            s.map(f"{name}.TCheckbutton", background=[("active", tint)])
        s.configure("TButton", padding=(14, 7), background="#FFFFFF", foreground=INK, bordercolor="#FFFFFF", relief="flat",
                    lightcolor="#FFFFFF", darkcolor="#FFFFFF", focusthickness=0)
        s.map("TButton", background=[("active", "#F1F5F7"), ("disabled", "#F6F8F9")], foreground=[("disabled", "#A7B1BA")])
        s.configure("Link.TButton", padding=(6, 4), background=BG, foreground=PRIMARY, bordercolor=BG, lightcolor=BG, darkcolor=BG)
        s.map("Link.TButton", background=[("active", "#EEF4F8")])
        s.configure("Accent.TButton", background=PRIMARY, foreground="white", padding=(26, 10), font=(F, 11, "bold"),
                    bordercolor=PRIMARY, lightcolor=PRIMARY, darkcolor=PRIMARY, relief="flat")
        s.map("Accent.TButton", background=[("active", PRIMARY_HOVER), ("disabled", "#A9C3D4")], foreground=[("disabled", "white")])
        s.configure("TCheckbutton", background=BG, foreground=TXT); s.map("TCheckbutton", background=[("active", BG)])
        s.configure("TEntry", padding=6, fieldbackground="#FFFFFF", bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        s.configure("TCombobox", padding=5, fieldbackground="#FFFFFF", background="#FFFFFF", bordercolor=LINE, lightcolor=LINE,
                    darkcolor=LINE, arrowcolor=PRIMARY)
        s.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")], selectbackground=[("readonly", "#FFFFFF")],
              selectforeground=[("readonly", TXT)], background=[("readonly", "#FFFFFF"), ("active", "#F1F5F7")])
        s.configure("TSpinbox", padding=4, fieldbackground="#FFFFFF", bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, arrowcolor=PRIMARY)
        s.configure("Horizontal.TProgressbar", troughcolor="#EEF2F4", background=MEADOW[5], bordercolor="#EEF2F4",
                    lightcolor=MEADOW[5], darkcolor=MEADOW[5], thickness=8)
        s.configure("Vertical.TScrollbar", background="#EEF2F4", troughcolor=BG, bordercolor=BG, arrowcolor=MUT, relief="flat")

    def card(self, parent, title, tint="blue"):
        f = ttk.Frame(parent, style=f"{tint}.TFrame", padding=(18, 11))
        f.pack(fill="x", pady=(0, 8))
        f.tint = tint
        ttk.Label(f, text=title, style=f"{tint}.H.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        f.columnconfigure(1, weight=1)
        return f

    def palette_bar(self, parent, width=150, height=6):
        """the flat multicolour accent bar from the reference design, drawn in the Meadow palette"""
        c = tk.Canvas(parent, width=width, height=height, bg=BG, highlightthickness=0)
        seg = width / len(MEADOW)
        for i, col in enumerate(MEADOW):
            c.create_rectangle(i * seg, 0, (i + 1) * seg + 1, height, fill=col, outline=col)
        return c

    def build(self):
        # the whole window scrolls, so the log is always reachable on short screens
        outer = ttk.Frame(self); outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set); vsb.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, padding=(30, 18))
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

        def wheel(e):
            if hasattr(self, "log") and str(e.widget).startswith(str(self.log)) and self.log.yview() != (0.0, 1.0):
                return  # the log scrolls itself when the pointer is over it
            canvas.yview_scroll(int(-e.delta / 120), "units")
        self.bind_all("<MouseWheel>", wheel)
        self.palette_bar(body).pack(anchor="w", pady=(0, 10))
        ttk.Label(body, text="TRACER", style="Title.TLabel").pack(anchor="w")
        ttk.Label(body, text="Transcribe · Reconstruct · Audit · Caption · Encode · Review — accessible math from lecture capture.", style="Sub.TLabel").pack(anchor="w", pady=(2, 6))
        self.setup_var = tk.StringVar()
        row = ttk.Frame(body); row.pack(fill="x", pady=(0, 14))
        self.setup_lbl = ttk.Label(row, textvariable=self.setup_var, foreground=MUT); self.setup_lbl.pack(side="left")
        ttk.Button(row, text="Check setup  →", style="Link.TButton", command=self.show_setup).pack(side="right")

        c1 = self.card(body, "1   Videos", "lime")
        ttk.Button(c1, text="Choose a video…", command=self.pick_file).grid(row=1, column=0, sticky="w")
        ttk.Button(c1, text="Choose a folder…", command=self.pick_folder).grid(row=1, column=1, sticky="w", padx=8)
        self.sel_var = tk.StringVar(value="Nothing selected yet")
        ttk.Label(c1, textvariable=self.sel_var, style="lime.Mut.TLabel").grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        c1b = self.card(body, "2   Video type", "green")
        self.type_var = tk.StringVar(value=dict(TYPES)[self.cfg.get("vtype", "auto")])
        cb = ttk.Combobox(c1b, textvariable=self.type_var, values=[t[1] for t in TYPES], state="readonly", width=58)
        cb.grid(row=1, column=0, columnspan=4, sticky="w")
        self.type_note = tk.StringVar()
        ttk.Label(c1b, textvariable=self.type_note, style="green.Mut.TLabel", wraplength=740, justify="left").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.type_var.trace_add("write", lambda *a: self.type_note.set(TYPE_NOTES[self.vtype()]))
        self.type_note.set(TYPE_NOTES[self.vtype()])

        c1c = self.card(body, "3   What to make", "mint")
        saved = set(self.cfg.get("outputs", [k for k, _ in OUTPUTS]))
        self.out_sel = {}
        for i, (k, lab) in enumerate(OUTPUTS):
            v = tk.BooleanVar(value=k in saved); self.out_sel[k] = v
            ttk.Checkbutton(c1c, text=lab, variable=v, style="mint.TCheckbutton", command=self.update_estimate).grid(
                row=1 + i // 2, column=(i % 2) * 2, columnspan=2, sticky="w", pady=2, padx=(0, 18))
        ttk.Label(c1c, style="mint.Mut.TLabel", wraplength=740, justify="left",
                  text="Review pages and the audit log are always made. Choosing only Transcript and captions skips the slides and "
                       "the textbook writing: faster and cheaper.").grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))

        c2 = self.card(body, "4   Save results to", "teal")
        self.out_var = tk.StringVar(value=self.cfg.get("out", ""))
        ttk.Entry(c2, textvariable=self.out_var, state="readonly").grid(row=1, column=0, columnspan=3, sticky="ew")
        ttk.Button(c2, text="Choose…", command=self.pick_out).grid(row=1, column=3, padx=(8, 0))

        c3 = self.card(body, "5   Your API keys", "blue")
        self.k_claude, self.k_mpid, self.k_mpkey = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.key_entries = []
        for i, (lab, var) in enumerate((("Claude (Anthropic) key", self.k_claude), ("Mathpix app ID", self.k_mpid), ("Mathpix app key", self.k_mpkey))):
            ttk.Label(c3, text=lab, style="blue.TLabel").grid(row=1 + i, column=0, sticky="w", pady=3)
            e = ttk.Entry(c3, textvariable=var, show="•"); e.grid(row=1 + i, column=1, columnspan=3, sticky="ew", padx=(12, 0), pady=3)
            self.key_entries.append(e)
        self.show_keys = tk.BooleanVar(value=False)
        ttk.Checkbutton(c3, text="Show keys", variable=self.show_keys, command=self.toggle_show, style="blue.TCheckbutton").grid(row=4, column=1, sticky="w", padx=12)
        self.remember = tk.BooleanVar(value=False)
        self.remember_cb = ttk.Checkbutton(c3, text="Remember on this computer", variable=self.remember, style="blue.TCheckbutton")
        self.remember_cb.grid(row=4, column=2, sticky="w")
        ttk.Button(c3, text="Forget saved keys", command=self.forget_keys).grid(row=4, column=3, sticky="e")
        ttk.Button(c3, text="Check Claude key", command=self.check_key).grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.vault_note = tk.StringVar(value="")
        ttk.Label(c3, textvariable=self.vault_note, style="blue.Mut.TLabel").grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Label(c3, style="blue.Mut.TLabel", wraplength=740, justify="left",
                  text="Your keys stay on this computer. They are held in memory (or in your system's credential vault if you tick Remember), "
                       "never saved in a file or shown in the log, and sent only to Anthropic and Mathpix, which bill your own accounts."
                  ).grid(row=6, column=0, columnspan=4, sticky="w", pady=(4, 0))

        run = ttk.Frame(body); run.pack(fill="x", pady=(6, 6))
        self.est_var = tk.StringVar()
        ttk.Label(run, textvariable=self.est_var, style="Est.TLabel").grid(row=0, column=0, columnspan=6, sticky="w")
        ttk.Label(run, text="Spending limit (USD)").grid(row=1, column=0, sticky="w", pady=6)
        self.limit_var = tk.StringVar(value=str(self.cfg.get("limit", "10")))
        ttk.Spinbox(run, from_=1, to=500, increment=1, width=6, textvariable=self.limit_var).grid(row=1, column=1, sticky="w", padx=8)
        self.free_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(run, text="Free steps only (transcripts, no API cost)", variable=self.free_only,
                        command=self.update_estimate, style="TCheckbutton").grid(row=1, column=2, sticky="w", padx=14)
        self.start_btn = ttk.Button(run, text="Start", style="Accent.TButton", command=self.start); self.start_btn.grid(row=1, column=4, sticky="e")
        self.cancel_btn = ttk.Button(run, text="Cancel", command=self.cancel, state="disabled"); self.cancel_btn.grid(row=1, column=5, padx=(8, 0))
        run.columnconfigure(3, weight=1)

        self.prog = ttk.Progressbar(body, maximum=1000); self.prog.pack(fill="x", pady=(4, 4))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(body, textvariable=self.status_var).pack(anchor="w")

        head = ttk.Frame(body); head.pack(fill="x", pady=(12, 4))
        ttk.Label(head, text="Log", style="LogH.TLabel").pack(side="left")
        self.details = tk.BooleanVar(value=False)
        ttk.Checkbutton(head, text="Show details", variable=self.details, command=self.redraw_log, style="TCheckbutton").pack(side="left", padx=12)
        for text, cmd in (("Review pages", lambda: self.open_index("index_review.html")), ("Student pages", lambda: self.open_index("index_student.html")),
                          ("Save log…", self.save_log)):
            ttk.Button(head, text=text, command=cmd).pack(side="right", padx=(6, 0))
        wrap = ttk.Frame(body); wrap.pack(fill="both", expand=True)
        self.log = tk.Text(wrap, height=14, wrap="word", font=("Consolas", 9), bg="#F7F9FA", fg=TXT, relief="flat", borderwidth=0,
                           highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE, padx=10, pady=8, state="disabled")
        sb = ttk.Scrollbar(wrap, command=self.log.yview); self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); self.log.pack(fill="both", expand=True)
        self.log.tag_configure("stage", foreground=TEAL, font=("Consolas", 9, "bold")); self.log.tag_configure("err", foreground="#A8322B")
        self.log.tag_configure("ok", foreground="#2E7D32")
        for v in (self.out_var, self.free_only):
            v.trace_add("write", lambda *a: self.update_estimate())

    def vtype(self):
        label = self.type_var.get()
        return next((k for k, v in TYPES if v == label), "auto")

    def toggle_show(self):
        for e in self.key_entries:
            e.configure(show="" if self.show_keys.get() else "•")

    # ---------- selection ----------
    def pick_file(self):
        f = filedialog.askopenfilename(title="Choose a lecture video", initialdir=self.cfg.get("in_dir") or None,
                                       filetypes=[("Video files", "*.mp4 *.mov *.mkv *.m4v *.avi *.webm"), ("All files", "*.*")])
        if f:
            self.cfg["in_dir"] = str(pathlib.Path(f).parent); self.set_videos([pathlib.Path(f)])

    def pick_folder(self):
        d = filedialog.askdirectory(title="Choose a folder of lecture videos", initialdir=self.cfg.get("in_dir") or None)
        if d:
            self.cfg["in_dir"] = d
            vids = sorted(p for p in pathlib.Path(d).rglob("*") if p.suffix.lower() in VIDEO_EXT)
            if not vids:
                messagebox.showinfo("No videos found", "That folder has no video files (mp4, mov, mkv, m4v, avi, webm).")
                return
            self.set_videos(vids)

    def pick_out(self):
        d = filedialog.askdirectory(title="Where should the results go?", initialdir=self.out_var.get() or None)
        if d:
            self.out_var.set(d)

    def set_videos(self, vids):
        self.videos = vids; self.durs = {}
        self.sel_var.set(f"{len(vids)} video(s) selected. Measuring length…")
        threading.Thread(target=self._measure, args=(list(vids),), daemon=True).start()

    def _measure(self, vids):
        d = {v: probe_duration(v) for v in vids}
        self.q.put(("durs", d))

    def total_minutes(self):
        return sum(self.durs.values()) / 60

    def outputs(self):
        return [k for k, _ in OUTPUTS if self.out_sel[k].get()]

    def transcript_only(self):
        return not set(self.outputs()) & {"textbook", "teaching", "word"}

    def stage_list(self):
        return [s for s in STAGE_SECS if not (self.free_only.get() and s in PAID)
                and not (self.transcript_only() and s in TEXTBOOK_STAGES)
                and not (s in ("transcript prep",) and not self.transcript_only())]

    def rate(self, stage):
        """seconds per video minute for a stage: measured on this computer in earlier runs when available, else the default"""
        return float(self.cfg.get("rates", {}).get(stage, STAGE_SECS.get(stage, 1.0)))

    def per_min(self):
        return sum(self.rate(s) for s in self.stage_list())

    def est_seconds(self, minutes=None):
        m = self.total_minutes() if minutes is None else minutes
        return m * self.per_min() + OVERHEAD * len(self.videos)

    def est_cost(self):
        if self.transcript_only():
            return self.total_minutes() * COST_PER_MIN_TRANSCRIPT
        return self.total_minutes() * COST_PER_MIN + COST_PER_VIDEO * len(self.videos)

    def update_estimate(self):
        if not self.videos or not self.durs:
            self.est_var.set("Choose videos to see a time and cost estimate."); return
        m = self.total_minutes()
        cost = 0 if self.free_only.get() else self.est_cost()
        self.est_var.set(f"Estimated time about {fmt_dur(self.est_seconds())}"
                         + ("" if self.free_only.get() else f", cost about ${cost:.2f} (Claude + Mathpix, on your accounts)")
                         + f"   ·   {len(self.videos)} video(s), {fmt_dur(m * 60)} of footage")

    # ---------- setup / keys check ----------
    def refresh_setup(self):
        bad = [r for r in setup_report() if not r[0]]
        self.setup_var.set("Setup complete: everything the pipeline needs was found." if not bad
                           else f"Setup incomplete: {len(bad)} item(s) missing. Choose 'Check setup' for details.")
        self.setup_lbl.configure(foreground=MUT if not bad else "#A8322B")

    def show_setup(self):
        w = tk.Toplevel(self); w.title("Setup check"); w.configure(bg="white"); w.geometry("640x380")
        t = tk.Text(w, wrap="word", font=("Segoe UI", 10), bg="white", relief="flat", padx=16, pady=14)
        t.pack(fill="both", expand=True)
        for ok, what, fix in setup_report():
            t.insert("end", ("✓  " if ok else "✗  ") + what + "\n", "ok" if ok else "bad")
            if not ok:
                t.insert("end", "      " + fix + "\n")
        t.insert("end", "\nThe free steps also need a GPU for reasonable speed (an NVIDIA card with 8 GB or more works best). "
                        "See README.md for the full setup.")
        t.tag_configure("bad", foreground="#A8322B"); t.configure(state="disabled")

    def check_key(self):
        k = self.k_claude.get().strip()
        if not k:
            self.vault_note.set("Enter a Claude key first."); return
        self.vault_note.set("Checking…")

        def work():
            try:
                import anthropic
                anthropic.Anthropic(api_key=k).models.list(limit=1)
                self.q.put(("note", "Claude key works."))
            except Exception as e:
                msg = "Claude rejected this key." if "authentication" in type(e).__name__.lower() or "401" in str(e) else "Could not check the key (offline?)."
                self.q.put(("note", msg))
        threading.Thread(target=work, daemon=True).start()

    # ---------- run ----------
    def start(self):
        if not self.videos:
            messagebox.showinfo("Choose videos", "Choose a video or a folder first."); return
        if not self.out_var.get():
            messagebox.showinfo("Choose a destination", "Choose where the results should be saved."); return
        if not self.outputs():
            messagebox.showinfo("Choose what to make", "Tick at least one item under 'What to make'."); return
        free = self.free_only.get()
        if not free:
            missing = [n for n, v in (("Claude key", self.k_claude), ("Mathpix app ID", self.k_mpid), ("Mathpix app key", self.k_mpkey)) if not v.get().strip()]
            if missing:
                messagebox.showinfo("API keys needed", "Please enter: " + ", ".join(missing) + ".\n\nOr tick 'Free steps only' to run without keys.")
                return
        bad = [r for r in setup_report() if not r[0]]
        if bad and messagebox.askyesno("Setup incomplete", "Some components are missing:\n  " + "\n  ".join(r[1] for r in bad) + "\n\nStart anyway?") is False:
            return
        try:
            limit = float(self.limit_var.get())
        except ValueError:
            messagebox.showinfo("Spending limit", "Enter the spending limit as a number, e.g. 10."); return
        m = self.total_minutes()
        msg = (f"{len(self.videos)} video(s), {fmt_dur(m * 60)} of footage.\nEstimated time about {fmt_dur(self.est_seconds())}.\n" +
               ("No API cost.\n" if free else f"Estimated cost about ${self.est_cost():.2f}, billed to your Anthropic and Mathpix accounts.\n"
                f"The run stops by itself if Claude spend passes ${limit:.2f}.\n") + "\nStart?")
        if not messagebox.askokcancel("Ready to start", msg):
            return
        self.store_keys(); self.save_cfg(); self.run_type = self.vtype(); self.run_outputs = self.outputs()
        self.secrets = {v.get().strip() for v in (self.k_claude, self.k_mpid, self.k_mpkey) if len(v.get().strip()) >= 6}
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        env.pop("ANTHROPIC_API_KEY", None)  # the pipeline never uses an ambient Claude key
        if not free:
            env["OLC_SCRIPT_API_KEY"] = self.k_claude.get().strip()
            env["MATHPIX_APP_ID"] = self.k_mpid.get().strip(); env["MATHPIX_APP_KEY"] = self.k_mpkey.get().strip()
        self.cancelled = False; self.spent, self.limit = 0.0, limit
        self.log_lines.clear(); self.redraw_log()
        out = pathlib.Path(self.out_var.get()); out.mkdir(parents=True, exist_ok=True)
        self.log_path = out / "run_log.txt"
        self.start_btn.state(["disabled"]); self.cancel_btn.state(["!disabled"])
        self.t_start = time.time(); self.est_total = self.est_seconds(); self.est_done = 0.0; self.act_done = 0.0
        self.cur_stage, self.cur_t0, self.cur_est, self.vi = None, 0, 0, 0
        threading.Thread(target=self.worker, args=(list(self.videos), dict(self.durs), out, free, env), daemon=True).start()

    def worker(self, vids, durs, out, free, env):
        flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
        fails = 0
        for i, v in enumerate(vids):
            if self.cancelled:
                break
            self.q.put(("video", i, len(vids), v.stem, durs.get(v, 0)))
            left = max(self.limit - self.spent, 0.0)  # what is left of the limit; the pipeline enforces it before every Claude call
            base = [sys.executable, str(SCRIPTS / "run_video.py"), str(v), "--runs", str(out), "--type", self.run_type,
                    "--outputs", ",".join(self.run_outputs), "--budget", f"{left:.2f}"] + (["--free-only"] if free else [])
            rc = self.run_cmd(base, env, flags)
            if rc == 4:  # the spending limit was reached inside the pipeline: stop the whole batch
                self.q.put(("budget_stop",))
                self.q.put(("video_done", i, rc)); break
            if rc == 3 and not self.cancelled:  # classroom recording: ask before doing anything more
                ev, ans = threading.Event(), {}
                self.q.put(("ask_type1", v.name, ev, ans)); ev.wait()
                if ans.get("yes"):
                    self.q.put(("line", "Making a transcript only for this video."))
                    rc = self.run_cmd(base + ["--transcript-only"], env, flags)
                else:
                    self.q.put(("line", "Skipped: classroom recording, no transcript requested.")); rc = 0
            if rc != 0 and not self.cancelled:
                fails += 1
            self.q.put(("video_done", i, rc))
            for s in ("build_index.py", "build_checklist.py"):
                try:
                    subprocess.run([sys.executable, str(SCRIPTS / s), str(out)], cwd=str(ROOT), capture_output=True, timeout=120, **flags)
                except Exception:
                    pass
        self.q.put(("finished", fails))

    def run_cmd(self, cmd, env, flags):
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                         errors="replace", env=env, cwd=str(ROOT), **flags)
            for line in self.proc.stdout:
                self.q.put(("line", line.rstrip("\r\n")))
            return self.proc.wait()
        except Exception as e:
            self.q.put(("line", f"STAGE FAILED: could not start the pipeline ({type(e).__name__})"))
            return 1

    def ask_type1(self, name):
        return messagebox.askyesno(
            "Classroom recording",
            f"{name} looks like a classroom recording: a camera filming a screen or board with the instructor in front.\n\n"
            "This kind of video likely won't work well. The instructor blocks the screen, so slides, figures and equations "
            "would come out unreliable.\n\n"
            "Make a transcript only instead? You get a cleaned, checked transcript with time stamps, but no slides, images "
            "or math formatting.\n\n"
            "Yes = make a transcript only.   No = skip this video.\n"
            "(If this is not a classroom recording, choose the right type under Video type and start again.)")

    def cancel(self):
        self.cancelled = True
        p = self.proc
        if p and p.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    import signal
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                pass
        self.status_var.set("Cancelling…")

    # ---------- events from the worker ----------
    STAGE_RE = re.compile(r"^=== \[(.+?)\] (.+)$"); SKIP_RE = re.compile(r"^--- \[(.+?)\] (\w+): already done")
    COST_RE = re.compile(r"= \$([0-9.]+)")

    def stage_done(self, now):
        if self.cur_stage:
            self.est_done += self.cur_est; self.act_done += now - self.cur_t0
            dmin = self.durs.get(self.videos[self.vi], 0) / 60 if self.vi < len(self.videos) else 0
            st = self.cur_stage
            if dmin >= 1 and st in STAGE_SECS:  # learn this computer's speed, so the next estimate is realistic
                measured = (now - self.cur_t0) / dmin
                rates = self.cfg.setdefault("rates", {})
                rates[st] = round(0.5 * rates[st] + 0.5 * measured, 2) if st in rates else round(measured, 2)
                self.save_cfg()
                if st.startswith("asr") and measured > 3 * STAGE_SECS[st]:
                    self.append_log(f"Note: {FRIENDLY.get(st, st)} took {measured / 60:.1f} min per minute of video, much slower than "
                                    "expected. Check that the graphics card (GPU) is being used; on the processor alone it is very slow.", "err")
        self.cur_stage = None

    def scale(self):
        return min(max(self.act_done / self.est_done, 0.3), 4) if self.est_done > 5 else 1.0

    def update_progress(self):
        now = time.time()
        part = 0.0
        if self.cur_stage:
            part = min(self.cur_est * 0.95, (now - self.cur_t0) / max(self.scale(), 0.01))
        done = self.est_done + part
        self.prog["value"] = 1000 * min(done / max(self.est_total, 1), 1)
        left = max(self.est_total - done, 0) * self.scale()
        label = FRIENDLY.get(self.cur_stage, self.cur_stage or "Starting")
        cost = "" if self.free_only.get() else f"  ·  Claude spend so far ≈ ${self.spent:.2f}"
        self.status_var.set(f"Video {self.vi + 1} of {len(self.videos)}  ·  {label}  ·  about {fmt_dur(left)} left{cost}")

    def handle_line(self, line):
        for s in self.secrets:
            line = line.replace(s, "••••")
        self.log_lines.append(line)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        self.append_log(line)
        m = self.STAGE_RE.match(line)
        if m:
            now = time.time(); self.stage_done(now)
            stage = m.group(2); dmin = self.durs.get(self.videos[self.vi], 0) / 60 if self.vi < len(self.videos) else 0
            self.cur_stage, self.cur_t0, self.cur_est = stage, now, self.rate(stage) * dmin
        m = self.SKIP_RE.match(line)
        if m:
            pass  # skipped stages are credited at video end
        m = self.COST_RE.search(line)
        if m and "[spend] claude" in line:  # one line per Claude call (Mathpix is logged as [cost] and not counted here)
            self.spent += float(m.group(1))
        if self.spent > self.limit * 1.1 and not self.cancelled:  # backstop only: the pipeline itself stops at the limit
            self.append_log(f"Spending limit of ${self.limit:.2f} passed: stopping.", "err")
            self.cancel()
            messagebox.showwarning("Spending limit reached", f"Claude spend reached about ${self.spent:.2f}. The run was stopped. "
                                   "Finished stages are kept and will not be paid for again if you start the same videos later.")

    def pump(self):
        try:
            while True:
                ev = self.q.get_nowait()
                k = ev[0]
                if k == "line":
                    self.handle_line(ev[1])
                elif k == "durs":
                    self.durs = ev[1]
                    m = sum(ev[1].values()) / 60
                    self.sel_var.set(f"{len(self.videos)} video(s) selected, {fmt_dur(m * 60)} of footage"
                                     + ("" if len(self.videos) > 1 else f"  ·  {self.videos[0].name}"))
                    self.update_estimate()
                elif k == "note":
                    self.vault_note.set(ev[1])
                elif k == "ask_type1":
                    ev[3]["yes"] = self.ask_type1(ev[1]); ev[2].set()
                elif k == "video":
                    self.vi = ev[1]; self.log_ev(f"\n▶ Video {ev[1] + 1} of {ev[2]}: {ev[3]}", "stage")
                elif k == "video_done":
                    now = time.time(); self.stage_done(now)
                    # credit the whole video so skipped (already finished) stages don't leave the bar behind
                    v = self.videos[ev[1]]; dmin = self.durs.get(v, 0) / 60
                    target = self.per_min() * dmin + OVERHEAD
                    base = sum((self.per_min() * self.durs.get(x, 0) / 60 + OVERHEAD) for x in self.videos[:ev[1]])
                    self.est_done = max(self.est_done, base + target)
                    self.log_ev("✓ finished" if ev[2] == 0 else ("Stopped" if self.cancelled else "✗ this video failed (see messages above)"),
                                "ok" if ev[2] == 0 else "err")
                elif k == "budget_stop":
                    self.cancelled = True
                    self.append_log(f"Spending limit of ${self.limit:.2f} reached: stopped.", "err")
                    messagebox.showwarning("Spending limit reached", f"Claude spend reached about ${self.spent:.2f}, the limit you set. "
                                           "The run stopped before the next paid call. Finished steps are kept and will not be paid "
                                           "for again; raise the limit and start the same videos to continue.")
                elif k == "finished":
                    self.finish(ev[1])
            # (loop exits via queue.Empty)
        except queue.Empty:
            pass
        if self.proc is not None and self.cancel_btn.instate(["!disabled"]):
            self.update_progress()
        self.after(150, self.pump)

    def finish(self, fails):
        self.start_btn.state(["!disabled"]); self.cancel_btn.state(["disabled"])
        el = time.time() - self.t_start
        if self.cancelled:
            self.status_var.set(f"Stopped after {fmt_dur(el)}. Claude spend ≈ ${self.spent:.2f}.")
        else:
            self.prog["value"] = 1000
            self.status_var.set(f"Done in {fmt_dur(el)}." + ("" if fails == 0 else f" {fails} video(s) had problems.")
                                + ("" if self.free_only.get() else f" Claude spend ≈ ${self.spent:.2f}."))
            self.log_ev(f"\nAll done. Results are in {self.out_var.get()}", "ok")
        self.proc = None

    # ---------- log ----------
    def keep(self, line):
        if self.details.get():
            return True
        return bool(re.match(r"^(===|---|STAGE FAILED|Traceback|\w*Error|wrote |TOTAL SPEND|slide area|merging repeated|section \d|\[.*spend so far|frames:)", line)) \
            or "ERROR" in line

    def log_ev(self, text, tag=None):
        self.log_lines.append(text); self.append_log(text, tag, force=True)

    def append_log(self, line, tag=None, force=False):
        if not (force or self.keep(line)):
            return
        if tag is None:
            if line.startswith("==="):
                tag = "stage"; line = "   " + (FRIENDLY.get(self.STAGE_RE.match(line).group(2), self.STAGE_RE.match(line).group(2)) if self.STAGE_RE.match(line) else line)
            elif line.startswith(("STAGE FAILED", "Traceback")) or "Error" in line[:40]:
                tag = "err"
        self.log.configure(state="normal"); self.log.insert("end", line + "\n", tag or ()); self.log.see("end"); self.log.configure(state="disabled")

    def redraw_log(self):
        self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")
        for l in self.log_lines:
            self.append_log(l, force=l.startswith(("\n▶", "✓", "Stopped", "✗", "\nAll done")))

    def save_log(self):
        f = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="trace_log.txt")
        if f:
            pathlib.Path(f).write_text("\n".join(self.log_lines), encoding="utf-8")

    def open_index(self, name):
        p = pathlib.Path(self.out_var.get()) / name
        if p.exists():
            webbrowser.open(p.as_uri())
        else:
            messagebox.showinfo("Not ready yet", "Results appear here after the first video finishes.")

    def take_shot(self, path):
        try:
            from PIL import ImageGrab
            self.attributes("-topmost", True); self.lift(); self.update(); time.sleep(0.6); self.update()
            x, y, w, h = self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height()
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
        finally:
            self.destroy()


if __name__ == "__main__":
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled displays
        except Exception:
            pass
    shot =sys.argv[sys.argv.index("--shot") + 1] if "--shot" in sys.argv else None
    App(shot).mainloop()

"""Shared helper: cached, spend-reporting Claude calls that return one structured tool call.
Key comes only from the gitignored .env file (OLC_SCRIPT_API_KEY). Prices: USD per million tokens (input, output)."""
import hashlib, json, os, pathlib

def get_key():
    """Script-only API key. Read ONLY from the gitignored .env file (never from the process environment),
    so Claude Code / agents that read ANTHROPIC_API_KEY can never pick it up, and it is passed to the client explicitly.
    Preferred name: OLC_SCRIPT_API_KEY. Legacy ANTHROPIC_API_KEY lines in .env are still accepted (rename them)."""
    if os.environ.get("OLC_SCRIPT_API_KEY"):  # set only by the desktop app / the user for this process; deliberately not ANTHROPIC_API_KEY
        return os.environ["OLC_SCRIPT_API_KEY"]
    vals = {}
    f = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    if vals.get("OLC_SCRIPT_API_KEY"):
        return vals["OLC_SCRIPT_API_KEY"]
    if vals.get("ANTHROPIC_API_KEY"):
        print("WARNING: .env uses the legacy name ANTHROPIC_API_KEY; rename it to OLC_SCRIPT_API_KEY.")
        return vals["ANTHROPIC_API_KEY"]
    raise SystemExit("No OLC_SCRIPT_API_KEY in .env. (The script deliberately ignores the ANTHROPIC_API_KEY environment variable.)")


_CFG = json.load(open(pathlib.Path(__file__).resolve().parent.parent / "config/models.json", encoding="utf8"))
MODELS = _CFG["roles"]                                    # role -> model id (config/models.json; flags override)
PRICE = {k: tuple(v) for k, v in _CFG["prices"].items()}
SPEND = {"usd": 0.0, "calls": 0}
CACHE = pathlib.Path("work/llm_cache"); CACHE.mkdir(parents=True, exist_ok=True)


def cost(model, usage):
    pi, po = PRICE.get(model, (5.0, 25.0))  # unknown model: assume expensive
    return (usage["input_tokens"] * pi + usage["output_tokens"] * po) / 1e6


# ---------- spending limit: every Claude call in the pipeline passes through call(), so the limit holds on the command line too.
# TRACER_BUDGET_USD = the most this video run may spend on Claude. run_video.py sets TRACER_SPEND_OFFSET to the size of the spend
# log when the run starts, so the limit counts this run only, across all of its steps (each step is a separate process).
SPEND_LOG = pathlib.Path("work/llm_spend.jsonl")


class BudgetExceeded(SystemExit):
    pass


def spent_this_run():
    off = os.environ.get("TRACER_SPEND_OFFSET")
    if off is None or not SPEND_LOG.exists():
        return SPEND["usd"]
    with open(SPEND_LOG, encoding="utf8") as f:
        f.seek(int(off))
        return sum(json.loads(l).get("usd", 0) for l in f if l.strip())


def check_budget():
    b = os.environ.get("TRACER_BUDGET_USD")
    if b and spent_this_run() >= float(b):
        print(f"[budget] Spending limit reached: ${spent_this_run():.2f} of ${float(b):.2f}. Stopping. Finished work is saved; "
              f"run again with a higher limit to continue (cached answers are free).")
        raise BudgetExceeded(4)


def record(model, label, usage, note=""):
    """one log line per paid call, in one format (the desktop app reads the printed line; the audit summary reads the file)"""
    c = cost(model, usage)
    SPEND["usd"] += c; SPEND["calls"] += 1
    with open(SPEND_LOG, "a", encoding="utf8") as log:
        log.write(json.dumps({"model": model, "label": label, **usage, "usd": round(c, 6)}) + "\n")
    print(f"   [spend] claude {label or model}: {usage['input_tokens']} in / {usage['output_tokens']} out = ${c:.4f}   "
          f"running total ${SPEND['usd']:.4f}{note}")
    return c


def call(model, system, tool, user, *, effort="low", max_tokens=2000, label="", dry_run=False, images=(), cache_file=None):
    """Returns (tool_input_dict_or_None, was_fresh_call). Cached results are free. `images`: optional JPEG/PNG file paths.
    `cache_file`: keep an older script's own cache file name, so its earlier runs stay free."""
    blobs = [pathlib.Path(p).read_bytes() for p in images]
    key = hashlib.sha256(json.dumps([model, system, tool, user, effort] + [hashlib.sha256(b).hexdigest() for b in blobs]).encode()).hexdigest()[:24]
    f = pathlib.Path(cache_file) if cache_file else CACHE / f"{key}.json"
    if f.exists():
        with open(f, encoding="utf8") as fh:
            d = json.load(fh)
        return (d["out"] if isinstance(d, dict) and "out" in d else d), False
    if dry_run:
        return None, False
    check_budget()
    import anthropic, base64
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png" if b[:4] == b"\x89PNG" else "image/jpeg",
                                            "data": base64.standard_b64encode(b).decode()}} for b in blobs]
    content.append({"type": "text", "text": user + f"\n\nRespond by calling the `{tool['name']}` tool exactly once."})
    client = anthropic.Anthropic(api_key=get_key())
    # (forcing the tool with tool_choice is not supported by every model, so it is asked for; an answer without the tool call is
    # retried once instead of being lost, so a later run does not pay for it again)
    out = None
    for attempt in range(2):
        r = client.messages.create(model=model, max_tokens=max_tokens, system=system, tools=[tool], tool_choice={"type": "auto"},
                                   output_config={"effort": effort}, messages=[{"role": "user", "content": content}])
        out = next((b.input for b in r.content if b.type == "tool_use"), None)
        usage = {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}
        record(model, label, usage, "" if out is not None else "  (no structured answer" + ("; retrying)" if attempt == 0 else "; not cached)"))
        if out is not None:
            break
        check_budget()
    if out is not None:
        with open(f, "w", encoding="utf8") as fh:
            json.dump({"out": out, "usage": usage, "usd": cost(model, usage)}, fh, indent=1)
    return out, True

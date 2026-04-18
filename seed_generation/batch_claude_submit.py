"""
batch_claude_submit.py
──────────────────────
STEP 1 of 2 — Submit a full seed file as a single Anthropic Message Batch.

Unlike the server/client approach (which processes one entry at a time and
needs a running FastAPI server), this submits ALL entries at once as a batch
and exits immediately.  Anthropic processes them asynchronously at 50% cost.

Workflow:
  Step 1 — python batch_claude_submit.py  [options]
            → Submits batch → saves batch_state_<lang>_<version>_<ts>.json
  Step 2 — python batch_claude_collect.py [--state <state_file>]
            → Polls batch → downloads results → builds devotionals → saves JSON

Batch limits:
  • 100,000 requests or 256 MB per batch (whichever first)
  • Results available for 29 days
  • Batch expires after 24 hours if not completed

CLI usage:
  python batch_claude_submit.py \\
         --seed     seeds/2025/seed_ar_NAV_2025.json \\
         --lang     ar \\
         --version  NAV \\
         --output   2025/yearly_devotionals/AR \\
         [--model   claude-haiku-4-5-20251001] \\
         [--start-date 2025-06-01] \\
         [--limit 10]

Interactive (Tkinter):
  python batch_claude_submit.py

Requires:
  ANTHROPIC_API_KEY in environment or .env file
  pip install anthropic python-dotenv
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

try:
    import anthropic
except ImportError:
    raise ImportError("anthropic package not installed.  Run: pip install anthropic")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from tkinter import Tk, filedialog, messagebox, simpledialog
    _HAS_TKINTER = True
except ImportError:
    _HAS_TKINTER = False

# =============================================================================
# CONFIG
# =============================================================================

# Model choices (cheapest → best quality)
BATCH_MODEL_DEFAULT = "claude-haiku-4-5-20251001"   # 50% off batch → ~$0.25/$1.25 per MTok
MAX_TOKENS          = 4096

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# =============================================================================
# PROMPT BUILDER
# =============================================================================

def build_prompt(verse_cita: str, lang: str, topic: str | None = None) -> str:
    """
    Identical prompt to API_Server_Seed_Claude.py so output format is consistent.
    Returns reflexion + oracion as a JSON object.
    """
    parts = "\n\n".join([
        f"You are a devoted biblical devotional writer. "
        f"Write a devotional in {lang.upper()} based on the key verse: \"{verse_cita}\".",

        "Return ONLY a valid JSON object with these exact keys:",

        f"- `reflexion`: Deep contextualized reflection on the verse "
        f"(minimum 900 characters, approximately 300 words, in {lang}). "
        f"Each paragraph must develop a distinct aspect of the verse.",
        f"Do NOT repeat any word consecutively, even when separated by punctuation marks — "
        f"Never write patterns (example: 'word, word', 'word. Word', 'word; word').",
        f"- Do NOT repeat the same sentence, phrase, or idea in different words.\n"

        f"- `oracion`: Prayer on the devotional theme (minimum 150 words, 100% in {lang}). "
        f"MUST end with 'in the name of Jesus, amen' correctly translated to {lang}. "
        f"End with exactly one Amen — never write Amen twice.",
        f"Do NOT repeat any word consecutively, even when separated by punctuation marks — "
        f"Never write patterns (example: 'word, word', 'word. Word', 'word; word').",
        f"- Do NOT repeat the same sentence, phrase, or idea in different words.\n"

        f"RULES:\n"
        f"- ALL text MUST be 100% in {lang} — no language mixing.\n"
        f"- Do NOT include transliterations, romanizations, or text in parentheses.\n"
        f"- Do NOT repeat any word consecutively.\n"
        f"Do NOT repeat any word consecutively, even when separated by punctuation marks — "
        f"Never write patterns (example: 'word, word', 'word. Word', 'word; word').",
        f"- Do NOT repeat the same sentence, phrase, or idea in different words.\n"
        f"- Every sentence must introduce new content or a new perspective."
        + (f"\n- Suggested theme: {topic}." if topic else ""),
    ])
    return parts


def _safe_custom_id(date_key: str) -> str:
    """
    custom_id must match ^[a-zA-Z0-9_-]{1,64}$
    Date keys like '2025-01-15' are already valid (alphanumeric + hyphens).
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "_", date_key)[:64]


# =============================================================================
# SUBMIT
# =============================================================================

def submit_batch(
    seed_path: str,
    master_lang: str,
    master_version: str,
    output_dir: str,
    model: str = BATCH_MODEL_DEFAULT,
    start_date: str | None = None,
    limit: int | None = None,
) -> str:
    """
    Load seed, build batch requests, submit to Anthropic, save state file.
    Returns path to state JSON file.
    """
    SEP = "=" * 60

    # ── Load seed ──────────────────────────────────────────────────────────
    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    all_dates = sorted(seed.keys())

    if start_date:
        all_dates = [d for d in all_dates if d >= start_date]
    if limit:
        all_dates = all_dates[:limit]

    total = len(all_dates)

    print("\n" + SEP)
    print("ANTHROPIC BATCH SUBMIT")
    print(SEP)
    print(f"  Seed       : {seed_path}")
    print(f"  Lang       : {master_lang}  Version: {master_version}")
    print(f"  Model      : {model}")
    print(f"  Entries    : {total}")
    if start_date:
        print(f"  Start date : {start_date}")
    if limit:
        print(f"  Limit      : {limit}")
    print(SEP + "\n")

    if total == 0:
        print("ERROR: No seed entries found after filtering. Aborting.")
        sys.exit(1)

    # ── Build batch requests ───────────────────────────────────────────────
    requests_list = []
    for date_key in all_dates:
        seed_entry = seed[date_key]
        cita       = seed_entry["versiculo"]["cita"]
        topic      = seed_entry.get("topic")

        requests_list.append({
            "custom_id": _safe_custom_id(date_key),
            "params": {
                "model": model,
                "max_tokens": MAX_TOKENS,
                "messages": [
                    {"role": "user", "content": build_prompt(cita, master_lang, topic)}
                ],
            },
        })

    # ── Submit to Anthropic ────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set. Add it to your .env file.")

    client = anthropic.Anthropic(api_key=api_key)

    print(f"INFO: Submitting {total} requests to Anthropic Message Batches API...")
    batch = client.messages.batches.create(requests=requests_list)
    batch_id = batch.id
    print(f"INFO: ✅ Batch submitted — ID: {batch_id}")
    print(f"INFO: Status: {batch.processing_status}")
    print(f"INFO: Expires at: {batch.expires_at}")

    # ── Save state file ────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    state_filename = f"batch_state_{master_lang}_{master_version}_{ts}.json"
    state_path     = os.path.join(_SCRIPT_DIR, state_filename)

    state = {
        "batch_id":       batch_id,
        "seed_path":      seed_path,
        "master_lang":    master_lang,
        "master_version": master_version,
        "output_dir":     output_dir,
        "model":          model,
        "dates":          all_dates,    # ordered list for reference
        "total":          total,
        "submitted_at":   datetime.now().isoformat(),
        "expires_at":     str(batch.expires_at),
        "processing_status": batch.processing_status,
    }

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print(f"\nState file → {state_path}")
    print(f"\nNext step:")
    print(f"  python batch_claude_collect.py --state {state_path}")
    print(f"\n  (or just run it without --state to auto-find the latest state file)")
    print(SEP + "\n")

    return state_path


# =============================================================================
# INTERACTIVE (Tkinter)
# =============================================================================

def _interactive():
    root = Tk()
    root.withdraw()

    messagebox.showinfo("Batch Submit 1/4", "Select the seed JSON file.")
    seed_path = filedialog.askopenfilename(
        title="Select seed JSON",
        filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
    )
    if not seed_path:
        root.destroy()
        sys.exit(0)

    # Pre-read seed to show first date
    with open(seed_path, encoding="utf-8") as f:
        _seed_preview = json.load(f)
    _first_date = sorted(_seed_preview.keys())[0] if _seed_preview else "2025-01-01"

    master_lang = simpledialog.askstring("Language", "Language code (e.g. hi, ar, de):", initialvalue="hi")
    if not master_lang:
        root.destroy(); sys.exit(0)

    master_version = simpledialog.askstring("Version", "Version code (e.g. OV, NAV, LU17):", initialvalue="OV")
    if not master_version:
        root.destroy(); sys.exit(0)

    messagebox.showinfo("Batch Submit 3/4", "Select the OUTPUT folder.")
    output_dir = filedialog.askdirectory(title="Select output folder")
    if not output_dir:
        root.destroy(); sys.exit(0)

    model_raw = simpledialog.askstring(
        "Model",
        "Claude model to use:",
        initialvalue=BATCH_MODEL_DEFAULT,
    )
    model = model_raw.strip() if model_raw and model_raw.strip() else BATCH_MODEL_DEFAULT

    start_date_raw = simpledialog.askstring(
        "Start date (optional)",
        "Start date YYYY-MM-DD — blank for full seed:",
        initialvalue=_first_date,
    )
    start_date = start_date_raw.strip() if start_date_raw and start_date_raw.strip() else None

    limit_raw = simpledialog.askstring(
        "Limit (optional)",
        "Max entries to submit — blank for all:",
        initialvalue="",
    )
    limit = int(limit_raw.strip()) if limit_raw and limit_raw.strip().isdigit() else None

    root.destroy()

    submit_batch(
        seed_path      = seed_path,
        master_lang    = master_lang.strip(),
        master_version = master_version.strip(),
        output_dir     = output_dir,
        model          = model,
        start_date     = start_date,
        limit          = limit,
    )


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Submit seed entries to Anthropic Message Batches API (Step 1/2)."
    )
    parser.add_argument("--seed",       type=str, help="Path to seed JSON file")
    parser.add_argument("--lang",       type=str, help="Language code (e.g. hi, ar)")
    parser.add_argument("--version",    type=str, help="Bible version code (e.g. OV, NAV)")
    parser.add_argument("--output",     type=str, help="Output directory for results")
    parser.add_argument("--model",      type=str, default=BATCH_MODEL_DEFAULT,
                        help=f"Claude model (default: {BATCH_MODEL_DEFAULT})")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Only submit entries from this date onwards (YYYY-MM-DD)")
    parser.add_argument("--limit",      type=int, default=None,
                        help="Maximum number of entries to submit")
    args = parser.parse_args()

    # If required CLI args are missing → fall back to interactive
    if not all([args.seed, args.lang, args.version, args.output]):
        if _HAS_TKINTER:
            _interactive()
        else:
            parser.print_help()
            print("\nERROR: --seed, --lang, --version, --output are required in non-interactive mode.")
            sys.exit(1)
        return

    submit_batch(
        seed_path      = args.seed,
        master_lang    = args.lang,
        master_version = args.version,
        output_dir     = args.output,
        model          = args.model,
        start_date     = args.start_date,
        limit          = args.limit,
    )


if __name__ == "__main__":
    main()

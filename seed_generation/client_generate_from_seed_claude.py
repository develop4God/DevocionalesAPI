"""
client_generate_from_seed_claude.py — Claude-direct devotional generator.

Drop-in replacement for client_generate_from_seed.py that bypasses the
FastAPI / Gemini server entirely.  All devotional logic (DevotionalBuilder,
checkpoint, output structure) is IDENTICAL to the original client.

Claude generates: reflexion + oracion only.
Python owns    : ID, date, language, version, versiculo, para_meditar, tags.

Usage (CLI):
  python client_generate_from_seed_claude.py \\
         --seed  seeds/2025/seed_ar_NAV_2025_<ts>.json \\
         --lang  ar \\
         --version NAV \\
         --output 2025 \\
         [--start-date 2025-08-01] \\
         [--limit 5]

Usage (interactive — Tkinter):
  python client_generate_from_seed_claude.py

Requires:
  ANTHROPIC_API_KEY  environment variable (or .env file via python-dotenv)
"""

import argparse
import json
import os
import re
import signal
import sys
import time
from datetime import datetime

try:
    import anthropic
except ImportError:
    raise ImportError(
        "anthropic package not installed.  Run: pip install anthropic"
    )

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   # dotenv optional

try:
    from tkinter import Tk, filedialog, messagebox, simpledialog
    _HAS_TKINTER = True
except ImportError:
    _HAS_TKINTER = False

# =============================================================================
# CONFIG
# =============================================================================

CLAUDE_MODEL        = "claude-opus-4-5"   # change to claude-sonnet-4-5 for faster/cheaper
MAX_TOKENS          = 4096
MAX_RETRIES         = 3           # full regeneration attempts per date
DELAY_BETWEEN       = 3           # seconds between API calls
CHECKPOINT_INTERVAL = 1           # save checkpoint after every N successes

_SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_FILE = os.path.join(_SCRIPT_DIR, "generate_seed_claude_checkpoint.json")


# =============================================================================
# DEVOTIONAL BUILDER  (identical to client_generate_from_seed.py)
# =============================================================================

class DevotionalValidationError(ValueError):
    pass


class DevotionalBuilder:

    def __init__(self, date_key, seed_entry, master_lang, master_version):
        self._date    = date_key
        self._seed    = seed_entry
        self._lang    = master_lang
        self._version = master_version
        self._reflexion = ""
        self._oracion   = ""

    def merge(self, reflexion, oracion):
        self._reflexion = reflexion.strip()
        self._oracion   = oracion.strip()
        return self

    def _build_versiculo(self):
        cita  = self._seed["versiculo"]["cita"]
        texto = self._seed["versiculo"]["texto"]
        return cita + " " + self._version + ': "' + texto + '"'

    def _build_id(self):
        cita         = self._seed["versiculo"]["cita"]
        id_part      = re.sub(r"\s+", "", cita).replace(":", "")
        date_compact = self._date.replace("-", "")
        return id_part + self._version + date_compact

    def _extract_tags(self) -> list:
        tags = self._seed.get("tags", [])
        if isinstance(tags, list) and tags:
            return tags
        return ["devotional"]

    def validate(self):
        errors = []
        if not self._reflexion:                           errors.append("reflexion empty")
        if not self._oracion:                             errors.append("oracion empty")
        if not self._seed.get("versiculo", {}).get("cita"):  errors.append("cita missing")
        if not self._seed.get("versiculo", {}).get("texto"): errors.append("texto missing")
        if not self._seed.get("para_meditar"):            errors.append("para_meditar empty")
        if errors:
            raise DevotionalValidationError("[" + self._date + "] " + "; ".join(errors))

    def build(self):
        self.validate()
        return {
            "id":           self._build_id(),
            "date":         self._date,
            "language":     self._lang,
            "version":      self._version,
            "versiculo":    self._build_versiculo(),
            "reflexion":    self._reflexion,
            "para_meditar": self._seed["para_meditar"],
            "oracion":      self._oracion,
            "tags":         self._extract_tags(),
        }


# =============================================================================
# CHECKPOINT
# =============================================================================

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, encoding="utf-8") as f:
                data = json.load(f)
            print("INFO: Checkpoint found — " + str(data["completed_count"]) + " dates done")
            return data
        except Exception as e:
            print("WARNING: Could not load checkpoint: " + str(e))
    return None


def save_checkpoint(completed, count, seed_path, lang, version, output_dir):
    data = {
        "completed":       completed,
        "completed_count": count,
        "seed_path":       seed_path,
        "master_lang":     lang,
        "master_version":  version,
        "output_dir":      output_dir,
        "timestamp":       datetime.now().isoformat(),
    }
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("  checkpoint saved — " + str(count) + " completed")


def delete_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        try:
            os.remove(CHECKPOINT_FILE)
        except Exception:
            pass


# =============================================================================
# OUTPUT
# =============================================================================

def save_output(completed, lang, version, output_dir):
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = "raw_" + lang + "_" + version + "_claude_" + ts + ".json"
    path     = os.path.join(output_dir, filename)
    nested   = {lang: {date: [devo] for date, devo in completed.items()}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"data": nested}, f, ensure_ascii=False, indent=2)
    return path


# =============================================================================
# CLAUDE GENERATION  (replaces the HTTP call to API_Server_Seed.py)
# =============================================================================

def _build_prompt(verse_cita: str, lang: str, topic: str | None = None) -> str:
    topic_line = f"\n- Suggested theme: {topic}." if topic else ""
    return (
        f"You are a devoted biblical devotional writer. "
        f"Write a devotional in {lang.upper()} based on the key verse: \"{verse_cita}\".\n\n"
        f"Return ONLY a valid JSON object with these exact keys:\n\n"
        f"- `reflexion`: Deep contextualized reflection on the verse "
        f"(minimum 900 characters, approximately 300 words, in {lang}). "
        f"Each paragraph must develop a distinct aspect of the verse.\n"
        f"  Do NOT repeat any word consecutively, even when separated by punctuation marks.\n"
        f"  Do NOT repeat the same sentence, phrase, or idea in different words.\n\n"
        f"- `oracion`: Prayer on the devotional theme (minimum 150 words, 100% in {lang}). "
        f"MUST end with 'in the name of Jesus, amen' correctly translated to {lang}. "
        f"End with exactly one Amen — never write Amen twice.\n"
        f"  Do NOT repeat any word consecutively.\n"
        f"  Do NOT repeat the same sentence, phrase, or idea in different words.\n\n"
        f"RULES:\n"
        f"- ALL text MUST be 100% in {lang} — no language mixing.\n"
        f"- Do NOT include transliterations, romanizations, or text in parentheses.\n"
        f"- Do NOT repeat any word consecutively.\n"
        f"- Every sentence must introduce new content or a new perspective.{topic_line}\n\n"
        f"Return ONLY the JSON object — no markdown, no preamble, no explanation."
    )


def _parse_claude_response(raw: str) -> tuple[str, str]:
    """Extract reflexion and oracion from Claude's JSON response."""
    raw = raw.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$",          "", raw)
    raw = raw.strip()

    # Robustly extract the first JSON object (handles preamble from verbose models)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in Claude response")

    data = json.loads(match.group())
    reflexion = data.get("reflexion", "").strip()
    oracion   = data.get("oracion",   "").strip()

    if not reflexion or not oracion:
        raise ValueError("Claude returned empty reflexion or oracion")

    return reflexion, oracion


def generate_reflexion_oracion(
    client:     "anthropic.Anthropic",
    verse_cita: str,
    lang:       str,
    topic:      str | None = None,
) -> tuple[str, str]:
    """
    Call Claude directly to get reflexion + oracion for one devotional.
    Returns (reflexion, oracion).  Raises ValueError on permanent failure.
    """
    prompt = _build_prompt(verse_cita, lang, topic)

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
            return _parse_claude_response(raw)

        except anthropic.RateLimitError as e:
            wait = 60
            print(f"  Rate limit (attempt {attempt}/{MAX_RETRIES}) — sleeping {wait}s...")
            time.sleep(wait)
            last_err = e

        except anthropic.APIStatusError as e:
            wait = 15 * attempt
            print(f"  API error {e.status_code} (attempt {attempt}/{MAX_RETRIES}) — sleeping {wait}s: {e.message}")
            time.sleep(wait)
            last_err = e

        except (json.JSONDecodeError, ValueError) as e:
            print(f"  Parse error (attempt {attempt}/{MAX_RETRIES}): {e}")
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(5)

    raise ValueError(f"Claude generation failed after {MAX_RETRIES} attempts: {last_err}")


# =============================================================================
# MAIN GENERATION LOOP
# =============================================================================

def generate_from_seed(
    client:         "anthropic.Anthropic",
    seed_path:      str,
    master_lang:    str,
    master_version: str,
    output_dir:     str,
    start_date:     str | None = None,
    limit:          int | None = None,
) -> None:
    SEP = "=" * 60
    print("\n" + SEP)
    print("SEED-DRIVEN GENERATOR — Claude direct")
    print(SEP)
    print("  Seed    : " + seed_path)
    print("  Lang    : " + master_lang + "  Version: " + master_version)
    print("  Model   : " + CLAUDE_MODEL)
    print("  Output  : " + output_dir)
    print(SEP + "\n")

    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    all_dates = sorted(seed.keys())
    total     = len(all_dates)
    print("INFO: " + str(total) + " seed entries\n")

    # ── Partial generation mode ───────────────────────────────────────────────
    if start_date:
        all_dates = [d for d in all_dates if d >= start_date]
        if limit:
            all_dates = all_dates[:limit]
        total = len(all_dates)
        print("INFO: Partial mode — " + str(total) + " dates | "
              + all_dates[0] + " → " + all_dates[-1])
        completed   = {}
        start_index = 0

    else:
    # ── Full / checkpoint mode ────────────────────────────────────────────────
        completed   = {}
        start_index = 0
        checkpoint  = load_checkpoint()

        if checkpoint and checkpoint.get("seed_path") == seed_path:
            ans = input(
                "\nCheckpoint — " + str(checkpoint["completed_count"]) +
                " done. Resume? (y/n): "
            ).strip().lower()
            if ans == "y":
                completed   = checkpoint["completed"]
                start_index = checkpoint["completed_count"]
                print("INFO: Resuming from " + str(start_index + 1) + "/" + str(total) + "\n")

    interrupted = False

    def _sig(sig, frame):
        nonlocal interrupted
        print("\n\nCtrl+C — saving checkpoint...")
        interrupted = True

    signal.signal(signal.SIGINT, _sig)

    success_count = start_index
    error_count   = 0
    error_dates   = []

    print("INFO: Checkpoint every " + str(CHECKPOINT_INTERVAL) + " successes")
    print("-" * 60)

    for i in range(start_index, total):
        if interrupted:
            break

        date_key   = all_dates[i]
        seed_entry = seed[date_key]
        cita       = seed_entry["versiculo"]["cita"]

        print("\n[" + str(i + 1) + "/" + str(total) + "] " + date_key + " — " + cita)

        try:
            reflexion, oracion = generate_reflexion_oracion(client, cita, master_lang)

            builder    = DevotionalBuilder(date_key, seed_entry, master_lang, master_version)
            devotional = builder.merge(reflexion, oracion).build()
            completed[date_key] = devotional
            success_count += 1
            print("  OK — " + str(len(reflexion)) + " chars | tags: " + str(devotional["tags"]))

            if success_count % CHECKPOINT_INTERVAL == 0:
                save_checkpoint(
                    completed, success_count, seed_path,
                    master_lang, master_version, output_dir,
                )

        except DevotionalValidationError as e:
            print("  Validation error: " + str(e))
            error_count += 1
            error_dates.append({"date": date_key, "cita": cita, "reason": str(e)})

        except Exception as e:
            msg = type(e).__name__ + ": " + str(e)
            print("  Error: " + msg)
            error_count += 1
            error_dates.append({"date": date_key, "cita": cita, "reason": msg})

        time.sleep(DELAY_BETWEEN)

    # ── Interrupted → save checkpoint ─────────────────────────────────────────
    if interrupted and completed:
        save_checkpoint(completed, success_count, seed_path, master_lang, master_version, output_dir)
        print("\nProgress saved. Run again to resume.")
        sys.exit(0)

    print("\n" + "-" * 60)

    if completed:
        out = save_output(completed, master_lang, master_version, output_dir)
        print("\nOutput  -> " + out)
        delete_checkpoint()
        if error_dates:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            ep = os.path.join(
                output_dir,
                "generation_errors_" + master_lang + "_" + master_version + "_claude_" + ts + ".json",
            )
            with open(ep, "w", encoding="utf-8") as f:
                json.dump(error_dates, f, ensure_ascii=False, indent=2)
            print("Errors  -> " + ep)
    else:
        print("No devotionals generated.")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("  Seed entries  : " + str(total))
    print("  Generated OK  : " + str(success_count))
    print("  Skipped       : " + str(error_count))
    print("=" * 60 + "\n")


# =============================================================================
# ENTRY POINT
# =============================================================================

def _parse_cli() -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(
        description="Claude-direct seed generator — no Gemini / FastAPI server needed",
        add_help=True,
    )
    parser.add_argument("--seed",       type=str, help="Path to seed JSON file")
    parser.add_argument("--lang",       type=str, help="Language code (e.g. ar, hi, de)")
    parser.add_argument("--version",    type=str, help="Bible version code (e.g. NAV, HERV)")
    parser.add_argument("--output",     type=str, help="Output folder path")
    parser.add_argument("--start-date", type=str, default=None, help="Start date YYYY-MM-DD (optional)")
    parser.add_argument("--limit",      type=int, default=None, help="Max dates to generate (optional)")

    args, _ = parser.parse_known_args()
    if args.seed and args.lang and args.version and args.output:
        return args
    return None


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.")
        print("       Export it with: export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key, max_retries=0)

    cli = _parse_cli()
    if cli:
        seed_path      = cli.seed
        master_lang    = cli.lang.lower()
        master_version = cli.version.upper()
        output_dir     = cli.output
        start_date     = cli.start_date
        limit          = cli.limit
    else:
        if not _HAS_TKINTER:
            print("ERROR: tkinter not available. Use CLI args: --seed --lang --version --output")
            sys.exit(1)

        root = Tk()
        root.withdraw()

        checkpoint = load_checkpoint()
        if checkpoint and "output_dir" in checkpoint:
            ans = input(
                "\nCheckpoint found — " + str(checkpoint["completed_count"]) +
                " done (" + checkpoint["master_lang"] + " " + checkpoint["master_version"] +
                "). Resume? (y/n): "
            ).strip().lower()
            if ans == "y":
                root.destroy()
                generate_from_seed(
                    client,
                    checkpoint["seed_path"],
                    checkpoint["master_lang"],
                    checkpoint["master_version"],
                    checkpoint["output_dir"],
                )
                return

        messagebox.showinfo("Claude Seed Generator 1/4", "Select the seed JSON file.")
        seed_path = filedialog.askopenfilename(
            title="Select seed JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not seed_path:
            sys.exit(0)

        with open(seed_path, encoding="utf-8") as f:
            _seed_preview = json.load(f)
        _first_seed_date = sorted(_seed_preview.keys())[0]

        master_lang = simpledialog.askstring("Language", "Language code (e.g. ar, hi):", initialvalue="ar")
        if not master_lang:
            sys.exit(0)

        master_version = simpledialog.askstring("Version", "Version code (e.g. NAV, HERV):", initialvalue="NAV")
        if not master_version:
            sys.exit(0)

        messagebox.showinfo("Claude Seed Generator 4/4", "Select the output folder.")
        output_dir = filedialog.askdirectory(title="Select output folder")
        if not output_dir:
            sys.exit(0)

        start_date_raw = simpledialog.askstring(
            "Start date (optional)",
            "Start date YYYY-MM-DD — leave blank for full/checkpoint run:",
            initialvalue=_first_seed_date,
        )
        start_date = start_date_raw.strip() if start_date_raw and start_date_raw.strip() else None

        limit_raw = simpledialog.askstring(
            "Limit (optional)",
            "Max devotionals to generate — leave blank for all:",
            initialvalue="",
        )
        limit = int(limit_raw.strip()) if limit_raw and limit_raw.strip().isdigit() else None

        root.destroy()

    os.makedirs(output_dir, exist_ok=True)
    generate_from_seed(
        client, seed_path, master_lang, master_version, output_dir, start_date, limit
    )


if __name__ == "__main__":
    main()

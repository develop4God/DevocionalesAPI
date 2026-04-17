"""
batch_claude_collect.py
───────────────────────
STEP 2 of 2 — Poll Anthropic batch, collect results, build devotionals.

Reads the state file produced by batch_claude_submit.py, polls the batch
until processing has ended, then:
  1. Downloads all results (JSONL stream from Anthropic)
  2. Parses reflexion + oracion from each succeeded response
  3. Builds full devotional records via DevotionalBuilder
  4. Runs Phase 1 local validation (length, prayer ending, dup words, truncation)
     — issues are logged but never block saving
  5. Saves final JSON  →  raw_<lang>_<version>_<ts>.json
     Saves errors JSON →  batch_errors_<lang>_<version>_<ts>.json (if any)

CLI usage:
  # Point at a specific state file:
  python batch_claude_collect.py --state batch_state_ar_NAV_20250415_120000.json

  # Auto-find the most recent state file in this directory:
  python batch_claude_collect.py

  # Custom poll interval (seconds, default 60):
  python batch_claude_collect.py --poll-interval 30

Requires:
  ANTHROPIC_API_KEY in environment or .env file
  pip install anthropic python-dotenv
"""

import argparse
import json
import os

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Import shared utilities
from pipeline_shared import repair_json, _extract_first_balanced_object, build_prompt, _load_prayer_endings, _normalize_word, _check_prayer_ending, LITURGICAL_WHITELIST

try:
    import anthropic
except ImportError:
    raise ImportError("anthropic package not installed.  Run: pip install anthropic")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# PHASE 1 — LOCAL VALIDATION  (self-contained, no Gemini dependency)
# =============================================================================

REFLEXION_MIN_CHARS = 800
ORACION_MIN_CHARS   = 150

LITURGICAL_WHITELIST: frozenset = frozenset({
    global _PRAYER_ENDINGS
    return unicodedata.normalize("NFD", word).encode("ascii", "ignore").decode().lower()
    endings = _load_prayer_endings().get(lang, ["Amen"])
        w1 = words[i].strip(strip_chars).lower()
    dup = _find_consecutive_dup(r)
    def _extract_tags(self) -> list:
        tags = self._seed.get("tags", [])
        if isinstance(tags, list) and tags:
            return tags
        return ["devotional"]

    def validate(self):
        errors = []
        if not self._reflexion:                                errors.append("reflexion empty")
        if not self._oracion:                                  errors.append("oracion empty")
        if not self._seed.get("versiculo", {}).get("cita"):   errors.append("cita missing")
        if not self._seed.get("versiculo", {}).get("texto"):  errors.append("texto missing")
        if not self._seed.get("para_meditar"):                 errors.append("para_meditar empty")
        if errors:
            raise DevotionalValidationError("[" + self._date + "] " + "; ".join(errors))

    def build(self) -> dict:
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
# OUTPUT
# =============================================================================

def _safe_custom_id(date_key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", date_key)[:64]


# =============================================================================
# ROBUST JSON REPAIR  (handles control chars, extra data, trailing commas, etc.)
# =============================================================================

def _extract_first_balanced_object(text: str) -> Optional[str]:
    """Extract the first balanced {} object from text."""
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, c in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def repair_json(raw_text: str) -> Optional[dict]:
    """
    Try multiple strategies to extract and parse a JSON object from Claude output.
    Returns the parsed dict or None if all strategies fail.
    """
    # Strip markdown code fences
    text = re.sub(r'```(?:json)?\s*', '', raw_text)
    text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE).strip()

    # Strategy 1: Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: First balanced JSON object, parse as-is
    candidate = _extract_first_balanced_object(text)
    if candidate:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # Strategy 3: Remove trailing commas before } or ]
        fixed = re.sub(r',(\s*[}\]])', r'\1', candidate)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # Strategy 4: Remove unescaped control characters
        fixed2 = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', fixed)
        try:
            return json.loads(fixed2)
        except json.JSONDecodeError:
            pass

        # Strategy 5: Fix literal newlines inside string values
        fixed3 = re.sub(r',(\s*[}\]])', r'\1', candidate)
        fixed3 = re.sub(r'(?<=: ")([^"]*?)\n([^"]*?)(?=")', r'\1\\n\2', fixed3)
        try:
            return json.loads(fixed3)
        except json.JSONDecodeError:
            pass

    # Strategy 6: Regex extraction of reflexion + oracion fields
    reflexion_m = re.search(
        r'"reflexion"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL
    )
    oracion_m = re.search(
        r'"oracion"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL
    )
    if reflexion_m and oracion_m:
        try:
            return {
                "reflexion": reflexion_m.group(1).encode('raw_unicode_escape').decode('unicode_escape'),
                "oracion":   oracion_m.group(1).encode('raw_unicode_escape').decode('unicode_escape'),
            }
        except Exception:
            return {
                "reflexion": reflexion_m.group(1),
                "oracion":   oracion_m.group(1),
            }

    return None


def save_output(completed: dict, lang: str, version: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"raw_{lang}_{version}_batch_{ts}.json"
    path     = os.path.join(output_dir, filename)
    nested   = {lang: {date: [devo] for date, devo in completed.items()}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"data": nested}, f, ensure_ascii=False, indent=2)
    return path


# =============================================================================
# STATE FILE HELPERS
# =============================================================================

def find_latest_state_file() -> Optional[str]:
    """Returns path to the most recent batch_state_*.json in _SCRIPT_DIR."""
    candidates = sorted(
        Path(_SCRIPT_DIR).glob("batch_state_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def load_state(state_path: str) -> dict:
    with open(state_path, encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# POLLING
# =============================================================================

def poll_until_done(client: anthropic.Anthropic, batch_id: str, poll_interval: int = 60) -> None:
    """Blocks until batch processing_status == 'ended'."""
    print(f"INFO: Polling batch {batch_id} every {poll_interval}s until done...")
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        status  = batch.processing_status
        counts  = batch.request_counts

        print(
            f"  [{datetime.now().strftime('%H:%M:%S')}] status={status} | "
            f"processing={counts.processing} "
            f"succeeded={counts.succeeded} "
            f"errored={counts.errored} "
            f"canceled={counts.canceled} "
            f"expired={counts.expired}"
        )

        if status == "ended":
            print(f"INFO: ✅ Batch ended — {counts.succeeded} succeeded, "
                  f"{counts.errored} errored, {counts.expired} expired")
            return

        if status == "canceling":
            print(f"WARNING: Batch is canceling — results may be partial")

        time.sleep(poll_interval)


# =============================================================================
# COLLECT
# =============================================================================

def collect(state_path: str, poll_interval: int = 60) -> None:
    SEP = "=" * 60
    state = load_state(state_path)

    batch_id       = state["batch_id"]
    seed_path      = state["seed_path"]
    master_lang    = state["master_lang"]
    master_version = state["master_version"]
    output_dir     = state["output_dir"]

    print("\n" + SEP)
    print("ANTHROPIC BATCH COLLECT")
    print(SEP)
    print(f"  Batch ID   : {batch_id}")
    print(f"  Lang       : {master_lang}  Version: {master_version}")
    print(f"  Seed       : {seed_path}")
    print(f"  Output dir : {output_dir}")
    print(f"  Submitted  : {state.get('submitted_at', 'unknown')}")
    print(f"  Expires at : {state.get('expires_at', 'unknown')}")
    print(SEP + "\n")

    # ── Load seed (needed to build full devotional records) ────────────────
    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    # Build a lookup: custom_id → (date_key, seed_entry)
    # custom_id was derived from date_key in submit script
    custom_id_map: dict[str, tuple[str, dict]] = {}
    for date_key in state.get("dates", []):
        cid = _safe_custom_id(date_key)
        custom_id_map[cid] = (date_key, seed.get(date_key, {}))

    # ── Anthropic client ───────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set. Add it to your .env file.")
    client = anthropic.Anthropic(api_key=api_key)

    # ── Poll ───────────────────────────────────────────────────────────────
    poll_until_done(client, batch_id, poll_interval=poll_interval)

    # ── Collect results ────────────────────────────────────────────────────
    print(f"\nINFO: Downloading and processing results...")

    completed: dict[str, dict] = {}
    error_records: list[dict]  = []
    val_warnings: list[dict]   = []

    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id
        date_key, seed_entry = custom_id_map.get(custom_id, (custom_id, {}))

        # ── Non-succeeded results ──────────────────────────────────────────
        if result.result.type != "succeeded":
            reason = str(result.result)
            print(f"  [{result.result.type.upper()}] {date_key} — {reason[:80]}")
            error_records.append({
                "date":      date_key,
                "custom_id": custom_id,
                "reason":    f"batch_result_{result.result.type}",
                "detail":    reason,
            })
            continue

        # ── Parse JSON response (with robust multi-strategy repair) ──────────
        raw_text = result.result.message.content[0].text.strip()
        data = repair_json(raw_text)

        if data is None:
            print(f"  [PARSE_ERROR] {date_key} — all JSON repair strategies failed")
            error_records.append({
                "date":   date_key,
                "reason": "parse_error",
                "detail": raw_text[:200],
            })
            continue

        reflexion = data.get("reflexion", "").strip()
        oracion   = data.get("oracion",   "").strip()

        if not reflexion or not oracion:
            print(f"  [EMPTY] {date_key} — reflexion or oracion is empty")
            error_records.append({"date": date_key, "reason": "empty_fields",
                                  "detail": f"reflexion={bool(reflexion)} oracion={bool(oracion)}"})
            continue

        # ── Script validation ──────────────────────────────────────────────
        r_valid, r_ratio = validate_script(reflexion, master_lang)
        o_valid, o_ratio = validate_script(oracion,   master_lang)
        if not r_valid or not o_valid:
            print(f"  [SCRIPT_WARN] {date_key} — reflexion:{r_ratio:.0%} oracion:{o_ratio:.0%}")
            # Log as warning but still save best-effort content
            val_warnings.append({
                "date":   date_key,
                "issue":  "script_validation",
                "detail": f"reflexion:{r_ratio:.0%} oracion:{o_ratio:.0%}",
            })

        # ── Phase 1 validation ─────────────────────────────────────────────
        p1_passed, p1_issues = run_phase1(reflexion, oracion, master_lang)
        if not p1_passed:
            print(f"  [VAL_WARN] {date_key} — Phase 1: {p1_issues}")
            val_warnings.append({"date": date_key, "issue": "phase1", "detail": p1_issues})

        # ── Build devotional record ────────────────────────────────────────
        if not seed_entry:
            print(f"  [NO_SEED] {date_key} — seed entry not found (custom_id={custom_id})")
            error_records.append({"date": date_key, "reason": "seed_entry_missing"})
            continue

        try:
            devotional = (
                DevotionalBuilder(date_key, seed_entry, master_lang, master_version)
                .merge(reflexion, oracion)
                .build()
            )
            completed[date_key] = devotional
            print(
                f"  OK  {date_key} — reflexion:{len(reflexion)} oracion:{len(oracion)}"
                + (f" | ⚠ {len(p1_issues)} issue(s)" if p1_issues else "")
            )

        except DevotionalValidationError as e:
            print(f"  [BUILD_ERROR] {e}")
            error_records.append({"date": date_key, "reason": "build_error", "detail": str(e)})

    # ── Save outputs ───────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"RESULTS")
    print(f"  Total submitted  : {state.get('total', len(custom_id_map))}")
    print(f"  Built OK         : {len(completed)}")
    print(f"  Errors           : {len(error_records)}")
    print(f"  Val warnings     : {len(val_warnings)}")
    print(SEP)

    if completed:
        out_path = save_output(completed, master_lang, master_version, output_dir)
        print(f"\nOutput  → {out_path}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if error_records:
        err_path = os.path.join(
            output_dir,
            f"batch_errors_{master_lang}_{master_version}_{ts}.json"
        )
        os.makedirs(output_dir, exist_ok=True)
        with open(err_path, "w", encoding="utf-8") as f:
            json.dump(error_records, f, ensure_ascii=False, indent=2)
        print(f"Errors  → {err_path}")

    if val_warnings:
        warn_path = os.path.join(
            output_dir,
            f"batch_val_warnings_{master_lang}_{master_version}_{ts}.json"
        )
        os.makedirs(output_dir, exist_ok=True)
        with open(warn_path, "w", encoding="utf-8") as f:
            json.dump(val_warnings, f, ensure_ascii=False, indent=2)
        print(f"Warnings → {warn_path}")

    if not completed and not error_records:
        print("\nWARNING: No results were retrieved. The batch may still be processing.")

    print(SEP + "\n")


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Collect Anthropic batch results and build devotionals (Step 2/2)."
    )
    parser.add_argument(
        "--state",
        type=str,
        default=None,
        help="Path to batch state JSON (default: auto-find latest batch_state_*.json)",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=60,
        help="Seconds between status checks while polling (default: 60)",
    )
    args = parser.parse_args()

    state_path = args.state
    if not state_path:
        state_path = find_latest_state_file()
        if not state_path:
            print("ERROR: No batch state file found. "
                  "Run batch_claude_submit.py first, or pass --state <file>.")
            sys.exit(1)
        print(f"INFO: Using latest state file: {state_path}")

    if not os.path.exists(state_path):
        print(f"ERROR: State file not found: {state_path}")
        sys.exit(1)

    collect(state_path, poll_interval=args.poll_interval)


if __name__ == "__main__":
    main()

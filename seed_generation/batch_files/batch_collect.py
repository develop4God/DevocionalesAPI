"""
batch_collect.py
────────────────
STEP 2 — Collect results from any provider and build devotional records.

Replaces batch_claude_collect.py with a provider-agnostic version.
Reads the state file from batch_submit.py, fires the adapter's collect(),
parses JSON, validates Phase 1, builds devotional records, saves output.

CLI:
  python batch_collect.py --state batch_state_tl_ADB_gemini_20260421_120000.json
  python batch_collect.py          # auto-finds latest batch_state_*.json

Outputs (in output_dir):
  raw_<lang>_<version>_<provider>_<ts>.json       all built devotionals
  batch_errors_<lang>_<version>_<provider>_<ts>.json   failed dates
  batch_val_warnings_<lang>_<version>_<provider>_<ts>.json  Phase1 warnings
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pipeline_shared import repair_json, build_prompt
from pipeline_shared import _check_prayer_ending, _load_prayer_endings
from provider_adapter import load_adapter, BatchRequest, RawResult

_SCRIPT_DIR = Path(__file__).parent

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 local validation  (identical logic to original collect)
# ─────────────────────────────────────────────────────────────────────────────

REFLEXION_MIN_CHARS = 800
ORACION_MIN_CHARS   = 150

LITURGICAL_WHITELIST = frozenset({
    "heilig", "holy", "kadosh", "halleluja", "hosanna",
    "amen", "amén", "āmen", "aleluya", "panginoon",
    "banal", "santo", "sagrado",
})

def _normalize(w: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFD", w).encode("ascii", "ignore").decode().lower()

def _find_dup_words(text: str) -> Optional[str]:
    strip_chars = ".,;:!?\"'()[]{}—–-\u2019\u2018\u201c\u201d"
    words = text.split()
    for i in range(1, len(words)):
        w1 = words[i - 1].strip(strip_chars).lower()
        w2 = words[i].strip(strip_chars).lower()
        if w1 and w2 and w1 == w2 and _normalize(w1) not in LITURGICAL_WHITELIST:
            return f"{words[i-1]} {words[i]}"
    return None

def run_phase1(reflexion: str, oracion: str, lang: str) -> tuple[bool, list[str]]:
    issues = []
    r, o = reflexion.strip(), oracion.strip()

    if len(r) < REFLEXION_MIN_CHARS:
        issues.append(f"reflexion too short: {len(r)} < {REFLEXION_MIN_CHARS}")
    if len(o) < ORACION_MIN_CHARS:
        issues.append(f"oracion too short: {len(o)} < {ORACION_MIN_CHARS}")
    if not r.endswith((".", "!", "?", "…", "।")):
        if len(r) > 0 and r[-1].isalnum():
            issues.append("reflexion_truncated: ends mid-word")
    if not _check_prayer_ending(o, lang):
        endings = _load_prayer_endings().get(lang, ["Amen"])
        last = o.split()[-1] if o.split() else ""
        issues.append(f"prayer_ending: last word '{last}' — expected one of {endings}")

    # double amen (last 120 chars)
    tail = o[-120:].lower()
    amen_variants = ["amen", "amén", "amem", "amim", "amin"]
    amen_count = sum(tail.count(v) for v in amen_variants)
    if amen_count >= 2:
        issues.append("double_amen: duplicate Amen artifact detected in closing")

    dup_r = _find_dup_words(r)
    if dup_r:
        issues.append(f"dup_words_reflexion: consecutive duplicate '{dup_r}'")
    dup_o = _find_dup_words(o)
    if dup_o:
        issues.append(f"dup_words_oracion: consecutive duplicate '{dup_o}'")

    return (len(issues) == 0), issues


# ─────────────────────────────────────────────────────────────────────────────
# Devotional builder  (identical to original)
# ─────────────────────────────────────────────────────────────────────────────

def _safe_custom_id(date_key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", date_key)[:64]

def _build_id(date_key: str, seed_entry: dict, lang: str, version: str) -> str:
    cita = seed_entry.get("versiculo", {}).get("cita", "")
    book_ref = re.sub(r"[^A-Za-z0-9]", "", cita)[:10]
    date_slug = date_key.replace("-", "")
    return f"{book_ref}{version}{date_slug}"

def _build_versiculo_str(seed_entry: dict, version: str) -> str:
    v    = seed_entry.get("versiculo", {})
    cita = v.get("cita", "")
    txt  = v.get("texto", "")
    return f"{cita} {version}: \"{txt}\""

def build_devotional(
    date_key: str, seed_entry: dict, lang: str, version: str,
    reflexion: str, oracion: str,
) -> dict:
    required = ["versiculo", "para_meditar"]
    for k in required:
        if not seed_entry.get(k):
            raise ValueError(f"[{date_key}] seed missing required field: {k}")
    if not seed_entry["versiculo"].get("cita"):
        raise ValueError(f"[{date_key}] versiculo.cita is empty")

    tags = seed_entry.get("tags", [])
    if not isinstance(tags, list) or not tags:
        tags = ["devotional"]

    return {
        "id":           _build_id(date_key, seed_entry, lang, version),
        "date":         date_key,
        "language":     lang,
        "version":      version,
        "versiculo":    _build_versiculo_str(seed_entry, version),
        "reflexion":    reflexion,
        "para_meditar": seed_entry["para_meditar"],
        "oracion":      oracion,
        "tags":         tags,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Save helpers
# ─────────────────────────────────────────────────────────────────────────────

def _save_json(data, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_output(completed: dict, lang: str, version: str, provider: str, output_dir: str) -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"raw_{lang}_{version}_{provider}_{ts}.json"
    path     = Path(output_dir) / filename
    nested   = {"data": {lang: {date: [devo] for date, devo in sorted(completed.items())}}}
    _save_json(nested, path)
    return str(path)


# ─────────────────────────────────────────────────────────────────────────────
# State loader
# ─────────────────────────────────────────────────────────────────────────────

def find_latest_state_file() -> Optional[str]:
    files = sorted(_SCRIPT_DIR.glob("batch_state_*.json"), key=os.path.getmtime, reverse=True)
    return str(files[0]) if files else None


def load_state(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Main collect logic
# ─────────────────────────────────────────────────────────────────────────────

def collect(state_path: str, results_file: Optional[str] = None) -> None:
    SEP = "=" * 60
    state = load_state(state_path)

    provider       = state["provider"]
    model_alias    = state.get("model_alias") or None
    job_id         = state["job_id"]
    seed_path      = state["seed_path"]
    master_lang    = state["master_lang"]
    master_version = state["master_version"]
    output_dir     = state["output_dir"]

    print("\n" + SEP)
    print("BATCH COLLECT")
    print(SEP)
    print(f"  Provider   : {provider}")
    print(f"  Model      : {state.get('model_id', '?')}")
    print(f"  Job ID     : {job_id}")
    print(f"  Lang       : {master_lang}  Version: {master_version}")
    print(f"  Seed       : {seed_path}")
    print(f"  Output dir : {output_dir}")
    print(f"  Submitted  : {state.get('submitted_at', 'unknown')}")
    print(SEP + "\n")

    # ── Load seed ──────────────────────────────────────────────────────────
    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    # Rebuild BatchRequest list from state (needed by adapters)
    requests: list[BatchRequest] = []
    for date_key in state.get("dates", []):
        seed_entry = seed.get(date_key, {})
        cita  = seed_entry.get("versiculo", {}).get("cita", "")
        topic = seed_entry.get("topic")
        requests.append(BatchRequest(
            date_key=date_key,
            custom_id=_safe_custom_id(date_key),
            prompt=build_prompt(cita, master_lang, topic),
            model_id=state.get("model_id", ""),
        ))

    # ── Load adapter + collect ─────────────────────────────────────────────
    if model_alias == "default":
        model_alias = None
    adapter = load_adapter(provider, model_alias)

    # For openai_batch_file, job_id is the submitted file path —
    # results come from a separate file the user downloads from Fireworks.
    batch_strategy = state.get("batch_strategy") or adapter.batch_strategy
    if batch_strategy == "openai_batch_file":
        if not results_file:
            print("ERROR: --results <results.jsonl> is required for fireworks_batch provider.")
            print("       Download the results JSONL from Fireworks AI and pass it here.")
            sys.exit(1)
        collect_job_id = results_file
    else:
        collect_job_id = job_id

    print(f"INFO: Collecting {len(requests)} results from {provider}...")
    raw_results: list[RawResult] = adapter.collect(collect_job_id, requests)

    # ── Process results ────────────────────────────────────────────────────
    completed:    dict[str, dict] = {}
    error_records: list[dict]     = []
    val_warnings:  list[dict]     = []

    for result in raw_results:
        date_key = result.date_key

        if not result.succeeded:
            print(f"  [ERROR] {date_key} — {result.error}")
            error_records.append({"date": date_key, "reason": "provider_error",
                                   "detail": result.error})
            continue

        # Parse JSON
        data = repair_json(result.raw_text)
        if data is None:
            print(f"  [PARSE_ERROR] {date_key} — all JSON repair strategies failed")
            error_records.append({"date": date_key, "reason": "parse_error",
                                   "detail": result.raw_text[:200]})
            continue

        reflexion = data.get("reflexion", "").strip()
        oracion   = data.get("oracion",   "").strip()

        if not reflexion or not oracion:
            print(f"  [EMPTY] {date_key}")
            error_records.append({"date": date_key, "reason": "empty_fields",
                                   "detail": f"reflexion={bool(reflexion)} oracion={bool(oracion)}"})
            continue

        # Phase 1 validation
        p1_passed, p1_issues = run_phase1(reflexion, oracion, master_lang)
        if not p1_passed:
            print(f"  [VAL_WARN] {date_key} — {p1_issues}")
            val_warnings.append({"date": date_key, "issue": "phase1", "detail": p1_issues})

        # Build devotional record
        seed_entry = seed.get(date_key, {})
        if not seed_entry:
            print(f"  [NO_SEED] {date_key}")
            error_records.append({"date": date_key, "reason": "seed_entry_missing"})
            continue

        try:
            devo = build_devotional(date_key, seed_entry, master_lang, master_version,
                                    reflexion, oracion)
            completed[date_key] = devo
            warn_tag = f" | ⚠ {len(p1_issues)} issue(s)" if p1_issues else ""
            print(f"  OK  {date_key} — r:{len(reflexion)} o:{len(oracion)}{warn_tag}")
        except Exception as e:
            print(f"  [BUILD_ERROR] {date_key} — {e}")
            error_records.append({"date": date_key, "reason": "build_error", "detail": str(e)})

    # ── Save outputs ───────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("RESULTS")
    print(f"  Total   : {len(requests)}")
    print(f"  Built   : {len(completed)}")
    print(f"  Errors  : {len(error_records)}")
    print(f"  Warnings: {len(val_warnings)}")
    print(SEP)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if completed:
        out_path = save_output(completed, master_lang, master_version, provider, output_dir)
        print(f"\nOutput   → {out_path}")

    if error_records:
        err_path = Path(output_dir) / f"batch_errors_{master_lang}_{master_version}_{provider}_{ts}.json"
        _save_json(error_records, err_path)
        print(f"Errors   → {err_path}")

    if val_warnings:
        warn_path = Path(output_dir) / f"batch_val_warnings_{master_lang}_{master_version}_{provider}_{ts}.json"
        _save_json(val_warnings, warn_path)
        print(f"Warnings → {warn_path}")

    print(SEP + "\n")

    if error_records:
        print(f"⚠️  {len(error_records)} errors — run batch_repair.py next.")
        print(f"  python batch_repair.py --state {state_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Collect provider results and build devotionals (Step 2/2)."
    )
    parser.add_argument("--state", type=str, default=None,
                        help="Path to batch_state_*.json (default: auto-find latest)")
    parser.add_argument("--results", type=str, default=None,
                        help="Path to results JSONL downloaded from Fireworks AI "
                             "(required for --provider fireworks_batch)")
    args = parser.parse_args()

    state_path = args.state
    if not state_path:
        state_path = find_latest_state_file()
        if not state_path:
            print("ERROR: No batch state file found. Run batch_submit.py first.")
            sys.exit(1)
        print(f"INFO: Using latest state file: {state_path}")

    if not os.path.exists(state_path):
        print(f"ERROR: State file not found: {state_path}")
        sys.exit(1)

    collect(state_path, results_file=args.results)


if __name__ == "__main__":
    main()

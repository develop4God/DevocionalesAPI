"""
batch_repair_failed.py
──────────────────────
Re-fetches Anthropic batch results for failed dates, applies robust JSON
repair, and merges them into the existing devotionals file.

For dates that still fail after repair, re-submits them individually to
the Anthropic API using the same model/prompt logic as the original batch.

Usage:
  python batch_repair_failed.py \
    --state  batch_state_tl_ASND_20260415_173212.json \
    --errors 2026/yearly_devotionals/batch_errors_tl_ASND_20260415_182836.json \
    --existing 2026/yearly_devotionals/279-Devocional_year_2026_tl_ASND.json \
    --output   2026/yearly_devotionals/Devocional_year_2026_tl_ASND.json
"""

import argparse
import json

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
    raise ImportError("anthropic package not installed.")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# JSON REPAIR
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
    Try multiple strategies to extract and parse a JSON object.
    except json.JSONDecodeError:
        except json.JSONDecodeError:
        fixed2 = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', fixed)
        fixed3 = re.sub(r'(?<=: ")([^"]*?)\n([^"]*?)(?=")', r'\1\\n\2', candidate)
    return None
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


# =============================================================================
# API FALLBACK — regenerate failed/unfixable entries individually
# =============================================================================

def regenerate_via_api(
    date_key: str,
    seed_entry: dict,
    lang: str,
    version: str,
    client: anthropic.Anthropic,
    model: str,
    max_retries: int = 3,
) -> Optional[dict]:
    """Call Claude directly (non-batch) to regenerate one devotional."""
    cita  = seed_entry["versiculo"]["cita"]
    topic = seed_entry.get("tags", [None])[0]
    prompt = build_prompt(cita, lang, topic)

    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text.strip()
            data = repair_json(raw_text)
            if not data:
                print(f"    [RETRY {attempt}] {date_key} — repair_json returned None")
                time.sleep(2)
                continue

            reflexion = data.get("reflexion", "").strip()
            oracion   = data.get("oracion",   "").strip()
            if not reflexion or not oracion:
                print(f"    [RETRY {attempt}] {date_key} — empty fields after repair")
                time.sleep(2)
                continue

            devotional = build_devotional(date_key, seed_entry, lang, version, reflexion, oracion)
            print(f"  API ✅ {date_key} (attempt {attempt})")
            return devotional

        except Exception as ex:
            print(f"    [RETRY {attempt}] {date_key} — API error: {ex}")
            if attempt < max_retries:
                time.sleep(5 * attempt)

    print(f"  API ❌ {date_key} — gave up after {max_retries} attempts")
    return None


# =============================================================================
# MAIN REPAIR LOGIC
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Re-fetch failed batch dates, repair JSON, merge devotionals."
    )
    parser.add_argument("--state",    required=True, help="batch_state_*.json file")
    parser.add_argument("--errors",   required=True, help="batch_errors_*.json file")
    parser.add_argument("--existing", required=True, help="existing devotionals JSON (279-...)")
    parser.add_argument("--output",   required=True, help="output path for merged file")
    parser.add_argument("--model",    default="claude-sonnet-4-6", help="Model for API fallback")
    args = parser.parse_args()

    SEP = "=" * 60

    # ── Load inputs ────────────────────────────────────────────────────────
    with open(args.state,    encoding="utf-8") as f: state    = json.load(f)
    with open(args.errors,   encoding="utf-8") as f: err_list = json.load(f)
    with open(args.existing, encoding="utf-8") as f: existing = json.load(f)

    batch_id       = state["batch_id"]
    seed_path      = state["seed_path"]
    master_lang    = state["master_lang"]
    master_version = state["master_version"]

    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    # Dates that still need to be generated
    failed_dates = {e["date"] for e in err_list}
    # Dates that have "build_error" - seed issue, not JSON issue
    build_errors = {e["date"] for e in err_list if e.get("reason") == "build_error"}
    repair_dates = failed_dates - build_errors

    print(SEP)
    print("BATCH REPAIR")
    print(f"  Batch ID     : {batch_id}")
    print(f"  Lang         : {master_lang}  Version: {master_version}")
    print(f"  Failed dates : {len(failed_dates)}")
    print(f"  Build errors : {len(build_errors)} (will skip: {sorted(build_errors)})")
    print(f"  Repair dates : {len(repair_dates)}")
    print(SEP + "\n")

    # ── Load existing completed devotionals ────────────────────────────────
    lang_data = existing["data"][master_lang]
    completed: dict[str, dict] = {
        date_key: entries[0]
        for date_key, entries in lang_data.items()
        if entries
    }
    print(f"✅ Loaded {len(completed)} existing devotionals from {args.existing}")

    # ── Build custom_id → date_key map ─────────────────────────────────────
    def _safe_custom_id(date_key: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]", "_", date_key)[:64]

    # Map only the failed dates
    custom_id_map = {_safe_custom_id(d): d for d in repair_dates}

    # ── Connect to Anthropic ───────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")
    client = anthropic.Anthropic(api_key=api_key)

    # ── Re-fetch batch results for failed dates ────────────────────────────
    print(f"\nFetching batch results from Anthropic (batch {batch_id})...")
    repaired:     dict[str, dict]  = {}
    still_failed: list[str]        = []

    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id
        if custom_id not in custom_id_map:
            continue  # already in completed; skip

        date_key   = custom_id_map[custom_id]
        seed_entry = seed.get(date_key, {})

        if result.result.type != "succeeded":
            print(f"  [NON-SUCCEEDED] {date_key}")
            still_failed.append(date_key)
            continue

        raw_text = result.result.message.content[0].text.strip()
        data = repair_json(raw_text)

        if not data:
            print(f"  [REPAIR_FAIL]   {date_key} — all strategies failed")
            still_failed.append(date_key)
            continue

        reflexion = data.get("reflexion", "").strip()
        oracion   = data.get("oracion",   "").strip()

        if not reflexion or not oracion:
            print(f"  [EMPTY_FIELDS]  {date_key}")
            still_failed.append(date_key)
            continue

        if not seed_entry:
            print(f"  [NO_SEED]       {date_key}")
            still_failed.append(date_key)
            continue

        try:
            devo = build_devotional(date_key, seed_entry, master_lang, master_version,
                                    reflexion, oracion)
            repaired[date_key] = devo
            print(f"  REPAIRED ✅     {date_key} — reflexion:{len(reflexion)} oracion:{len(oracion)}")
        except Exception as ex:
            print(f"  [BUILD_ERROR]   {date_key} — {ex}")
            still_failed.append(date_key)

    print(f"\nRepaired from batch: {len(repaired)}")
    print(f"Still failed: {len(still_failed)}")

    # ── API fallback for still-failed dates ────────────────────────────────
    api_generated: dict[str, dict] = {}
    if still_failed:
        print(f"\nRegenerating {len(still_failed)} dates via direct API...")
        for date_key in sorted(still_failed):
            seed_entry = seed.get(date_key, {})
            if not seed_entry:
                print(f"  [SKIP] {date_key} — no seed entry")
                continue
            devo = regenerate_via_api(date_key, seed_entry, master_lang, master_version,
                                      client, args.model)
            if devo:
                api_generated[date_key] = devo
            else:
                print(f"  [FINAL_FAIL]   {date_key}")
            # Small rate-limit pause
            time.sleep(0.5)

    # ── Merge all results ──────────────────────────────────────────────────
    merged = {**completed, **repaired, **api_generated}
    print(f"\n{SEP}")
    print(f"MERGE SUMMARY")
    print(f"  Existing     : {len(completed)}")
    print(f"  Repaired     : {len(repaired)}")
    print(f"  API-generated: {len(api_generated)}")
    print(f"  Total merged : {len(merged)}")
    print(f"  Failed dates : {len(failed_dates) - len(repaired) - len(api_generated)} still missing")
    print(SEP)

    # ── Save output ────────────────────────────────────────────────────────
    out_data = {
        "data": {
            master_lang: {
                date_key: [devo]
                for date_key, devo in sorted(merged.items())
            }
        }
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Saved → {out_path}")

    # Check if any dates are still missing
    missing = sorted(failed_dates - set(merged.keys()) - build_errors)
    if missing:
        print(f"\n⚠️  {len(missing)} dates still missing: {missing}")

        # Save a remaining errors file
        err_out = out_path.parent / f"remaining_errors_{master_lang}_{master_version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(err_out, "w", encoding="utf-8") as f:
            json.dump([{"date": d, "reason": "unrepairable"} for d in missing], f, ensure_ascii=False, indent=2)
        print(f"   Remaining errors → {err_out}")
    else:
        print(f"\n🎉 All failed dates successfully repaired!")

    print(SEP + "\n")


if __name__ == "__main__":
    main()

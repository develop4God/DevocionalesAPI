"""
batch_repair.py
───────────────
Repair failed dates from any provider using the same or a different provider.

For Fireworks (async_parallel) there is no batch to re-fetch —
all repairs go directly to the provider's generate_one() API call.
For Gemini (native batch) the same applies — collect() is not re-invoked for repair.
For Anthropic (native batch) it re-fetches the original batch first, then falls
back to direct API for still-failed dates.

CLI:
  python batch_repair.py \\
    --state    batch_state_tl_ADB_gemini_20260421_120000.json \\
    --errors   2025/yearly_devotionals/TL/batch_errors_tl_ADB_gemini_20260421_130000.json \\
    --existing 2025/yearly_devotionals/TL/raw_tl_ADB_gemini_20260421_130000.json \\
    --output   2025/yearly_devotionals/TL/Devocional_year_2025_tl_ADB.json \\
    [--provider gemini]   # override provider for repair (defaults to original)
    [--model   gemini-2.5-flash]   # optionally use a better model for repair
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pipeline_shared import repair_json, build_prompt
from provider_adapter import load_adapter, BatchRequest
from batch_collect import build_devotional, run_phase1

_SCRIPT_DIR = Path(__file__).parent


def _safe_custom_id(date_key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", date_key)[:64]


def _load_existing_devotionals(path: str, lang: str) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    lang_data = raw["data"][lang]
    return {date: entries[0] for date, entries in lang_data.items() if entries}


def _save_merged(merged: dict, lang: str, output_path: str) -> None:
    out_data = {
        "data": {
            lang: {date: [devo] for date, devo in sorted(merged.items())}
        }
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)


def repair(
    state_path:     str,
    errors_path:    str,
    existing_path:  str,
    output_path:    str,
    provider_override: str | None = None,
    model_alias:       str | None = None,
    max_retries:       int = 3,
) -> None:
    SEP = "=" * 60

    # ── Load inputs ────────────────────────────────────────────────────────
    with open(state_path,  encoding="utf-8") as f: state    = json.load(f)
    with open(errors_path, encoding="utf-8") as f: err_list = json.load(f)

    original_provider = state["provider"]
    provider          = provider_override or original_provider
    master_lang       = state["master_lang"]
    master_version    = state["master_version"]
    seed_path         = state["seed_path"]
    original_model_id = state.get("model_id", "")

    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    completed = _load_existing_devotionals(existing_path, master_lang)

    # Dates that need repair
    failed_dates  = {e["date"] for e in err_list}
    build_errors  = {e["date"] for e in err_list if e.get("reason") == "build_error"}
    repair_dates  = sorted(failed_dates - build_errors)

    print(SEP)
    print("BATCH REPAIR")
    print(f"  Original provider : {original_provider}")
    print(f"  Repair provider   : {provider}")
    print(f"  Lang              : {master_lang}  Version: {master_version}")
    print(f"  Existing          : {len(completed)}")
    print(f"  Failed dates      : {len(failed_dates)}")
    print(f"  Build errors skip : {len(build_errors)}")
    print(f"  To repair         : {len(repair_dates)}")
    print(SEP + "\n")

    if not repair_dates:
        print("Nothing to repair.")
        _save_merged(completed, master_lang, output_path)
        print(f"✅ Saved → {output_path}")
        return

    # ── Adapter ────────────────────────────────────────────────────────────
    adapter = load_adapter(provider, model_alias)
    print(f"INFO: Using {provider} / {adapter.model_id} for repair\n")

    # ── Anthropic: try re-fetching original batch first ────────────────────
    repaired: dict[str, dict] = {}
    still_failed: list[str]   = list(repair_dates)

    if original_provider == "anthropic" and provider == "anthropic":
        job_id   = state["job_id"]
        cid_map  = {_safe_custom_id(d): d for d in repair_dates}
        requests_for_collect = [
            BatchRequest(
                date_key=d,
                custom_id=_safe_custom_id(d),
                prompt="",        # not needed for re-fetch
                model_id=original_model_id,
            )
            for d in repair_dates
        ]
        print(f"INFO: Re-fetching Anthropic batch {job_id} for {len(repair_dates)} dates...")
        raw_results = adapter.collect(job_id, requests_for_collect)
        still_failed = []
        for result in raw_results:
            date_key = result.date_key
            if not result.succeeded:
                still_failed.append(date_key)
                continue
            data = repair_json(result.raw_text)
            if not data:
                still_failed.append(date_key)
                continue
            reflexion = data.get("reflexion", "").strip()
            oracion   = data.get("oracion",   "").strip()
            if not reflexion or not oracion:
                still_failed.append(date_key)
                continue
            seed_entry = seed.get(date_key, {})
            if not seed_entry:
                still_failed.append(date_key)
                continue
            try:
                devo = build_devotional(date_key, seed_entry, master_lang, master_version,
                                        reflexion, oracion)
                repaired[date_key] = devo
                print(f"  REPAIRED ✅ {date_key}")
            except Exception as e:
                print(f"  [BUILD_ERR] {date_key} — {e}")
                still_failed.append(date_key)

        print(f"\nRepaired from batch re-fetch: {len(repaired)}")
        print(f"Still failed: {len(still_failed)}")

    # ── Direct API fallback for all remaining ─────────────────────────────
    api_generated: dict[str, dict] = {}
    if still_failed:
        print(f"\nRegenerating {len(still_failed)} dates via direct API ({provider})...")
        for date_key in still_failed:
            seed_entry = seed.get(date_key, {})
            if not seed_entry:
                print(f"  [SKIP] {date_key} — no seed")
                continue

            cita  = seed_entry.get("versiculo", {}).get("cita", "")
            topic = seed_entry.get("topic")
            request = BatchRequest(
                date_key=date_key,
                custom_id=_safe_custom_id(date_key),
                prompt=build_prompt(cita, master_lang, topic),
                model_id=adapter.model_id,
            )

            devo = None
            for attempt in range(1, max_retries + 1):
                result = adapter.generate_one(request)
                if not result.succeeded:
                    print(f"    [RETRY {attempt}] {date_key} — {result.error}")
                    time.sleep(3 * attempt)
                    continue

                data = repair_json(result.raw_text)
                if not data:
                    print(f"    [RETRY {attempt}] {date_key} — JSON repair failed")
                    time.sleep(2)
                    continue

                reflexion = data.get("reflexion", "").strip()
                oracion   = data.get("oracion",   "").strip()
                if not reflexion or not oracion:
                    print(f"    [RETRY {attempt}] {date_key} — empty fields")
                    time.sleep(2)
                    continue

                p1_passed, p1_issues = run_phase1(reflexion, oracion, master_lang)
                if p1_issues:
                    print(f"    ⚠ {date_key} — Phase1: {p1_issues}")

                try:
                    devo = build_devotional(date_key, seed_entry, master_lang, master_version,
                                            reflexion, oracion)
                    print(f"  API ✅ {date_key} (attempt {attempt})")
                    break
                except Exception as e:
                    print(f"    [BUILD_ERR] {date_key} — {e}")
                    time.sleep(2)

            if devo:
                api_generated[date_key] = devo
            else:
                print(f"  API ❌ {date_key} — gave up after {max_retries} attempts")

            time.sleep(0.3)  # rate limit courtesy pause

    # ── Merge ──────────────────────────────────────────────────────────────
    merged = {**completed, **repaired, **api_generated}
    still_missing = sorted(failed_dates - set(merged.keys()) - build_errors)

    print(f"\n{SEP}")
    print("MERGE SUMMARY")
    print(f"  Existing      : {len(completed)}")
    print(f"  Repaired      : {len(repaired)}")
    print(f"  API-generated : {len(api_generated)}")
    print(f"  Total merged  : {len(merged)}")
    print(f"  Still missing : {len(still_missing)}")
    print(SEP)

    _save_merged(merged, master_lang, output_path)
    print(f"\n✅ Saved → {output_path}")

    if still_missing:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        err_out = Path(output_path).parent / f"remaining_errors_{master_lang}_{master_version}_{ts}.json"
        with open(err_out, "w", encoding="utf-8") as f:
            json.dump([{"date": d, "reason": "unrepairable"} for d in still_missing],
                      f, ensure_ascii=False, indent=2)
        print(f"⚠️  {len(still_missing)} dates still missing → {err_out}")
    else:
        print("🎉 All failed dates successfully repaired!")

    print(SEP + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Repair failed devotional dates using any provider."
    )
    parser.add_argument("--state",    required=True, help="batch_state_*.json")
    parser.add_argument("--errors",   required=True, help="batch_errors_*.json")
    parser.add_argument("--existing", required=True, help="existing raw_*.json output")
    parser.add_argument("--output",   required=True, help="final merged output path")
    parser.add_argument("--provider", default=None,
                        help="Override provider for repair (default: same as original)")
    parser.add_argument("--model",    default=None,
                        help="Model alias for repair (default: provider default)")
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    repair(
        state_path        = args.state,
        errors_path       = args.errors,
        existing_path     = args.existing,
        output_path       = args.output,
        provider_override = args.provider,
        model_alias       = args.model,
        max_retries       = args.max_retries,
    )


if __name__ == "__main__":
    main()

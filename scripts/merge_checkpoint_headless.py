#!/usr/bin/env python3
"""
merge_checkpoint_headless.py — Non-GUI equivalent of merge_devocional_gui.py's
merge step, for the LU17 2027 checkpoint (generate_from_seed_checkpoint_*.json)
specifically. Written for an unattended overnight run where the real tkinter
GUI (no headless mode) can't be driven safely.

Reuses validate_devocional_gui.py's validate_entry/check_content_quality (same
functions the GUI imports) rather than reimplementing validation logic, and
writes the exact same output shape merge_devocional_gui.py's _do_merge()
produces: {"data": {lang: {date: [entry]}}}, named
Devocional_year_{year}_{lang}_{version}.json.

This script does ONE thing: merge a checkpoint's completed entries into that
final file, with a report of any missing dates and content-quality issues.
It does NOT retry pending/timed-out dates (that's the resume script's job,
run separately) and does NOT overwrite an existing output file without
--force, since that file may be what the live app serves.

Usage:
    python3 merge_checkpoint_headless.py \
        --checkpoint /path/to/generate_from_seed_checkpoint_gemma4-3a-26b.json \
        --seed-path /path/to/seed_de_LU17_for_2027.json \
        --year 2027 --lang de --version LU17 \
        --out-dir /path/to/output/dir
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_devocional_gui import validate_entry  # noqa: E402


def load_checkpoint_entries(checkpoint_path: Path) -> dict[str, dict]:
    """Returns {date_key: entry_dict} from a generate_from_seed checkpoint's
    "completed" field -- the same date-keyed shape PartialFile.entries holds
    in the GUI, just sourced from a checkpoint instead of a partial JSON."""
    with open(checkpoint_path, encoding="utf-8") as f:
        data = json.load(f)
    completed = data.get("completed")
    if not isinstance(completed, dict):
        raise ValueError(f"No 'completed' dict found in {checkpoint_path}")
    return completed


def expected_dates_from_seed(seed_path: Path) -> list[str]:
    """The seed file is the real source of truth for which dates this cycle
    covers -- confirmed NOT a calendar year for LU17 2027 (it's a 366-day
    reading cycle from 2027-08-01 through 2028-07-31, not Jan-Dec), so
    "expected" must come from the seed's own keys, never assumed from
    --year."""
    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)
    return sorted(seed.keys())


def validate_entries(
    entries: dict[str, dict], lang: str, version: str
) -> dict[str, list[str]]:
    """date_key -> list of issue strings, same as PartialFile.entry_issues."""
    issues = {}
    for date_key, entry in entries.items():
        found = validate_entry(entry, date_key, lang, version)
        if found:
            issues[date_key] = found
    return issues


def build_output(entries: dict[str, dict], lang: str) -> dict:
    """Same shape as merge_devocional_gui.py's _do_merge() output:
    {"data": {lang: {date: [entry]}}}."""
    sorted_dates = sorted(entries.keys())
    return {"data": {lang: {dk: [entries[dk]] for dk in sorted_dates}}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument(
        "--seed-path",
        required=True,
        help="The seed JSON this checkpoint was generated from -- its own "
        "keys are the real expected-dates universe (not assumed from --year, "
        "since a reading cycle can span two calendar years).",
    )
    parser.add_argument(
        "--year",
        type=int,
        required=True,
        help="Used only for the output filename (Devocional_year_{year}_...), "
        "matching this project's existing naming convention -- not used to "
        "compute which dates are expected.",
    )
    parser.add_argument("--lang", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing output file. Without this flag, refuses "
        "to overwrite -- that file may be what the live app serves.",
    )
    args = parser.parse_args()

    checkpoint_path = Path(args.checkpoint)
    entries = load_checkpoint_entries(checkpoint_path)

    expected_dates = expected_dates_from_seed(Path(args.seed_path))
    missing = [d for d in expected_dates if d not in entries]
    issues = validate_entries(entries, args.lang, args.version)

    print(f"Checkpoint: {checkpoint_path}")
    print(f"Entries found: {len(entries)} / {len(expected_dates)} expected")
    if missing:
        print(f"Missing dates ({len(missing)}): {missing[:10]}"
              f"{' ...' if len(missing) > 10 else ''}")
    if issues:
        print(f"Entries with content-quality issues: {len(issues)}")
        for dk, iss in list(issues.items())[:10]:
            print(f"  {dk}: {iss}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = f"Devocional_year_{args.year}_{args.lang}_{args.version}.json"
    out_path = out_dir / out_name

    if out_path.exists() and not args.force:
        print(f"\nRefusing to overwrite existing file: {out_path}")
        print("Pass --force to overwrite.")
        return 1

    output = build_output(entries, args.lang)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nWrote {len(entries)} entries to {out_path}")
    if missing:
        print(f"NOTE: {len(missing)} dates are still missing -- this is a "
              f"partial merge, not a complete {args.year} file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

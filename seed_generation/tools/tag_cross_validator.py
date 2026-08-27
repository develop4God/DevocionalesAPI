"""
tag_cross_validator.py
───────────────────────
EN is the source of truth for devotional tags. For every date, each EN tag
(in order) is looked up in tags_master.json to get its expected translation
in the target language, then compared byte-for-byte against the tag at that
same position in the target-language devotional file.

No tolerance, no fallback, no set matching — exact string equality only.
Any difference (wrong word, wrong order, wrong count, unknown tag) is a fail.

Usage:
  python -m seed_generation.tools.tag_cross_validator <target.json> <reference.json> \\
      --target-lang es --reference-lang en [--tags-master path/to/tags_master.json]

  <target.json> / <reference.json> — devotional JSON files shaped like:
      {"data": {"<lang>": {"<date>": [{"tags": [...], ...}], ...}}}
"""

import argparse
import json
from pathlib import Path

_TAGS_MASTER_DEFAULT = Path(__file__).parent.parent / "tags_master.json"


def load_tags_master(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["tags"]


def load_entries(json_path: str, lang: str) -> dict[str, list[str]]:
    """
    date -> tags list.

    Supports two shapes:
      - devotional JSON: {"data": {"<lang>": {"<date>": [{"tags": [...]}]}}}
      - seed JSON:        {"<date>": {"tags": [...]}}  (lang unused)
    """
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    if "data" in data:
        lang_data = data["data"][lang]
        return {date: entries[0].get("tags", []) for date, entries in lang_data.items()}
    return {date: entry.get("tags", []) for date, entry in data.items()}


def expected_translation(en_tag: str, target_lang: str, tags_master: dict) -> str | None:
    """
    Look up en_tag's canonical entry in tags_master.json (keyed by the EN
    value, since EN is the source of truth) and return its target-language
    translation. None if en_tag isn't in the master at all.
    """
    entry = tags_master.get(en_tag)
    if entry is None:
        for candidate in tags_master.values():
            if candidate.get("en") == en_tag:
                entry = candidate
                break
    if entry is None:
        return None
    return entry.get(target_lang)


def cross_validate(
    target_entries: dict[str, list[str]],
    reference_entries: dict[str, list[str]],
    target_lang: str,
    tags_master: dict,
) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {}

    dates = sorted(set(target_entries) | set(reference_entries))
    for date in dates:
        date_issues = []
        if date not in target_entries:
            date_issues.append(f"missing from target ({target_lang})")
            issues[date] = date_issues
            continue
        if date not in reference_entries:
            date_issues.append("missing from reference (en)")
            issues[date] = date_issues
            continue

        target_tags = target_entries[date]
        reference_tags = reference_entries[date]

        if len(target_tags) != len(reference_tags):
            date_issues.append(
                f"tag count mismatch: en={len(reference_tags)} ({reference_tags}) "
                f"vs {target_lang}={len(target_tags)} ({target_tags})"
            )
        else:
            for i, en_tag in enumerate(reference_tags):
                expected = expected_translation(en_tag, target_lang, tags_master)
                if expected is None:
                    date_issues.append(f"unknown EN tag '{en_tag}' (not in tags_master)")
                    continue
                actual = target_tags[i]
                if actual != expected:
                    date_issues.append(
                        f"position {i}: en='{en_tag}' expected {target_lang}="
                        f"'{expected}' but found '{actual}'"
                    )

        if date_issues:
            issues[date] = date_issues

    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="Path to target-language devotional JSON")
    parser.add_argument("reference", help="Path to reference (EN) devotional JSON")
    parser.add_argument("--target-lang", required=True, help="e.g. es")
    parser.add_argument("--reference-lang", default="en", help="default: en")
    parser.add_argument(
        "--tags-master",
        default=str(_TAGS_MASTER_DEFAULT),
        help="Path to tags_master.json (default: seed_generation/tags_master.json)",
    )
    args = parser.parse_args()

    tags_master = load_tags_master(Path(args.tags_master))
    target_entries = load_entries(args.target, args.target_lang)
    reference_entries = load_entries(args.reference, args.reference_lang)

    issues = cross_validate(target_entries, reference_entries, args.target_lang, tags_master)

    total = len(set(target_entries) | set(reference_entries))
    print(f"Entries compared: {total}\n{'=' * 60}")
    print(f"Results: {total - len(issues)}/{total} passed\n")

    if issues:
        print(f"{'=' * 60}\nFAILED ENTRIES ({len(issues)})\n{'=' * 60}")
        for date, date_issues in sorted(issues.items()):
            print(f"\n❌ {date}")
            for issue in date_issues:
                print(f"   • {issue}")
    else:
        print("✅ ALL ENTRIES PASSED")


if __name__ == "__main__":
    main()

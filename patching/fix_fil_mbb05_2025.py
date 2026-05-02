#!/usr/bin/env python3
"""
Remediation script for Filipino MBB05 2025 devotional file.
Based on genome error report: fil_MBB05_2025_genome_errors.txt

Usage:
    python fix_fil_mbb05_2025.py --input <file> --dry-run  # Preview changes
    python fix_fil_mbb05_2025.py --input <file>           # Apply fixes
"""

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


# Pattern groups organized by error type with confidence levels
TYPO_PATTERNS = [
    # Christonym spacing variants
    {
        "pattern": r"HesuKristo",
        "replacement": "Hesukristo",
        "description": "Inconsistent Christonym spacing",
        "confidence": 0.95,
    },
    {
        "pattern": r"Hesu-Kristo",
        "replacement": "Hesukristo",
        "description": "Inconsistent Christonym spacing",
        "confidence": 0.95,
    },
    {
        "pattern": r"Hesu Kristo",
        "replacement": "Hesukristo",
        "description": "Inconsistent Christonym spacing",
        "confidence": 0.95,
    },
    # Other typos
    {
        "pattern": r"reconciliasyon",
        "replacement": "rekonsilyasyon",
        "description": "Spanish-borrowed misspelling",
        "confidence": 0.9,
    },
    {
        "pattern": r"transpornasyong",
        "replacement": "transpormasyong",
        "description": "Metathesis typo",
        "confidence": 0.9,
    },
    {
        "pattern": r"S'ya\b",
        "replacement": "Siya",
        "description": "Unnecessary apostrophe contraction",
        "confidence": 0.95,
    },
    {
        "pattern": r"pagkakalingap\b",
        "replacement": "pagkakalinga",
        "description": "Erroneous suffix",
        "confidence": 0.85,
    },
    {
        "pattern": r"pagkadyos",
        "replacement": "pagka-Diyos",
        "description": "Missing hyphen + capitalisation",
        "confidence": 0.9,
    },
    {
        "pattern": r"minalas\b",
        "replacement": "minsan",
        "description": "Wrong word confusion",
        "confidence": 0.85,
    },
    {
        "pattern": r"tigsasandaang",
        "replacement": "tig-sasandaang",
        "description": "Missing hyphen",
        "confidence": 0.9,
    },
    {
        "pattern": r"transforming power",
        "replacement": "nagbabagong kapangyarihan",
        "description": "English phrase in Filipino text",
        "confidence": 0.95,
    },
]

REPETITION_PATTERNS = [
    {
        "pattern": r"tiwala at pagtitiwala",
        "replacement": "tiwala",
        "description": "Redundant near-synonyms",
        "confidence": 0.9,
    },
    {
        "pattern": r"pusong nagpapakumbaba at buong pagpapakumbaba",
        "replacement": "pusong nagpapakumbaba",
        "description": "Same root repeated",
        "confidence": 0.9,
    },
    {
        "pattern": r"makapangyarihang kapangyarihan",
        "replacement": "kapangyarihan",
        "description": "Root-form redundancy",
        "confidence": 0.9,
    },
    {
        "pattern": r"pag-asa at pag-asa",
        "replacement": "pag-asa at pagtitiwala",
        "description": "Direct word duplicate",
        "confidence": 0.95,
    },
    {
        "pattern": r"lubos na lampas sa aming pang-unawa",
        "replacement": "lampas sa aming pang-unawa",
        "description": "'lubos na' redundant with 'lampas'",
        "confidence": 0.8,
    },
]

UNNATURAL_PATTERNS = [
    # Throne calque (most frequent)
    {
        "pattern": r"lumalapit po kami sa Iyong luklukan ng biyaya",
        "replacement": "lumalapit po kami sa Iyong trono ng biyaya",
        "description": "Calque of 'throne of grace'; full phrase",
        "confidence": 0.85,
    },
    {
        "pattern": r"luklukan ng biyaya",
        "replacement": "trono ng biyaya",
        "description": "Calque of 'throne of grace'; 'trono' more natural",
        "confidence": 0.85,
    },
    # Negation and structure issues
    {
        "pattern": r"ang Kanyang di-nagbabagong pag-ibig",
        "replacement": "ang Kanyang pag-ibig na hindi nagbabago",
        "description": "Awkward prenominal negation",
        "confidence": 0.85,
    },
    {
        "pattern": r"nagbibigay-buhay at pag-asa\b",
        "replacement": "nagbibigay-buhay at nagbibigay-pag-asa",
        "description": "Parallel structure break",
        "confidence": 0.8,
    },
    {
        "pattern": r"Pinupuspos po kami ng pag-asa",
        "replacement": "Punuin po Ninyo kami ng pag-asa",
        "description": "Passive-voice awkwardness",
        "confidence": 0.8,
    },
    {
        "pattern": r"nagiging Iyong mga instrumento ng pagpapala",
        "replacement": "nagiging mga instrumento ng Iyong pagpapala",
        "description": "Possessive word-order",
        "confidence": 0.8,
    },
    {
        "pattern": r"sukdulan at di-kanais-nais na pagmamahal",
        "replacement": "sukdulan at di-malirip na pagmamahal",
        "description": "'di-kanais-nais'=undesirable, wrong register",
        "confidence": 0.85,
    },
    {
        "pattern": r"sa kakayahan naming manampalataya",
        "replacement": "sa kakayahan naming sumampalataya",
        "description": "Verb-form register mismatch",
        "confidence": 0.8,
    },
    {
        "pattern": r"kagalakang di masayod",
        "replacement": "kagalakang di masukat",
        "description": "Rare/archaic 'masayod'",
        "confidence": 0.9,
    },
    {
        "pattern": r"mga tagapagpakalat",
        "replacement": "mga tagapagdala",
        "description": "Unnatural agent nominalisation",
        "confidence": 0.8,
    },
    {
        "pattern": r"sa pagtakwil sa lahat ng uri ng kapagmataasan",
        "replacement": "sa pagtakwil sa lahat ng anyo ng kapalaluan",
        "description": "Mixed register",
        "confidence": 0.8,
    },
    {
        "pattern": r"nais naming",
        "replacement": "gusto naming",
        "description": "'nais' is formal/literary; 'gusto' more natural",
        "confidence": 0.75,
    },
]

GRAMMAR_PATTERNS = [
    {
        "pattern": r"aming kaharapin",
        "replacement": "aming haharapin",
        "description": "Past-tense verb in future context",
        "confidence": 0.85,
    },
    {
        "pattern": r"nagbibigay ng pagkakataon para sa atin upang maranasan",
        "replacement": "nagbibigay sa atin ng pagkakataong maranasan",
        "description": "Restructure indirect object",
        "confidence": 0.8,
    },
]


class DevotionalFixer:
    """Apply fixes to Filipino devotional JSON file."""

    def __init__(self, input_path: Path, dry_run: bool = False):
        self.input_path = input_path
        self.dry_run = dry_run
        self.fixes_applied = {
            "typo": [],
            "repetition": [],
            "unnatural": [],
            "grammar": [],
        }
        self.backup_path: Path | None = None

    def load_json(self) -> dict[str, Any]:
        """Load and validate JSON file."""
        with open(self.input_path, encoding="utf-8") as f:
            return json.load(f)

    def save_json(self, data: dict[str, Any]) -> None:
        """Save JSON file with proper formatting."""
        with open(self.input_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def create_backup(self) -> None:
        """Create timestamped backup of original file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = (
            f"{self.input_path.stem}_backup_{timestamp}{self.input_path.suffix}"
        )
        self.backup_path = self.input_path.parent / backup_name
        shutil.copy2(self.input_path, self.backup_path)
        print(f"✅ Backup created: {self.backup_path}")

    def apply_pattern_group(
        self, text: str, patterns: list[dict], group_name: str
    ) -> str:
        """Apply a group of patterns to text and track fixes."""
        for pattern_def in patterns:
            pattern = pattern_def["pattern"]
            replacement = pattern_def["replacement"]
            description = pattern_def["description"]
            confidence = pattern_def["confidence"]

            # Count matches before replacement
            matches = list(re.finditer(pattern, text))
            if matches:
                # Apply replacement
                new_text = re.sub(pattern, replacement, text)
                if new_text != text:
                    self.fixes_applied[group_name].append(
                        {
                            "pattern": pattern,
                            "replacement": replacement,
                            "description": description,
                            "confidence": confidence,
                            "count": len(matches),
                        }
                    )
                    text = new_text

        return text

    def process_devotionals(self, data: dict[str, Any]) -> dict[str, Any]:
        """Process all devotional entries and apply fixes."""
        devotionals_processed = 0

        for lang_key, lang_data in data.get("data", {}).items():
            if lang_key != "fil":
                continue

            for date_key, devotionals in lang_data.items():
                for devotional in devotionals:
                    # Apply fixes to text fields
                    for field in ["reflexion", "oracion"]:
                        if field in devotional:
                            original = devotional[field]
                            # Apply patterns in order: typo → repetition → unnatural → grammar
                            fixed = self.apply_pattern_group(
                                original, TYPO_PATTERNS, "typo"
                            )
                            fixed = self.apply_pattern_group(
                                fixed, REPETITION_PATTERNS, "repetition"
                            )
                            fixed = self.apply_pattern_group(
                                fixed, UNNATURAL_PATTERNS, "unnatural"
                            )
                            fixed = self.apply_pattern_group(
                                fixed, GRAMMAR_PATTERNS, "grammar"
                            )
                            devotional[field] = fixed

                    # Also check para_meditar text fields
                    for item in devotional.get("para_meditar", []):
                        if "texto" in item:
                            original = item["texto"]
                            fixed = self.apply_pattern_group(
                                original, TYPO_PATTERNS, "typo"
                            )
                            fixed = self.apply_pattern_group(
                                fixed, REPETITION_PATTERNS, "repetition"
                            )
                            fixed = self.apply_pattern_group(
                                fixed, UNNATURAL_PATTERNS, "unnatural"
                            )
                            fixed = self.apply_pattern_group(
                                fixed, GRAMMAR_PATTERNS, "grammar"
                            )
                            item["texto"] = fixed

                    devotionals_processed += 1

        print(f"📖 Processed {devotionals_processed} devotionals")
        return data

    def print_summary(self) -> None:
        """Print summary of fixes applied."""
        print("\n" + "=" * 80)
        print("FIX SUMMARY")
        print("=" * 80)

        total_fixes = 0
        for group_name, fixes in self.fixes_applied.items():
            if fixes:
                print(f"\n▶ {group_name.upper()} FIXES ({len(fixes)} patterns)")
                print("-" * 80)
                for fix in fixes:
                    total_fixes += fix["count"]
                    print(
                        f"  [{fix['count']:3d}x] {fix['pattern'][:40]:40s} → {fix['replacement'][:40]}"
                    )
                    print(
                        f"         {fix['description']} (confidence: {fix['confidence']})"
                    )

        print("\n" + "=" * 80)
        print(f"TOTAL REPLACEMENTS: {total_fixes}")
        print("=" * 80)

    def run(self) -> None:
        """Execute the fix workflow."""
        print(f"🔍 Loading: {self.input_path}")
        data = self.load_json()

        print(f"🔧 Mode: {'DRY RUN' if self.dry_run else 'APPLY FIXES'}")

        if not self.dry_run:
            self.create_backup()

        # Process devotionals
        fixed_data = self.process_devotionals(data)

        # Print summary
        self.print_summary()

        # Save if not dry-run
        if not self.dry_run:
            self.save_json(fixed_data)
            print(f"\n✅ Fixes applied and saved to: {self.input_path}")
            if self.backup_path:
                print(f"📦 Original backed up to: {self.backup_path}")
        else:
            print("\n⚠️  DRY RUN COMPLETE — No changes written to file")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fix Filipino MBB05 2025 devotional file based on genome error report"
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to input JSON file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying file",
    )

    args = parser.parse_args()

    if not args.input.exists():
        print(f"❌ Error: File not found: {args.input}")
        return

    fixer = DevotionalFixer(args.input, dry_run=args.dry_run)
    fixer.run()


if __name__ == "__main__":
    main()

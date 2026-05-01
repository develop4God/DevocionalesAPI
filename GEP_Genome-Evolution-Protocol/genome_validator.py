"""
genome_validator.py — GEP Critic v3
Single responsibility: validate devotional entries against genome patterns.

Design philosophy:
    - Use genome patterns to detect known errors in devotional JSON files
    - SOLID architecture: Single responsibility for pattern-based validation
    - No token cost — pre-validation before API calls
    - Returns validation report with matched patterns

Usage:
    from genome_validator import validate_entries, validate_file

    # Validate loaded entries
    report = validate_entries(entries, genome, lang, version, year)

    # Validate JSON file directly
    report = validate_file("path/to/file.json", genome, lang)
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple

from models import DevotionalEntry, Genome, GenomeFragment, PauseCategory


@dataclass
class PatternMatch:
    """A match of a genome pattern in an entry."""
    entry_id: str
    entry_date: str
    field: str  # "reflexion", "oracion", or "versiculo"
    fragment_id: str
    category: str
    pattern: str
    matched_text: str
    confidence: float
    line_number: int = 0  # For reporting


@dataclass
class ValidationReport:
    """Report of genome pattern validation."""
    total_entries: int
    entries_with_matches: int
    total_matches: int
    matches: List[PatternMatch]
    categories_found: Dict[str, int]  # category -> count

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"\n{'═'*60}",
            f"  📊 Genome Pattern Validation Report",
            f"{'═'*60}",
            f"  Total entries scanned : {self.total_entries}",
            f"  Entries with matches  : {self.entries_with_matches}",
            f"  Total pattern matches : {self.total_matches}",
        ]

        if self.categories_found:
            lines.append(f"\n  Patterns found by category:")
            for cat, count in sorted(self.categories_found.items(), key=lambda x: -x[1]):
                lines.append(f"    - {cat:<20} : {count} matches")

        if self.matches:
            lines.append(f"\n  Top matches (showing first 10):")
            for match in self.matches[:10]:
                lines.append(
                    f"    [{match.category}] {match.entry_date} — "
                    f"{match.field}: \"{match.matched_text[:60]}...\""
                )

        lines.append(f"{'═'*60}\n")
        return "\n".join(lines)


def _search_pattern_in_text(
    text: str,
    fragment: GenomeFragment,
    entry_id: str,
    entry_date: str,
    field: str
) -> List[PatternMatch]:
    """Search for a genome pattern in text. Returns list of matches."""
    matches = []

    # Try exact quote match first
    if fragment.example_quote.lower() in text.lower():
        matches.append(PatternMatch(
            entry_id=entry_id,
            entry_date=entry_date,
            field=field,
            fragment_id=fragment.id,
            category=fragment.category.value,
            pattern=fragment.pattern,
            matched_text=fragment.example_quote,
            confidence=fragment.confidence,
        ))
        return matches

    # Try pattern-based search (convert pattern to regex)
    # Pattern format: "word1 word2" or "word1.*word2" or similar
    pattern_regex = fragment.pattern.replace(" ", r"\s+")

    try:
        regex = re.compile(pattern_regex, re.IGNORECASE | re.MULTILINE)
        for match in regex.finditer(text):
            matched_text = match.group(0)
            matches.append(PatternMatch(
                entry_id=entry_id,
                entry_date=entry_date,
                field=field,
                fragment_id=fragment.id,
                category=fragment.category.value,
                pattern=fragment.pattern,
                matched_text=matched_text,
                confidence=fragment.confidence,
            ))
    except re.error:
        # If pattern is not valid regex, skip
        pass

    return matches


def validate_entry(
    entry: DevotionalEntry,
    genome: Genome,
    confidence_threshold: float = 0.6
) -> List[PatternMatch]:
    """
    Validate a single entry against genome patterns.

    Args:
        entry: DevotionalEntry to validate
        genome: Genome with patterns to check
        confidence_threshold: Minimum confidence for patterns to check

    Returns:
        List of PatternMatch objects for patterns found
    """
    if not genome:
        return []

    matches = []
    high_conf_fragments = genome.high_confidence_fragments(threshold=confidence_threshold)

    # Check reflexion field
    for fragment in high_conf_fragments:
        matches.extend(_search_pattern_in_text(
            entry.reflexion,
            fragment,
            entry.id,
            entry.date,
            "reflexion"
        ))

    # Check oracion field
    for fragment in high_conf_fragments:
        matches.extend(_search_pattern_in_text(
            entry.oracion,
            fragment,
            entry.id,
            entry.date,
            "oracion"
        ))

    # Check versiculo field (less common, but possible)
    for fragment in high_conf_fragments:
        matches.extend(_search_pattern_in_text(
            entry.versiculo,
            fragment,
            entry.id,
            entry.date,
            "versiculo"
        ))

    return matches


def validate_entries(
    entries: List[DevotionalEntry],
    genome: Genome,
    lang: str,
    version: str,
    year: int,
    confidence_threshold: float = 0.6
) -> ValidationReport:
    """
    Validate multiple entries against genome patterns.

    Args:
        entries: List of DevotionalEntry objects
        genome: Genome with patterns
        lang: Language code
        version: Bible version
        year: Year
        confidence_threshold: Minimum confidence for patterns

    Returns:
        ValidationReport with all matches found
    """
    all_matches = []
    entries_with_matches = 0
    categories_found: Dict[str, int] = {}

    print(f"\n  🔍 Validating {len(entries)} entries against genome patterns...")
    print(f"     Language: {lang} | Version: {version} | Year: {year}")
    print(f"     Confidence threshold: {confidence_threshold}")

    if genome:
        fragments = genome.high_confidence_fragments(threshold=confidence_threshold)
        print(f"     Genome fragments: {len(fragments)}")
    else:
        print(f"     ⚠️  No genome available — skipping validation")
        return ValidationReport(
            total_entries=len(entries),
            entries_with_matches=0,
            total_matches=0,
            matches=[],
            categories_found={}
        )

    for entry in entries:
        entry_matches = validate_entry(entry, genome, confidence_threshold)
        if entry_matches:
            entries_with_matches += 1
            all_matches.extend(entry_matches)

            for match in entry_matches:
                categories_found[match.category] = categories_found.get(match.category, 0) + 1

    report = ValidationReport(
        total_entries=len(entries),
        entries_with_matches=entries_with_matches,
        total_matches=len(all_matches),
        matches=all_matches,
        categories_found=categories_found
    )

    print(report.summary())

    return report


def validate_file(
    file_path: str,
    genome: Genome,
    lang: str,
    confidence_threshold: float = 0.6
) -> ValidationReport:
    """
    Validate a devotional JSON file against genome patterns.

    Args:
        file_path: Path to devotional JSON file
        genome: Genome with patterns
        lang: Language code
        confidence_threshold: Minimum confidence

    Returns:
        ValidationReport with matches found
    """
    from source import extract_entries

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    entries = extract_entries(data, lang)

    # Extract version and year from entries
    version = entries[0].version if entries else "unknown"
    year = int(entries[0].date[:4]) if entries else 2025

    return validate_entries(entries, genome, lang, version, year, confidence_threshold)


def export_report_to_file(report: ValidationReport, output_path: str):
    """Export validation report to JSON file."""
    data = {
        "total_entries": report.total_entries,
        "entries_with_matches": report.entries_with_matches,
        "total_matches": report.total_matches,
        "categories_found": report.categories_found,
        "matches": [
            {
                "entry_id": m.entry_id,
                "entry_date": m.entry_date,
                "field": m.field,
                "fragment_id": m.fragment_id,
                "category": m.category,
                "pattern": m.pattern,
                "matched_text": m.matched_text,
                "confidence": m.confidence,
            }
            for m in report.matches
        ]
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  💾 Report exported to: {output_path}")


# ── CLI Entry Point ────────────────────────────────────────────────────────────

def main():
    """CLI entry point for genome validation."""
    import argparse
    import sys
    from genome import load_genome

    parser = argparse.ArgumentParser(
        description="GEP Genome Validator — validate devotional files against genome patterns"
    )
    parser.add_argument("--file", required=True, help="Path to devotional JSON file")
    parser.add_argument("--lang", required=True, help="Language code (e.g., es, pt, fil)")
    parser.add_argument("--version", required=True, help="Bible version (e.g., RVR1960, NVI)")
    parser.add_argument("--year", type=int, required=True, help="Year (e.g., 2025, 2026)")
    parser.add_argument("--threshold", type=float, default=0.6,
                        help="Confidence threshold (default: 0.6)")
    parser.add_argument("--export", help="Export report to JSON file")

    args = parser.parse_args()

    # Load genome
    genome = load_genome(args.lang, args.version, args.year)
    if not genome:
        print(f"  ⚠️  No genome found for {args.lang}/{args.version}/{args.year}")
        print(f"     Validation will skip genome checks")

    # Validate file
    try:
        report = validate_file(args.file, genome, args.lang, args.threshold)

        # Export if requested
        if args.export:
            export_report_to_file(report, args.export)

        sys.exit(0 if report.total_matches == 0 else 1)

    except Exception as e:
        print(f"  ❌ Validation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

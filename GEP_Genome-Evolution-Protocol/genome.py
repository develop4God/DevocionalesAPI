"""
genome.py — GEP Critic v3
Single responsibility: load, update, and persist the GEP genome.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from models import (
    Genome, GenomeFragment, GeneState, PauseCategory, ReaderReaction, Verdict
)

# Minimum confidence a fragment needs to appear in prompts
CONFIDENCE_SEED    = 0.4   # first time a pattern is seen
CONFIDENCE_BOOST   = 0.15  # each additional evidence date
CONFIDENCE_MAX     = 0.95


import paths as _paths


def genome_path(lang: str, version: str, year: int) -> Path:
    _paths.ensure_dirs()
    return _paths.GENOMES_DIR / f"genome_{lang}_{version}_{year}.json"


def load_genome(lang: str, version: str, year: int) -> Genome | None:
    path = genome_path(lang, version, year)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    fragments = [
        GenomeFragment(
            id=fr["id"],
            language=fr["language"],
            version=fr["version"],
            category=PauseCategory(fr["category"]),
            pattern=fr["pattern"],
            example_quote=fr["example_quote"],
            evidence_dates=fr["evidence_dates"],
            confidence=fr["confidence"],
            created_at=fr["created_at"],
            updated_at=fr["updated_at"],
        )
        for fr in raw.get("fragments", [])
    ]
    return Genome(
        language=raw["language"],
        version=raw["version"],
        genome_version=raw["genome_version"],
        fragments=fragments,
        total_entries_reviewed=raw.get("total_entries_reviewed", 0),
        total_pauses=raw.get("total_pauses", 0),
        updated_at=raw.get("updated_at", ""),
    )


def save_genome(genome: Genome, year: int):
    path = genome_path(genome.language, genome.version, year)
    genome.updated_at = datetime.now(timezone.utc).isoformat()
    data = {
        "language": genome.language,
        "version": genome.version,
        "genome_version": genome.genome_version,
        "total_entries_reviewed": genome.total_entries_reviewed,
        "total_pauses": genome.total_pauses,
        "updated_at": genome.updated_at,
        "fragments": [
            {
                "id": f.id,
                "language": f.language,
                "version": f.version,
                "category": f.category.value,
                "pattern": f.pattern,
                "example_quote": f.example_quote,
                "evidence_dates": f.evidence_dates,
                "confidence": f.confidence,
                "state": f.state.value,
                "created_at": f.created_at,
                "updated_at": f.updated_at,
            }
            for f in genome.fragments
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _promote_pending(genome: Genome):
    """Promote any fragment already at or above threshold."""
    for f in genome.fragments:
        if f.confidence >= 0.7 and f.state != GeneState.CONFIRMED:
            f.state = GeneState.CONFIRMED


def ensure_genome(lang: str, version: str, year: int) -> Genome:
    genome = load_genome(lang, version, year)
    if genome:
        _promote_pending(genome)
        return genome
    return Genome(
        language=lang,
        version=version,
        genome_version=f"{lang}-{version}-{year}-v1",
        fragments=[],
        total_entries_reviewed=0,
        total_pauses=0,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def absorb_reaction(
    genome: Genome,
    reaction: ReaderReaction,
    date: str,
    year: int,
) -> tuple[Genome, str | None]:
    """
    Absorbs a PAUSE reaction into the genome.
    Returns (updated_genome, fragment_id | None).

    GEP logic:
    - If a similar fragment exists (same category + similar quote) → boost confidence
    - Otherwise → create new fragment at seed confidence
    """
    genome.total_entries_reviewed += 1

    if reaction.verdict != Verdict.PAUSE or not reaction.quoted_pause:
        return genome, None

    genome.total_pauses += 1
    category = reaction.category or PauseCategory.OTHER
    quote = reaction.quoted_pause.strip()

    # Try to find an existing fragment in the same category with overlapping quote
    existing = _find_similar_fragment(genome, category, quote)

    now = datetime.now(timezone.utc).isoformat()

    if existing:
        if date not in existing.evidence_dates:
            existing.evidence_dates.append(date)
        existing.confidence = min(
            existing.confidence + CONFIDENCE_BOOST, CONFIDENCE_MAX
        )
        if existing.confidence >= 0.7:
            existing.state = GeneState.CONFIRMED
        existing.updated_at = now
        save_genome(genome, year)
        return genome, existing.id

    # New fragment
    frag_id = f"frag_{uuid.uuid4().hex[:8]}"
    fragment = GenomeFragment(
        id=frag_id,
        language=genome.language,
        version=genome.version,
        category=category,
        pattern=reaction.reaction,       # reader's own words become the pattern
        example_quote=quote,
        evidence_dates=[date],
        confidence=CONFIDENCE_SEED,
        created_at=now,
        updated_at=now,
    )
    genome.fragments.append(fragment)
    save_genome(genome, year)
    return genome, frag_id


def _find_similar_fragment(
    genome: Genome,
    category: PauseCategory,
    quote: str,
) -> GenomeFragment | None:
    """Word-overlap check: same category + shared words > 40%.

    Always requires word overlap regardless of fragment confidence.
    High-confidence fragments do NOT absorb all same-category PAUSEs —
    distinct quotes create new fragments so the genome keeps learning.
    Returns the best-matching fragment (highest overlap), or None.
    """
    quote_words = set(quote.lower().split())
    best: GenomeFragment | None = None
    best_overlap = 0.0
    for frag in genome.fragments:
        if frag.category != category:
            continue
        frag_words = set(frag.example_quote.lower().split())
        if not quote_words or not frag_words:
            continue
        overlap = len(quote_words & frag_words) / min(len(quote_words), len(frag_words))
        if overlap >= 0.4 and overlap > best_overlap:
            best_overlap = overlap
            best = frag
    return best

def get_saved_genomes() -> list[str]:
    """Returns list of genome JSON filenames found in the current working directory."""
    from pathlib import Path
    return [str(p) for p in Path(".").glob("genome_*.json")]


def corpus_scan(
    genome: Genome,
    source_paths: list[Path],
    categories: list[str] | None = None,
) -> dict[str, list[dict]]:
    """
    Scan source devotional files for known genome patterns.
    Returns: {fragment_id: [{entry_id, date, field, context}]}

    Args:
        genome:       The genome to scan with.
        source_paths: List of source JSON files to scan.
        categories:   Filter to specific categories (e.g. ["grammar", "typo"]).
                      None = scan all fragments.
    """
    fragments = genome.fragments
    if categories:
        fragments = [f for f in fragments if f.category.value in categories]

    results: dict[str, list[dict]] = {f.id: [] for f in fragments}

    for source_path in source_paths:
        with open(source_path, encoding="utf-8") as fp:
            data = json.load(fp)

        lang_data = data.get("data", {})
        first_val = next(iter(lang_data.values()), {})
        date_map = first_val if isinstance(first_val, dict) else lang_data

        for date_key, entries in date_map.items():
            for entry in entries:
                eid = entry.get("id", "")
                date = entry.get("date", date_key)
                for field in ("reflexion", "oracion"):
                    text = entry.get(field, "") or ""
                    for fragment in fragments:
                        pattern = fragment.example_quote
                        if pattern in text:
                            idx = text.find(pattern)
                            context = text[max(0, idx - 40): idx + len(pattern) + 60]
                            results[fragment.id].append({
                                "entry_id": eid,
                                "date": date,
                                "field": field,
                                "context": f"...{context}...",
                            })

    return results


def print_corpus_scan_report(scan_results: dict, genome: Genome) -> None:
    """Pretty-print corpus_scan() results."""
    fragment_map = {f.id: f for f in genome.fragments}
    total_hits = sum(len(v) for v in scan_results.values())
    print(f"\n{'═'*60}")
    print(f"  🧬 Genome Corpus Scan — {total_hits} total hits")
    print(f"{'═'*60}")
    for fid, hits in scan_results.items():
        if not hits:
            continue
        fr = fragment_map.get(fid)
        if not fr:
            continue
        print(f"\n  [{fr.category.value}] \"{fr.example_quote}\"")
        print(f"  Pattern: {fr.pattern[:80]}")
        print(f"  Hits: {len(hits)}")
        for hit in hits:
            print(f"    • {hit['date']} [{hit['field']}] {hit['entry_id']}")
            print(f"      {hit['context']}")
    print(f"\n{'═'*60}\n")

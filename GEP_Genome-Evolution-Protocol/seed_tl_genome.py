"""
seed_tl_genome.py — GEP Critic v3
Injects 3 manually confirmed genome fragments into the TL genome
before the first overnight run. Source: 8 Sonnet 4.6 entries reviewed
by human + Ana persona on 2026-04-18.

Usage:
    python3 seed_tl_genome.py

Run ONCE before first critic run on TL. Idempotent — checks for
existing fragments before inserting.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from models import Genome, GenomeFragment, PauseCategory

LANG    = "tl"
VERSIONS = ["ASND", "ADB"]
REVIEW_DATE = "2026-04-18"

# ── 3 confirmed fragments from manual review ──────────────────────────────────

SEED_FRAGMENTS = [
    GenomeFragment(
        id="seed_tl_001",
        language="tl",
        version="*",                        # applies to all TL versions
        category=PauseCategory.TYPO,
        pattern=(
            "Sonnet uses the wrong verb form when the correct form appears in the "
            "verse itself. Example: verse says 'mamumunga' but reflection writes "
            "'makakapag-produce ng prutas' — English mix when pure Tagalog exists."
        ),
        example_quote="hindi na makakapag-produce ng prutas",
        evidence_dates=[REVIEW_DATE],
        confidence=0.85,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    ),
    GenomeFragment(
        id="seed_tl_002",
        language="tl",
        version="*",
        category=PauseCategory.REGISTER_DRIFT,
        pattern=(
            "Prayer register inconsistency: shifts between formal plural address "
            "to God (kayo/inyo) and intimate singular (mo/iyong) within the same "
            "prayer. Filipino Christian devotional prayer should use one register "
            "consistently — intimate (mo/iyong) is strongly preferred."
        ),
        example_quote="Huwag mo kaming hayaang maligaw... Ang inyong pakikipag-ugnayan",
        evidence_dates=[REVIEW_DATE],
        confidence=0.88,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    ),
    GenomeFragment(
        id="seed_tl_003",
        language="tl",
        version="*",
        category=PauseCategory.REGISTER_DRIFT,
        pattern=(
            "Unnatural reduplication or over-compounding of words that no native "
            "Tagalog writer would produce. Sounds machine-generated. "
            "Example: 'pagkakabukod-bukod' instead of 'paghihiwalay' or 'pagbubukod'."
        ),
        example_quote="labis na pagkakabukod-bukod ng isang tao",
        evidence_dates=[REVIEW_DATE],
        confidence=0.80,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    ),
]


def genome_path(lang: str, version: str) -> Path:
    """Matches the path convention genome.py uses."""
    return Path(f"genome_{lang}_{version}.json")


def load_genome(path: Path, lang: str, version: str) -> Genome:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
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
            for fr in data.get("fragments", [])
        ]
        return Genome(
            language=data["language"],
            version=data["version"],
            genome_version=data["genome_version"],
            fragments=fragments,
            total_entries_reviewed=data.get("total_entries_reviewed", 0),
            total_pauses=data.get("total_pauses", 0),
            updated_at=data.get("updated_at", ""),
        )
    return Genome(
        language=lang,
        version=version,
        genome_version=f"{lang}-{version}-seed-v1",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def save_genome(genome: Genome, path: Path):
    data = {
        "language": genome.language,
        "version": genome.version,
        "genome_version": genome.genome_version,
        "total_entries_reviewed": genome.total_entries_reviewed,
        "total_pauses": genome.total_pauses,
        "updated_at": datetime.now(timezone.utc).isoformat(),
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
                "created_at": f.created_at,
                "updated_at": f.updated_at,
            }
            for f in genome.fragments
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def seed_version(version: str):
    path = genome_path(LANG, version)
    genome = load_genome(path, LANG, version)

    existing_ids = {f.id for f in genome.fragments}
    added = 0

    for frag in SEED_FRAGMENTS:
        if frag.id in existing_ids:
            print(f"  ⏭️  [{version}] {frag.id} already exists — skipped")
            continue
        # Clone with correct version
        clone = GenomeFragment(
            id=frag.id,
            language=LANG,
            version=version,
            category=frag.category,
            pattern=frag.pattern,
            example_quote=frag.example_quote,
            evidence_dates=list(frag.evidence_dates),
            confidence=frag.confidence,
            created_at=frag.created_at,
            updated_at=frag.updated_at,
        )
        genome.fragments.append(clone)
        added += 1
        print(f"  ✅ [{version}] {frag.id} — {frag.category.value} (confidence: {frag.confidence})")

    if added > 0:
        save_genome(genome, path)
        print(f"  💾 Saved → {path}  ({len(genome.fragments)} total fragments)\n")
    else:
        print(f"  ✅ [{version}] Nothing to add — genome already seeded\n")


# ── Run ───────────────────────────────────────────────────────────────────────
print(f"\n{'═'*60}")
print(f"  🧬 TL Genome Seed — {REVIEW_DATE}")
print(f"  Fragments to inject: {len(SEED_FRAGMENTS)}")
print(f"  Versions: {', '.join(VERSIONS)}")
print(f"{'═'*60}\n")

for v in VERSIONS:
    seed_version(v)

print("  Done. Run critic_v3.py --genome tl ASND 2025 to verify.\n")

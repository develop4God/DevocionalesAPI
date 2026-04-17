"""
audit.py — GEP Critic v3
Single responsibility: read and write the JSONL audit log.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from models import AuditRecord, ReaderReaction, Verdict


def audit_path(lang: str, version: str, year: int) -> Path:
    return Path(f"critic_audit_{lang}_{version}_{year}.jsonl")


def load_reviewed_dates(log_path: Path) -> set[str]:
    reviewed = set()
    if not log_path.exists():
        return reviewed
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                reviewed.add(json.loads(line)["date"])
            except (json.JSONDecodeError, KeyError):
                pass
    return reviewed


def compute_asset_id(record: dict) -> str:
    """GEP-compliant SHA-256 content-addressable ID."""
    clean = {k: v for k, v in record.items() if k != "asset_id"}
    canonical = json.dumps(clean, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def append_record(log_path: Path, record: AuditRecord):
    row = {
        "date": record.date,
        "id": record.id,
        "language": record.language,
        "version": record.version,
        "reviewed_at": record.reviewed_at,
        "action": record.action,
        "verdict": record.verdict.value,
        "reaction": record.reaction,
        "quoted_pause": record.quoted_pause,
        "category": record.category,
        "confidence": record.confidence,
        "genome_fragment_id": record.genome_fragment_id,
        "raw_response": record.raw_response,
    }
    row["asset_id"] = compute_asset_id(row)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_record(
    entry_date: str,
    entry_id: str,
    lang: str,
    version: str,
    action: str,
    reaction: ReaderReaction,
    genome_fragment_id: str | None = None,
    raw_response: str | None = None,
) -> AuditRecord:
    return AuditRecord(
        date=entry_date,
        id=entry_id,
        language=lang,
        version=version,
        reviewed_at=datetime.utcnow().isoformat(),
        action=action,
        verdict=reaction.verdict,
        reaction=reaction.reaction,
        quoted_pause=reaction.quoted_pause,
        category=reaction.category.value if reaction.category else None,
        confidence=reaction.confidence,
        genome_fragment_id=genome_fragment_id,
        raw_response=raw_response,
    )


def print_summary(log_path: Path):
    if not log_path.exists():
        print("  No audit log found.")
        return

    total = ok = pauses = errors = 0
    by_category: dict[str, int] = {}
    pause_dates = []

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                total += 1
                v = rec.get("verdict")
                if v == "OK":
                    ok += 1
                elif v == "PAUSE":
                    pauses += 1
                    cat = rec.get("category") or "other"
                    by_category[cat] = by_category.get(cat, 0) + 1
                    pause_dates.append((rec["date"], cat, rec.get("quoted_pause", "")[:60]))
                else:
                    errors += 1
            except (json.JSONDecodeError, KeyError):
                errors += 1

    print(f"\n{'═'*60}")
    print(f"  📊 AUDIT REPORT — {log_path.name}")
    print(f"{'═'*60}")
    print(f"  Total reviewed : {total}")
    print(f"  ✅ OK          : {ok}")
    print(f"  🔶 PAUSE       : {pauses}")
    print(f"  ❌ Errors      : {errors}")
    if by_category:
        print(f"\n  Pauses by category:")
        for cat, count in sorted(by_category.items(), key=lambda x: -x[1]):
            print(f"    {cat:<22} {count}")
    if pause_dates:
        print(f"\n  Entries needing review:")
        for d, cat, quote in pause_dates:
            print(f"    • {d}  [{cat}]  \"{quote}\"")
    print(f"{'═'*60}\n")

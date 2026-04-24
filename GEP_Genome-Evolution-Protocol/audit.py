"""
audit.py — GEP Critic v3
Single responsibility: read and write the JSONL audit log.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from models import AuditRecord, ReaderReaction, Verdict
import paths as _paths


def audit_path(lang: str, version: str, year: int, role: str = "default") -> Path:
    _paths.ensure_dirs()
    suffix = f"_{role}" if role != "default" else ""
    return _paths.AUDIT_DIR / f"critic_audit_{lang}_{version}_{year}{suffix}.jsonl"


def _split_thinking(raw: str | None) -> tuple[str | None, str | None]:
    """Returns (thinking, verdict_raw) split from a raw model response."""
    if not raw:
        return None, None
    import re

    m = re.search(r"<think>(.*?)</think>(.*)", raw, re.DOTALL)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, raw.strip()


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
                rec = json.loads(line)
                if rec.get("action") == "reviewed":
                    reviewed.add(rec["date"])
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
        "phase1_verdict": record.phase1_verdict,
        "phase1_issue": record.phase1_issue,
        "phase1_quoted": record.phase1_quoted,
        "phase1_confidence": record.phase1_confidence,
        "phase1_raw": record.phase1_raw,
    }
    row["asset_id"] = compute_asset_id(row)
    row["suggested_reflexion"] = record.suggested_reflexion
    row["suggested_oracion"] = record.suggested_oracion
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
    phase1_verdict: str | None = None,
    phase1_issue: str | None = None,
    phase1_quoted: str | None = None,
    phase1_confidence: float | None = None,
    phase1_raw: str | None = None,
    suggested_reflexion: str | None = None,
    suggested_oracion: str | None = None,
) -> AuditRecord:
    p1_thinking, p1_verdict_raw = _split_thinking(phase1_raw)
    p2_thinking, p2_verdict_raw = _split_thinking(raw_response)
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
        phase1_verdict=phase1_verdict,
        phase1_issue=phase1_issue,
        phase1_quoted=phase1_quoted,
        phase1_confidence=phase1_confidence,
        phase1_raw=phase1_raw,
        phase1_thinking=p1_thinking,
        phase1_verdict_raw=p1_verdict_raw,
        p2_thinking=p2_thinking,
        p2_verdict_raw=p2_verdict_raw,
        suggested_reflexion=suggested_reflexion,
        suggested_oracion=suggested_oracion,
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

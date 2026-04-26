"""
collect_batch.py — GEP Critic v3
Single responsibility: parse batch results → write AuditRecord JSONL.

Supports both Fireworks and DashScope (OpenAI-compatible) result formats.

Usage:
    python3 collect_batch.py \
        --input  batch_input_tl_ASND_2026.jsonl \
        --results output.jsonl \
        --lang tl --version ASND --year 2026
    python3 collect_batch.py \
        --input  batch_input_es_RVR1960_2025_p1.jsonl \
        --results output.jsonl \
        --lang es --version RVR1960 --year 2025 --phase 1

Callable from batch_pipeline.py via process_results().

Output:
    Appends to critic_audit_{lang}_{version}_{year}.jsonl
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Load .env for local development (optional)
try:
    from dotenv import load_dotenv
    from pathlib import Path as _Path
    load_dotenv(dotenv_path=_Path(__file__).parent / ".env")
except ImportError:
    pass

# ── Project imports ───────────────────────────────────────────────────────────
from audit import audit_path, append_record, build_record
from cloud_client import _parse_reaction
from models import PauseCategory, ReaderReaction, Verdict
from genome import absorb_reaction, ensure_genome, save_genome


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_input_index(input_path: Path) -> dict[str, dict]:
    """
    Build index: custom_id → {date, id, lang, version} from batch input JSONL.
    Needed to recover date + entry metadata from custom_id.
    """
    index: dict[str, dict] = {}
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                custom_id = rec.get("custom_id", "")
                if not custom_id:
                    continue
                # Extract date from user prompt content using regex
                messages = rec.get("body", {}).get("messages", [])
                date = ""
                for msg in messages:
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            m = re.search(r"Date:\s*(\d{4}-\d{2}-\d{2})", content)
                            if m:
                                date = m.group(1)
                        break
                index[custom_id] = {
                    "id": custom_id,
                    "date": date,
                }
            except (json.JSONDecodeError, KeyError):
                continue
    return index


def load_source_index(source_path: Path) -> dict[str, str]:
    """
    Build index: entry_id → full_text (reflexion + oracion) from source JSON.
    Used as verbatim gate before any FLAG is written to audit log.
    """
    if not source_path or not source_path.exists():
        return {}
    with open(source_path, encoding="utf-8") as f:
        data = json.load(f)
    index: dict[str, str] = {}
    lang_data = data.get("data", {})
    # data.data is {lang: {date: [entries]}} or {date: [entries]}
    if lang_data:
        first_val = next(iter(lang_data.values()))
        date_map = first_val if isinstance(first_val, dict) else lang_data
        for date_entries in date_map.values():
            for entry in date_entries:
                eid = entry.get("id", "")
                if eid:
                    full = (entry.get("reflexion") or "") + " " + (entry.get("oracion") or "")
                    index[eid] = full
    return index


def extract_content(result_line: dict) -> str | None:
    """
    Extract raw model content from batch result line.
    Handles two response shapes:
      - Fireworks: response.choices[0].message.content
      - DashScope:  response.body.choices[0].message.content
    """
    resp = result_line.get("response", {})
    # DashScope wraps content under response.body
    body = resp.get("body") or resp
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


# ── Phase 1 parser ────────────────────────────────────────────────────────────

_P1_CATEGORY_MAP: dict[str, PauseCategory] = {
    "repetition": PauseCategory.REPETITION,
    "typo":       PauseCategory.TYPO,
    "grammar":    PauseCategory.GRAMMAR,
    "other":      PauseCategory.OTHER,
}

def _parse_phase1_reaction(raw: str) -> ReaderReaction | None:
    """
    Parse a Phase 1 CLEAN/FLAG JSON verdict into a ReaderReaction.
    P1 schema: {verdict: "CLEAN"|"FLAG", issue: str|null,
                quoted_problem: str|null, confidence: float,
                category: str|null}
    Maps FLAG → Verdict.PAUSE, CLEAN → Verdict.OK.
    Returns None if JSON is unparseable.
    """
    # Fireworks returns content starting mid-think, closing </think> only.
    # re.sub() strips nothing — must split on </think>.
    if "</think>" in raw:
        text = raw.split("</think>")[-1].strip()
    else:
        # Truncated — model hit token limit before producing JSON
        return None
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    verdict_raw = (data.get("verdict") or "").strip().upper()
    is_flag     = verdict_raw == "FLAG"
    verdict     = Verdict.PAUSE if is_flag else Verdict.OK

    cat_raw  = (data.get("category") or "").strip().lower()
    category = _P1_CATEGORY_MAP.get(cat_raw) if is_flag else None
    if is_flag and category is None:
        category = PauseCategory.OTHER

    return ReaderReaction(
        verdict      = verdict,
        reaction     = (data.get("issue") or "").strip(),
        quoted_pause = data.get("quoted_problem") or None,
        category     = category,
        confidence   = float(data.get("confidence") or 1.0),
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def process_results(
    input_path: Path,
    results_path: Path,
    lang: str,
    version: str,
    year: int,
    dry_run: bool = False,
    overwrite: bool = False,
    phase: int = 2,
    source_path: Path | None = None,
) -> dict:
    """
    Parse batch results and write AuditRecord JSONL.
    Returns summary dict: {"ok": int, "paused": int, "errors": int, "skipped": int}.
    phase=1 → expects CLEAN/FLAG schema; phase=2 → expects OK/PAUSE schema (default).
    Callable from batch_pipeline.py or standalone via main().
    """
    log_path = audit_path(lang, version, year)

    # Load existing audit into memory for dedup (id → verdict)
    existing: dict[str, str] = {}
    if log_path.exists() and not overwrite:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        r = json.loads(line)
                        existing[r["id"]] = (
                            r.get("action")
                            if r.get("action") == "error_parse"
                            else r.get("verdict", "error_parse")
                        )
                    except Exception:
                        pass

    # Load genome for PAUSE absorption
    genome = ensure_genome(lang, version, year)
    genome_dirty = False

    # Load source index for verbatim gate (Phase 1 only)
    source_index = load_source_index(source_path) if source_path else {}
    if phase == 1 and not source_index:
        print("  ⚠️  No --source provided for Phase 1 — verbatim gate disabled. Hallucinated flags will not be filtered.")

    print(f"\n{'═'*60}")
    print(f"  📥  GEP Batch Collector")
    print(f"  Lang: {lang} | Version: {version} | Year: {year}")
    print(f"  Input  : {input_path}")
    print(f"  Results: {results_path}")
    print(f"  Audit  : {log_path}")
    if dry_run:
        print(f"  ⚠️  DRY RUN — nothing will be written")
    if phase == 1:
        print(f"  🔬 Phase 1 mode — CLEAN/FLAG schema")
    print(f"{'═'*60}")

    print(f"  📋 Loading input index...")
    input_index = load_input_index(input_path)
    print(f"  ✅ {len(input_index)} entries indexed")

    total = ok = paused = errors = skipped = 0

    with open(results_path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                print(f"  ⚠️  Line {line_num}: invalid JSON — skipping")
                skipped += 1
                continue

            custom_id = result.get("custom_id", "")
            if not custom_id:
                print(f"  ⚠️  Line {line_num}: missing custom_id — skipping")
                skipped += 1
                continue

            entry_meta = input_index.get(custom_id)
            if not entry_meta:
                print(f"  ⚠️  Line {line_num}: custom_id '{custom_id}' not in input index — skipping")
                skipped += 1
                continue

            raw_content = extract_content(result)
            if not raw_content:
                if existing.get(custom_id) == "error_parse":
                    skipped += 1
                    continue
                print(f"  ❌  {custom_id}: empty/missing content — logging as error_parse")
                if not dry_run:
                    err_reaction = ReaderReaction(
                        verdict=Verdict.OK,
                        reaction="[collect_batch] Empty response from batch provider.",
                        quoted_pause=None,
                        category=None,
                        confidence=0.0,
                    )
                    record = build_record(
                        entry_date=entry_meta["date"],
                        entry_id=custom_id,
                        lang=lang,
                        version=version,
                        action="error_parse",
                        reaction=err_reaction,
                        raw_response=None,
                    )
                    append_record(log_path, record)
                errors += 1
                continue

            if phase == 1:
                reaction = _parse_phase1_reaction(raw_content)
            else:
                reaction = _parse_reaction(raw_content)

            if reaction is None:
                if existing.get(custom_id) == "error_parse":
                    skipped += 1
                    continue
                print(f"  ❌  {custom_id}: failed to parse JSON verdict — logging as error_parse")
                if not dry_run:
                    err_reaction = ReaderReaction(
                        verdict=Verdict.OK,
                        reaction="[collect_batch] Could not parse JSON from model response.",
                        quoted_pause=None,
                        category=None,
                        confidence=0.0,
                    )
                    record = build_record(
                        entry_date=entry_meta["date"],
                        entry_id=custom_id,
                        lang=lang,
                        version=version,
                        action="error_parse",
                        reaction=err_reaction,
                        raw_response=raw_content,
                    )
                    append_record(log_path, record)
                errors += 1
                continue

            # ── Verbatim gate (Phase 1 only) ─────────────────────────────
            # Discard any flags whose quoted_problem is not found verbatim
            # in the source entry. These are model hallucinations.
            if phase == 1 and source_index and reaction and reaction.verdict == Verdict.PAUSE:
                source_text = source_index.get(custom_id, "")
                if source_text:
                    original_pause = reaction.quoted_pause or ""
                    if original_pause and original_pause not in source_text:
                        print(
                            f"  🚫  {custom_id}: quoted_problem not in source "
                            f"(hallucinated) — downgraded to CLEAN"
                        )
                        # Downgrade to CLEAN — do not write as flagged
                        reaction = ReaderReaction(
                            verdict=Verdict.OK,
                            reaction=f"[gate] Hallucinated flag discarded: \"{original_pause[:60]}\"",
                            quoted_pause=None,
                            category=None,
                            confidence=0.0,
                        )
            # ─────────────────────────────────────────────────────────────

            action = "flagged" if reaction.verdict.value == "PAUSE" else "reviewed"

            prior = existing.get(custom_id)
            if prior and prior != "error_parse":
                skipped += 1
                continue
            if prior == "error_parse" and action == "error_parse":
                skipped += 1
                continue

            if not dry_run:
                if prior == "error_parse":
                    lines = log_path.read_text(encoding="utf-8").splitlines()
                    with open(log_path, "w", encoding="utf-8") as fw:
                        for l in lines:
                            if l.strip():
                                try:
                                    if json.loads(l).get("id") != custom_id:
                                        fw.write(l + "\n")
                                except Exception:
                                    fw.write(l + "\n")
                    existing[custom_id] = reaction.verdict.value

                record = build_record(
                    entry_date=entry_meta["date"],
                    entry_id=custom_id,
                    lang=lang,
                    version=version,
                    action=action,
                    reaction=reaction,
                    raw_response=raw_content,
                )
                append_record(log_path, record)
                if action == "flagged" and reaction.category and reaction.quoted_pause:
                    genome, _ = absorb_reaction(genome, reaction, entry_meta["date"], year)
                    genome_dirty = True

            total += 1
            if action == "flagged":
                paused += 1
                print(
                    f"  🔶  {entry_meta['date']} [{reaction.category.value if reaction.category else '?'}] "
                    f"conf={reaction.confidence:.2f}  \"{(reaction.quoted_pause or '')[:60]}\""
                )
            else:
                ok += 1

    if genome_dirty and not dry_run:
        save_genome(genome, year)
        print(f"  🧬 Genome updated: {len(genome.fragments)} fragments")

    print(f"\n{'═'*60}")
    print(f"  📊 Collection complete")
    print(f"  ✅ OK      : {ok}")
    print(f"  🔶 PAUSE   : {paused}")
    print(f"  ❌ Errors  : {errors}")
    print(f"  ⏭️  Skipped : {skipped}")
    print(f"  Total      : {total + errors + skipped}")
    if not dry_run and total > 0:
        print(f"  💾 Written to: {log_path}")
    print(f"{'═'*60}\n")

    return {"ok": ok, "paused": paused, "errors": errors, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description="GEP Critic — Collect Batch Results")
    parser.add_argument("--input",   required=True, metavar="FILE",
                        help="Original batch input JSONL")
    parser.add_argument("--results", required=True, metavar="FILE",
                        help="Batch output JSONL (downloaded results file)")
    parser.add_argument("--lang",    required=True, help="Language code (tl, es, ...)")
    parser.add_argument("--version", required=True, help="Bible version (ASND, NVI, ...)")
    parser.add_argument("--year",    required=True, type=int, help="Year (2025, 2026, ...)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and report only — do not write audit log")
    parser.add_argument("--overwrite", action="store_true",
                        help="Ignore existing audit entries and re-collect from scratch.")
    parser.add_argument("--phase", type=int, default=2, choices=[1, 2],
                        help="Phase whose results are being collected: 1 (CLEAN/FLAG) or 2 (OK/PAUSE). Default: 2")
    parser.add_argument("--source", metavar="FILE", default=None,
                        help="Source devotional JSON (required for Phase 1 verbatim gate).")
    args = parser.parse_args()

    process_results(
        input_path=Path(args.input),
        results_path=Path(args.results),
        lang=args.lang,
        version=args.version,
        year=args.year,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        phase=args.phase,
        source_path=Path(args.source) if args.source else None,
    )


if __name__ == "__main__":
    main()


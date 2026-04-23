"""
collect_batch.py — GEP Critic v3
Single responsibility: parse Fireworks batch results → write AuditRecord JSONL.

Usage:
    python3 collect_batch.py \
        --input  batch_input_tl_ASND_2026.jsonl \
        --results fireworks_output.jsonl \
        --lang tl --version ASND --year 2026

Output:
    Appends to critic_audit_tl_ASND_2026.jsonl

Fireworks output format (one line per request):
    {
      "custom_id": "<entry.id>",
      "response": {
        "body": {
          "choices": [{"message": {"content": "<raw model output>"}}]
        }
      }
    }
"""

import argparse
import json
import sys
from pathlib import Path

# ── Project imports ───────────────────────────────────────────────────────────
from audit import audit_path, append_record, build_record
from cloud_client import _parse_reaction
from models import ReaderReaction, Verdict
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
                # Extract date from user prompt content
                messages = rec.get("body", {}).get("messages", [])
                date = ""
                for msg in messages:
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        if isinstance(content, str) and content.startswith("Date:"):
                            date = content.split("\n")[0].replace("Date:", "").strip()
                        break
                index[custom_id] = {
                    "id": custom_id,
                    "date": date,
                }
            except (json.JSONDecodeError, KeyError):
                continue
    return index


def extract_content(result_line: dict) -> str | None:
    """Extract raw model content from Fireworks result line."""
    try:
        return (
            result_line["response"]["choices"][0]["message"]["content"]
        )
    except (KeyError, IndexError, TypeError):
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GEP Critic — Collect Fireworks Batch Results")
    parser.add_argument("--input",   required=True, metavar="FILE",
                        help="Original batch input JSONL (batch_input_tl_ASND_2026.jsonl)")
    parser.add_argument("--results", required=True, metavar="FILE",
                        help="Fireworks batch output JSONL (downloaded results file)")
    parser.add_argument("--lang",    required=True, help="Language code (tl, es, ...)")
    parser.add_argument("--version", required=True, help="Bible version (ASND, NVI, ...)")
    parser.add_argument("--year",    required=True, type=int, help="Year (2025, 2026, ...)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and report only — do not write audit log")
    args = parser.parse_args()

    input_path   = Path(args.input)
    results_path = Path(args.results)
    log_path     = audit_path(args.lang, args.version, args.year)

    # Load existing audit into memory for dedup (id → verdict)
    existing: dict[str, str] = {}
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        r = json.loads(line)
                        existing[r["id"]] = r.get("action") if r.get("action") == "error_parse" else r.get("verdict", "error_parse")
                    except Exception:
                        pass

    # Load genome for PAUSE absorption
    genome = ensure_genome(args.lang, args.version, args.year)
    genome_dirty = False

    print(f"\n{'═'*60}")
    print(f"  📥  GEP Batch Collector")
    print(f"  Lang: {args.lang} | Version: {args.version} | Year: {args.year}")
    print(f"  Input : {input_path}")
    print(f"  Results: {results_path}")
    print(f"  Audit  : {log_path}")
    if args.dry_run:
        print(f"  ⚠️  DRY RUN — nothing will be written")
    print(f"{'═'*60}")

    # Load input index for metadata lookup
    print(f"  📋 Loading input index...")
    input_index = load_input_index(input_path)
    print(f"  ✅ {len(input_index)} entries indexed")

    # Process results
    total = ok = paused = errors = skipped = 0

    with open(results_path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            # Parse result line
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

            # Look up entry metadata
            entry_meta = input_index.get(custom_id)
            if not entry_meta:
                print(f"  ⚠️  Line {line_num}: custom_id '{custom_id}' not in input index — skipping")
                skipped += 1
                continue

            # Extract raw model content
            raw_content = extract_content(result)
            if not raw_content:
                print(f"  ❌  {custom_id}: empty/missing content — logging as error_parse")
                if not args.dry_run:
                    err_reaction = ReaderReaction(
                        verdict=Verdict.OK,
                        reaction="[collect_batch] Empty response from Fireworks.",
                        quoted_pause=None,
                        category=None,
                        confidence=0.0,
                    )
                    record = build_record(
                        entry_date=entry_meta["date"],
                        entry_id=custom_id,
                        lang=args.lang,
                        version=args.version,
                        action="error_parse",
                        reaction=err_reaction,
                        raw_response=None,
                    )
                    append_record(log_path, record)
                errors += 1
                continue

            # Parse reaction from content
            reaction = _parse_reaction(raw_content)
            if reaction is None:
                print(f"  ❌  {custom_id}: failed to parse JSON verdict — logging as error_parse")
                if not args.dry_run:
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
                        lang=args.lang,
                        version=args.version,
                        action="error_parse",
                        reaction=err_reaction,
                        raw_response=raw_content,
                    )
                    append_record(log_path, record)
                errors += 1
                continue

            # Determine action
            action = "flagged" if reaction.verdict.value == "PAUSE" else "reviewed"

            # Dedup: skip if already audited with a good verdict
            prior = existing.get(custom_id)
            if prior and prior != "error_parse":
                skipped += 1
                continue

            # Also skip if we can't improve on existing error_parse
            if prior == "error_parse" and action == "error_parse":
                skipped += 1
                continue

            # Build and write record
            if not args.dry_run:
                # If replacing an error_parse, rewrite the whole file without it
                if prior == "error_parse":
                    lines = log_path.read_text(encoding="utf-8").splitlines()
                    with open(log_path, "w", encoding="utf-8") as f:
                        for l in lines:
                            if l.strip():
                                try:
                                    if json.loads(l).get("id") != custom_id:
                                        f.write(l + "\n")
                                except Exception:
                                    f.write(l + "\n")
                    existing[custom_id] = reaction.verdict.value

                record = build_record(
                    entry_date=entry_meta["date"],
                    entry_id=custom_id,
                    lang=args.lang,
                    version=args.version,
                    action=action,
                    reaction=reaction,
                    raw_response=raw_content,
                )
                append_record(log_path, record)
                if action == "flagged" and reaction.category and reaction.quoted_pause:
                    genome, _ = absorb_reaction(genome, reaction, entry_meta["date"], args.year)
                    genome_dirty = True

            total += 1
            if action == "flagged":
                paused += 1
                print(f"  🔶  {entry_meta['date']} [{reaction.category.value if reaction.category else '?'}] "
                      f"conf={reaction.confidence:.2f}  \"{(reaction.quoted_pause or '')[:60]}\"")
            else:
                ok += 1

    # Summary
    if genome_dirty and not args.dry_run:
        save_genome(genome, args.year)
        print(f"  🧬 Genome updated: {len(genome.fragments)} fragments")
    print(f"\n{'═'*60}")
    print(f"  📊 Collection complete")
    print(f"  ✅ OK      : {ok}")
    print(f"  🔶 PAUSE   : {paused}")
    print(f"  ❌ Errors  : {errors}")
    print(f"  ⏭️  Skipped : {skipped}")
    print(f"  Total      : {total + errors + skipped}")
    if not args.dry_run and total > 0:
        print(f"  💾 Written to: {log_path}")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()

"""
collect_batch.py — GEP Critic v3
Single responsibility: parse batch results → write AuditRecord JSONL.

Supports both Fireworks and DashScope (OpenAI-compatible) result formats.

Usage:
    python3 collect_batch.py \
        --input  batch_input_tl_ASND_2026.jsonl \
        --results output.jsonl \
        --lang tl --version ASND --year 2026

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


# ── Main ──────────────────────────────────────────────────────────────────────

def process_results(
    input_path: Path,
    results_path: Path,
    lang: str,
    version: str,
    year: int,
    dry_run: bool = False,
) -> dict:
    """
    Parse batch results and write AuditRecord JSONL.
    Returns summary dict: {"ok": int, "paused": int, "errors": int, "skipped": int}.
    Callable from batch_pipeline.py or standalone via main().
    """
    log_path = audit_path(lang, version, year)

    # Load existing audit into memory for dedup (id → verdict)
    existing: dict[str, str] = {}
    if log_path.exists():
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

    print(f"\n{'═'*60}")
    print(f"  📥  GEP Batch Collector")
    print(f"  Lang: {lang} | Version: {version} | Year: {year}")
    print(f"  Input  : {input_path}")
    print(f"  Results: {results_path}")
    print(f"  Audit  : {log_path}")
    if dry_run:
        print(f"  ⚠️  DRY RUN — nothing will be written")
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
    args = parser.parse_args()

    process_results(
        input_path=Path(args.input),
        results_path=Path(args.results),
        lang=args.lang,
        version=args.version,
        year=args.year,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()


"""
review_flags.py — GEP Flag Report Generator
Single responsibility: join batch_input + batch_output, extract FLAG entries,
write a human-readable report to data/reports/.

No model calls. Pure Python. No hardcoded paths — all dirs from paths.py.

Usage:
    python3 review_flags.py --lang es --version RVR1960 --year 2025 --phase 1
    python3 review_flags.py --lang ar --version NAV     --year 2025 --phase 1
    python3 review_flags.py --lang es --version RVR1960 --year 2025 --phase 2 --verdict ALL

    # Point to specific files instead of auto-resolving
    python3 review_flags.py --lang es --version RVR1960 --year 2025 --phase 1 \\
        --input  batch_input_es_RVR1960_2025_p1_qwen-flash_20260425_003812.jsonl \\
        --results BIJOutputSet_es_RVR1960_2025_p1_qwen-flash_20260425_003812_results.jsonl

Output:
    data/reports/review_{lang}_{version}_{year}_p{N}_{ts}.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Load .env for local development (optional)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    pass

import paths as _paths


# ── Field extraction ──────────────────────────────────────────────────────────

# Phase 1 prompt markers
_P1_MARKERS = {
    "reflexion": re.compile(r"--- REFLEXIÓN ---\n(.*?)(?=--- ORACIÓN ---|Evaluate only)", re.DOTALL),
    "oracion":   re.compile(r"--- ORACIÓN ---\n(.*?)(?=Evaluate only|\Z)", re.DOTALL),
}

# Phase 2 prompt markers (versiculo included in p2 prompts)
_P2_MARKERS = {
    "versiculo": re.compile(r"--- VERSÍCULO ---\n(.*?)(?=--- REFLEXIÓN ---)", re.DOTALL),
    "reflexion": re.compile(r"--- REFLEXIÓN ---\n(.*?)(?=--- ORACIÓN ---)", re.DOTALL),
    "oracion":   re.compile(r"--- ORACIÓN ---\n(.*?)(?=--- PARA MEDITAR ---|Evaluate only|\Z)", re.DOTALL),
}


def _extract_fields(prompt: str, phase: int) -> dict[str, str]:
    markers = _P2_MARKERS if phase == 2 else _P1_MARKERS
    result = {}
    for key, pattern in markers.items():
        m = pattern.search(prompt)
        result[key] = m.group(1).strip() if m else ""
    return result


# ── Response parsing ──────────────────────────────────────────────────────────

def _parse_p1_response(raw: str) -> dict | None:
    """Extract verdict dict from Phase 1 response (CLEAN/FLAG)."""
    clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        clean = parts[1][4:] if len(parts) > 1 and parts[1].startswith("json") else parts[1] if len(parts) > 1 else clean
    try:
        return json.loads(clean.strip())
    except json.JSONDecodeError:
        m = re.search(r'\{[^{}]*"verdict"[^{}]*\}', clean, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None


def _parse_p2_response(raw: str) -> dict | None:
    """Extract verdict dict from Phase 2 response (OK/PAUSE)."""
    clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        clean = parts[1][4:] if len(parts) > 1 and parts[1].startswith("json") else parts[1] if len(parts) > 1 else clean
    try:
        return json.loads(clean.strip())
    except json.JSONDecodeError:
        m = re.search(r'\{[^{}]*"verdict"[^{}]*\}', clean, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None


def _extract_content(result_line: dict) -> str | None:
    """Handle both Fireworks and DashScope response shapes."""
    resp = result_line.get("response", {})
    body = resp.get("body") or resp
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


# ── Auto-resolve input/results files ─────────────────────────────────────────

def _auto_resolve(lang: str, version: str, year: int, phase: int) -> tuple[Path | None, Path | None]:
    """
    Find the most recent matching input + results file pair in data dirs.
    Pattern: batch_input_{lang}_{version}_{year}_p{N}*.jsonl
             BIJOutputSet_{lang}_{version}_{year}_p{N}*_results.jsonl
    Returns (input_path, results_path) — None if not found.
    """
    _paths.ensure_dirs()

    prefix_in  = f"batch_input_{lang}_{version}_{year}_p{phase}"
    prefix_out = f"BIJOutputSet_{lang}_{version}_{year}_p{phase}"

    inputs  = sorted(_paths.BATCH_INPUT_DIR.glob(f"{prefix_in}*.jsonl"),  key=lambda p: p.stat().st_mtime, reverse=True)
    outputs = sorted(_paths.BATCH_OUTPUT_DIR.glob(f"{prefix_out}*_results.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

    return (inputs[0] if inputs else None, outputs[0] if outputs else None)


# ── Core ──────────────────────────────────────────────────────────────────────

def build_report(
    lang: str,
    version: str,
    year: int,
    phase: int,
    input_path: Path,
    results_path: Path,
    verdict_filter: str,        # "FLAG" | "PAUSE" | "ALL"
) -> tuple[str, int, int, int]:
    """
    Join input + results, filter by verdict, build report string.
    Returns (report_text, total, matched, errors).
    """
    # Phase 1: FLAG/CLEAN   Phase 2: PAUSE/OK
    flag_term  = "FLAG"  if phase == 1 else "PAUSE"
    clean_term = "CLEAN" if phase == 1 else "OK"
    issue_key  = "issue"          if phase == 1 else "reaction"
    quoted_key = "quoted_problem" if phase == 1 else "quoted_pause"

    # Build input index: custom_id -> user prompt content
    input_index: dict[str, str] = {}
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                cid = d.get("custom_id", "")
                for m in d.get("body", {}).get("messages", []):
                    if m.get("role") == "user":
                        input_index[cid] = m.get("content", "")
                        break
            except json.JSONDecodeError:
                continue

    # Parse results
    entries: list[dict] = []
    parse_errors = 0
    total = 0

    with open(results_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue

            total += 1
            cid = d.get("custom_id", "")
            raw = _extract_content(d)
            if not raw:
                parse_errors += 1
                continue

            parsed = _parse_p1_response(raw) if phase == 1 else _parse_p2_response(raw)
            if not parsed:
                parse_errors += 1
                continue

            verdict = parsed.get("verdict", "").upper()
            fields  = _extract_fields(input_index.get(cid, ""), phase)

            entries.append({
                "id":       cid,
                "verdict":  verdict,
                "issue":    parsed.get(issue_key, "") or "",
                "quoted":   parsed.get(quoted_key, "") or "",
                "conf":     parsed.get("confidence", ""),
                "category": parsed.get("category", "") or "",
                **fields,
            })

    # Filter
    if verdict_filter == "ALL":
        filtered = entries
    else:
        filtered = [e for e in entries if e["verdict"] == verdict_filter]

    ok_count   = sum(1 for e in entries if e["verdict"] in (clean_term,))
    flag_count = sum(1 for e in entries if e["verdict"] == flag_term)

    # Build report
    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    header_verdict = flag_term if verdict_filter != "ALL" else "ALL"
    lines.append(f"GEP·REVIEW  {lang}·{version}·{year}·p{phase}  {header_verdict}:{len(filtered)}  generated:{ts_now}")
    lines.append(f"input  : {input_path.name}")
    lines.append(f"results: {results_path.name}")
    lines.append(f"total:{total}  {clean_term}:{ok_count}  {flag_term}:{flag_count}  errors:{parse_errors}")
    lines.append("")

    if not filtered:
        lines.append(f"  No entries matched filter '{verdict_filter}'.")
    else:
        for i, e in enumerate(filtered, 1):
            lines.append(f"{'═'*64}")
            lines.append(f"[{i}/{len(filtered)}] {e['id']}")
            lines.append(f"VERDICT  {e['verdict']}")
            if e.get("category"):
                lines.append(f"CATEGORY {e['category']}")
            lines.append(f"CONF     {e['conf']}")
            lines.append(f"ISSUE    {e['issue']}")
            lines.append(f"QUOTED   {e['quoted']}")
            lines.append("")
            if e.get("versiculo"):
                lines.append(f"VER  {e['versiculo']}")
                lines.append("")
            lines.append(f"REF  {e['reflexion']}")
            lines.append("")
            lines.append(f"ORA  {e['oracion']}")
            lines.append("")
            lines.append(f"VERDICT_HUMAN  ___  [accept|dismiss|fix]")
            lines.append(f"NOTE           ___")
            lines.append("")

    lines.append(f"{'═'*64}")
    lines.append(f"END·GEP·REVIEW  {lang}·{version}·{year}·p{phase}")

    return "\n".join(lines), total, len(filtered), parse_errors


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="GEP — Generate human-readable FLAG review report (no model calls)."
    )
    parser.add_argument("--lang",     required=True, help="Language code (es, ar, tl, ...)")
    parser.add_argument("--version",  required=True, help="Bible version (RVR1960, NAV, ...)")
    parser.add_argument("--year",     required=True, type=int, help="Year (2025, 2026, ...)")
    parser.add_argument("--phase",    default=1, type=int, choices=[1, 2],
                        help="Phase to review: 1 (linguistic) or 2 (content) — default: 1")
    parser.add_argument("--input",    metavar="FILE",
                        help="Batch input JSONL — auto-resolved from data/batch_input/ if omitted")
    parser.add_argument("--results",  metavar="FILE",
                        help="Batch results JSONL — auto-resolved from data/batch_output/ if omitted")
    parser.add_argument("--verdict",  default="FLAG", choices=["FLAG", "PAUSE", "CLEAN", "OK", "ALL"],
                        help="Filter by verdict (default: FLAG for p1, PAUSE for p2)")
    parser.add_argument("--output",   metavar="FILE",
                        help="Override output report path (default: data/reports/review_...txt)")
    args = parser.parse_args()

    # Normalise verdict default per phase
    verdict_filter = args.verdict
    if verdict_filter == "FLAG" and args.phase == 2:
        verdict_filter = "PAUSE"

    _paths.ensure_dirs()

    # Resolve input / results paths
    if args.input:
        input_path = _paths.resolve_batch_input(args.input)
    else:
        input_path, _ = _auto_resolve(args.lang, args.version, args.year, args.phase)

    if args.results:
        results_path = _paths.resolve_batch_output(args.results)
    else:
        _, results_path = _auto_resolve(args.lang, args.version, args.year, args.phase)

    if not input_path or not input_path.exists():
        print(f"  ❌ batch_input not found. Pass --input explicitly or run build_batch first.")
        sys.exit(1)

    if not results_path or not results_path.exists():
        print(f"  ❌ batch_results not found. Pass --results explicitly or run batch_pipeline first.")
        sys.exit(1)

    print(f"\n{'═'*64}")
    print(f"  📋  GEP Flag Reviewer")
    print(f"  Lang: {args.lang} | Version: {args.version} | Year: {args.year} | Phase: {args.phase}")
    print(f"  Input  : {input_path.name}")
    print(f"  Results: {results_path.name}")
    print(f"  Filter : {verdict_filter}")
    print(f"{'═'*64}\n")

    report, total, matched, errors = build_report(
        lang=args.lang,
        version=args.version,
        year=args.year,
        phase=args.phase,
        input_path=input_path,
        results_path=results_path,
        verdict_filter=verdict_filter,
    )

    # Resolve output path
    if args.output:
        out_path = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"review_{args.lang}_{args.version}_{args.year}_p{args.phase}_{verdict_filter.lower()}_{ts}.txt"
        out_path = _paths.REPORTS_DIR / fname

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\n  💾 Report saved: {out_path}")
    print(f"  Total: {total}  Matched: {matched}  Errors: {errors}\n")


if __name__ == "__main__":
    main()

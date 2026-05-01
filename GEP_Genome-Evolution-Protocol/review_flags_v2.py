"""
review_flags_v2.py — GEP Flag Report Generator (supports flags[] array schema)

Fixes schema mismatch: deepseek batch uses flags[] array, not flat issue/quoted_problem.
Supports both schemas automatically.

Schema A (old/flat):
    {"verdict": "FLAG", "issue": "...", "quoted_problem": "...", "confidence": 0.9}

Schema B (deepseek/array):
    {"verdict": "FLAG", "flags": [{"type": "unnatural", "quoted_problem": "...",
                                    "suggested_fix": "...", "confidence": 0.8}]}

Usage:
    python3 review_flags_v2.py --lang fil --version MBB05 --year 2025 --phase 1 \\
        --input  batch_input_fil_MBB05_2025_p1_deepseek-v3p2_01may26.jsonl \\
        --results deepseek_fil_devocional_2025_review_critic_p1.jsonl.jsonl \\
        --verdict ALL

Output:
    data/reports/review_{lang}_{version}_{year}_p{N}_{verdict}_{ts}.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    pass

import paths as _paths


# ── Issue type classifier (heuristic, no model) ───────────────────────────────

_ISSUE_TYPE_RULES: list[tuple[str, re.Pattern]] = [
    (
        "structure",
        re.compile(r"double|amén.*amén|two clos|duplicate closing|spliced", re.I),
    ),
    (
        "punctuation",
        re.compile(r"punctuat|lowercase|capitaliz|uppercase|capital", re.I),
    ),
    ("typo", re.compile(r"typo|spelling|misspell|orthograph", re.I)),
    (
        "grammar",
        re.compile(
            r"grammar|grammat|conjugat|infinitiv|verb form|incorrect form", re.I
        ),
    ),
    ("repetition", re.compile(r"repeat|redundan|verbatim|same phrase|reiterat", re.I)),
    ("unnatural", re.compile(r"unnatural|awkward|register|phrasing|word order", re.I)),
]


def _classify_issue(issue: str) -> str:
    for label, pattern in _ISSUE_TYPE_RULES:
        if pattern.search(issue):
            return label
    return "other"


# ── Response parsing ──────────────────────────────────────────────────────────


def _clean_raw(raw: str) -> str:
    return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()


def _strip_fence(text: str) -> str:
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1][4:] if parts[1].startswith("json") else parts[1]
    return text.strip()


def _parse_response(raw: str) -> dict | None:
    """Parse JSON from model response; handles both flat and flags[] schemas."""
    clean = _strip_fence(_clean_raw(raw))
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        m = re.search(r'\{[^{}]*"verdict"[^{}]*\}', clean, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None


def _extract_flags(parsed: dict) -> list[dict]:
    """
    Normalise both response schemas into a flat list of flag dicts.

    Schema B (deepseek/array) takes precedence when ``flags`` is a non-empty list.
    Falls back to Schema A (flat) otherwise.
    """
    flags_raw = parsed.get("flags", [])
    verdict = parsed.get("verdict", "").upper()
    flag_verdicts = {"FLAG", "PAUSE"}

    # ── Schema B — flags[] array ──
    if flags_raw and isinstance(flags_raw, list):
        result = []
        for fl in flags_raw:
            quoted = fl.get("quoted_problem", "") or ""
            result.append(
                {
                    "type": fl.get("type", "other"),
                    "issue": fl.get("type", "") + (f": {quoted}" if quoted else ""),
                    "quoted": quoted,
                    "fix": fl.get("suggested_fix", "") or "",
                    "confidence": fl.get("confidence", ""),
                    "category": fl.get("category", "") or "",
                    "qa_tag": (
                        "AUTO_DISMISS_CANDIDATE: FLAG with no quoted evidence"
                        " — model self-corrected or hallucinated flag"
                        if verdict in flag_verdicts and not quoted.strip()
                        else ""
                    ),
                }
            )
        return result

    # ── Schema A — flat ──
    quoted = parsed.get("quoted_problem", "") or ""
    issue = parsed.get("issue", "") or ""
    return [
        {
            "type": _classify_issue(issue),
            "issue": issue,
            "quoted": quoted,
            "fix": parsed.get("suggested_fix", "") or "",
            "confidence": parsed.get("confidence", ""),
            "category": parsed.get("category", "") or "",
            "qa_tag": (
                "AUTO_DISMISS_CANDIDATE: FLAG with no quoted evidence"
                " — model self-corrected or hallucinated flag"
                if verdict in flag_verdicts and not quoted.strip()
                else ""
            ),
        }
    ]


def _extract_content(result_line: dict) -> str | None:
    """Handle both Fireworks and DashScope response shapes."""
    resp = result_line.get("response", {})
    body = resp.get("body") or resp
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


# ── Core ──────────────────────────────────────────────────────────────────────


def build_report(
    lang: str,
    version: str,
    year: int,
    phase: int,
    input_path: Path,
    results_path: Path,
    verdict_filter: str,
) -> tuple[str, int, int, int]:
    """
    Join input + results, extract flags, filter by verdict, build report string.

    Returns (report_text, total, matched, errors).
    """
    flag_term = "FLAG" if phase == 1 else "PAUSE"
    clean_term = "CLEAN" if phase == 1 else "OK"

    # Build input index: custom_id -> user prompt content
    input_index: dict[str, str] = {}
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                cid = d.get("custom_id", "")
                for msg in d.get("body", {}).get("messages", []):
                    if msg.get("role") == "user":
                        input_index[cid] = msg.get("content", "")
                        break
            except json.JSONDecodeError:
                continue

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

            parsed = _parse_response(raw)
            if not parsed:
                parse_errors += 1
                continue

            verdict = parsed.get("verdict", "").upper()
            flags = _extract_flags(parsed)

            if flags and verdict == flag_term:
                for fl in flags:
                    if not fl["quoted"].strip():
                        continue  # skip empty-quoted flags
                    entries.append(
                        {
                            "id": cid,
                            "verdict": verdict,
                            "issue_type": fl["type"],
                            "issue": fl["issue"],
                            "quoted": fl["quoted"],
                            "fix": fl["fix"],
                            "conf": fl["confidence"],
                            "category": fl["category"],
                            "qa_tag": fl["qa_tag"],
                            "input": input_index.get(cid, ""),
                        }
                    )
            elif verdict == clean_term:
                entries.append(
                    {
                        "id": cid,
                        "verdict": verdict,
                        "issue_type": "",
                        "issue": "",
                        "quoted": "",
                        "fix": "",
                        "conf": "",
                        "category": "",
                        "qa_tag": "",
                        "input": "",
                    }
                )

    ok_count = sum(1 for e in entries if e["verdict"] == clean_term)
    flag_count = sum(1 for e in entries if e["verdict"] == flag_term)

    if verdict_filter == "ALL":
        filtered = entries
    elif verdict_filter == flag_term:
        filtered = [e for e in entries if e["verdict"] == flag_term]
    else:
        filtered = [e for e in entries if e["verdict"] == verdict_filter]

    issue_type_counts = Counter(e["issue_type"] for e in filtered if e["issue_type"])
    auto_dismiss_count = sum(1 for e in filtered if e.get("qa_tag"))

    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    header_verdict = flag_term if verdict_filter != "ALL" else "ALL"

    lines.append(
        f"GEP·REVIEW  {lang}·{version}·{year}·p{phase}"
        f"  {header_verdict}:{len(filtered)}  generated:{ts_now}"
    )
    lines.append(f"input  : {input_path.name}")
    lines.append(f"results: {results_path.name}")
    lines.append(
        f"total:{total}  {clean_term}:{ok_count}  {flag_term}:{flag_count}  errors:{parse_errors}"
    )
    if auto_dismiss_count:
        lines.append(
            f"qa_warn  AUTO_DISMISS_CANDIDATES:{auto_dismiss_count}"
            " — review carefully before patching"
        )
    if issue_type_counts:
        lines.append(
            "patterns  "
            + "  ".join(f"{k}:{v}" for k, v in issue_type_counts.most_common())
        )
    lines.append("")

    if not filtered:
        lines.append(f"  No entries matched filter '{verdict_filter}'.")
    else:
        for i, e in enumerate(filtered, 1):
            lines.append(f"{'═' * 64}")
            lines.append(f"[{i}/{len(filtered)}] {e['id']}")
            lines.append(f"VERDICT     {e['verdict']}")
            if e.get("qa_tag"):
                lines.append(f"QA_TAG      ⚠️  {e['qa_tag']}")
            if e.get("category"):
                lines.append(f"CATEGORY    {e['category']}")
            lines.append(f"ISSUE_TYPE  {e['issue_type']}")
            lines.append(f"CONF        {e['conf']}")
            lines.append(f"QUOTED      {e['quoted']}")
            lines.append(f"FIX         {e['fix']}")
            lines.append("")
            if e.get("input"):
                lines.append("INPUT EXCERPT:")
                lines.append(e["input"][:600])
            lines.append("")

    lines.append(f"{'═' * 64}")
    lines.append(f"END·GEP·REVIEW  {lang}·{version}·{year}·p{phase}")
    return "\n".join(lines), total, len(filtered), parse_errors


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GEP Flag Reviewer v2 — supports flags[] array schema (deepseek-v3p2)."
    )
    parser.add_argument("--lang", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--phase", default=1, type=int, choices=[1, 2])
    parser.add_argument("--input", metavar="FILE")
    parser.add_argument("--results", metavar="FILE")
    parser.add_argument(
        "--verdict",
        default="FLAG",
        choices=["FLAG", "PAUSE", "CLEAN", "OK", "ALL"],
    )
    parser.add_argument("--output", metavar="FILE")
    args = parser.parse_args()

    _paths.ensure_dirs()

    input_path = _paths.resolve_batch_input(args.input) if args.input else None
    results_path = _paths.resolve_batch_output(args.results) if args.results else None

    if not input_path or not input_path.exists():
        print("  ❌ batch_input not found.")
        sys.exit(1)
    if not results_path or not results_path.exists():
        print("  ❌ batch_results not found.")
        sys.exit(1)

    print(f"\n{'═' * 64}")
    print("  📋  GEP Flag Reviewer v2")
    print(
        f"  Lang: {args.lang} | Version: {args.version} | Year: {args.year} | Phase: {args.phase}"
    )
    print(f"  Input  : {input_path.name}")
    print(f"  Results: {results_path.name}")
    print(f"  Filter : {args.verdict}")
    print(f"{'═' * 64}\n")

    report, total, matched, errors = build_report(
        lang=args.lang,
        version=args.version,
        year=args.year,
        phase=args.phase,
        input_path=input_path,
        results_path=results_path,
        verdict_filter=args.verdict,
    )

    if args.output:
        out_path = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = (
            f"review_{args.lang}_{args.version}_{args.year}"
            f"_p{args.phase}_{args.verdict.lower()}_{ts}.txt"
        )
        out_path = _paths.REPORTS_DIR / fname

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\n  💾 Report saved: {out_path}")
    print(f"  Total: {total}  Matched: {matched}  Errors: {errors}\n")


if __name__ == "__main__":
    main()

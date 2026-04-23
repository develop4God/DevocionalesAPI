"""
batch_collect.py
────────────────
STEP 2 — Collect results from any provider and build devotional records.

Replaces batch_claude_collect.py with a provider-agnostic version.
Reads the state file from batch_submit.py, fires the adapter's collect(),
parses JSON, validates Phase 1, builds devotional records, saves output.

CLI:
  python batch_collect.py --state batch_state_tl_ADB_gemini_20260421_120000.json
  python batch_collect.py          # auto-finds latest batch_state_*.json

Outputs (in output_dir):
  raw_<lang>_<version>_<provider>_<ts>.json       all built devotionals
  batch_errors_<lang>_<version>_<provider>_<ts>.json   failed dates
  batch_val_warnings_<lang>_<version>_<provider>_<ts>.json  Phase1 warnings
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pipeline_shared import repair_json, build_prompt
from pipeline_shared import _check_prayer_ending, _load_prayer_endings
from provider_adapter import load_adapter, BatchRequest, RawResult

_SCRIPT_DIR = Path(__file__).parent

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 local validation  (identical logic to original collect)
# ─────────────────────────────────────────────────────────────────────────────

REFLEXION_MIN_CHARS = 800
ORACION_MIN_CHARS   = 150

LITURGICAL_WHITELIST = frozenset({
    "heilig", "holy", "kadosh", "halleluja", "hosanna",
    "amen", "amén", "āmen", "aleluya", "panginoon",
    "banal", "santo", "sagrado",
})

def _normalize(w: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFD", w).encode("ascii", "ignore").decode().lower()

def _find_dup_words(text: str) -> Optional[str]:
    strip_chars = ".,;:!?\"'()[]{}—–-\u2019\u2018\u201c\u201d"
    words = text.split()
    for i in range(1, len(words)):
        w1 = words[i - 1].strip(strip_chars).lower()
        w2 = words[i].strip(strip_chars).lower()
        if w1 and w2 and w1 == w2 and _normalize(w1) not in LITURGICAL_WHITELIST:
            return f"{words[i-1]} {words[i]}"
    return None

def run_phase1(reflexion: str, oracion: str, lang: str) -> tuple[bool, list[str]]:
    issues = []
    r, o = reflexion.strip(), oracion.strip()

    if len(r) < REFLEXION_MIN_CHARS:
        issues.append(f"reflexion too short: {len(r)} < {REFLEXION_MIN_CHARS}")
    if len(o) < ORACION_MIN_CHARS:
        issues.append(f"oracion too short: {len(o)} < {ORACION_MIN_CHARS}")
    if not r.endswith((".", "!", "?", "…", "।")):
        if len(r) > 0 and r[-1].isalnum():
            issues.append("reflexion_truncated: ends mid-word")
    if not _check_prayer_ending(o, lang):
        endings = _load_prayer_endings().get(lang, ["Amen"])
        last = o.split()[-1] if o.split() else ""
        issues.append(f"prayer_ending: last word '{last}' — expected one of {endings}")

    # double amen (last 120 chars)
    tail = o[-120:].lower()
    amen_variants = ["amen", "amén", "amem", "amim", "amin"]
    amen_count = sum(tail.count(v) for v in amen_variants)
    if amen_count >= 2:
        issues.append("double_amen: duplicate Amen artifact detected in closing")

    dup_r = _find_dup_words(r)
    if dup_r:
        issues.append(f"dup_words_reflexion: consecutive duplicate '{dup_r}'")
    dup_o = _find_dup_words(o)
    if dup_o:
        issues.append(f"dup_words_oracion: consecutive duplicate '{dup_o}'")

    return (len(issues) == 0), issues


# ─────────────────────────────────────────────────────────────────────────────
# Devotional builder  (identical to original)
# ─────────────────────────────────────────────────────────────────────────────

def _safe_custom_id(date_key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", date_key)[:64]

def _build_id(date_key: str, seed_entry: dict, lang: str, version: str) -> str:
    cita = seed_entry.get("versiculo", {}).get("cita", "")
    book_ref = re.sub(r"[^A-Za-z0-9]", "", cita)[:10]
    date_slug = date_key.replace("-", "")
    return f"{book_ref}{version}{date_slug}"

def _build_versiculo_str(seed_entry: dict, version: str) -> str:
    v    = seed_entry.get("versiculo", {})
    cita = v.get("cita", "")
    txt  = v.get("texto", "")
    return f"{cita} {version}: \"{txt}\""

def build_devotional(
    date_key: str, seed_entry: dict, lang: str, version: str,
    reflexion: str, oracion: str,
) -> dict:
    required = ["versiculo", "para_meditar"]
    for k in required:
        if not seed_entry.get(k):
            raise ValueError(f"[{date_key}] seed missing required field: {k}")
    if not seed_entry["versiculo"].get("cita"):
        raise ValueError(f"[{date_key}] versiculo.cita is empty")

    tags = seed_entry.get("tags", [])
    if not isinstance(tags, list) or not tags:
        tags = ["devotional"]

    return {
        "id":           _build_id(date_key, seed_entry, lang, version),
        "date":         date_key,
        "language":     lang,
        "version":      version,
        "versiculo":    _build_versiculo_str(seed_entry, version),
        "reflexion":    reflexion,
        "para_meditar": seed_entry["para_meditar"],
        "oracion":      oracion,
        "tags":         tags,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Save helpers
# ─────────────────────────────────────────────────────────────────────────────

def _save_json(data, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_output(completed: dict, lang: str, version: str, provider: str, output_dir: str) -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"raw_{lang}_{version}_{provider}_{ts}.json"
    path     = Path(output_dir) / filename
    nested   = {"data": {lang: {date: [devo] for date, devo in sorted(completed.items())}}}
    _save_json(nested, path)
    return str(path)


# ─────────────────────────────────────────────────────────────────────────────
# State loader
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# ASCII / Retro-minimalist UI helpers
# ─────────────────────────────────────────────────────────────────────────────

_W = 90  # total inner width (between the side borders)

# ANSI color codes — fall back gracefully if terminal doesn't support them
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_WHITE  = "\033[97m"
# Brand colors (24-bit RGB — supported by most modern terminals)
_CAMO   = "\033[38;2;120;134;107m"   # Camouflage Green #78866B — OK status
_ORANGE = "\033[38;2;255;90;45m"     # Lobster Orange   #FF5A2D — WARN status

def _c(text: str, *codes: str) -> str:
    """Wrap text in ANSI codes (auto-reset)."""
    return "".join(codes) + text + _RESET

def _box_top(title: str = "") -> str:
    if title:
        pad   = _W - len(title) - 2
        left  = pad // 2
        right = pad - left
        inner = "─" * left + f" {title} " + "─" * right
    else:
        inner = "─" * _W
    return f"╔{inner}╗"

def _box_mid(title: str = "") -> str:
    if title:
        pad   = _W - len(title) - 2
        left  = pad // 2
        right = pad - left
        inner = "─" * left + f" {title} " + "─" * right
    else:
        inner = "─" * _W
    return f"╠{inner}╣"

def _box_bot() -> str:
    return f"╚{'─' * _W}╝"

def _box_row(text: str, pad_char: str = " ") -> str:
    visible = len(text.encode("utf-8").decode("utf-8"))
    # strip ANSI for length calc
    import re as _re
    clean = _re.sub(r"\033\[[0-9;]*m", "", text)
    padding = _W - len(clean)
    return f"║ {text}{pad_char * max(0, padding - 1)}║"

def _box_blank() -> str:
    return f"║{'  ' * (_W // 2)}║"[: _W + 2].replace("  " * (_W // 2), " " * _W)

def _print_banner() -> None:
    print()
    print(_c(_box_top("BATCH COLLECT  //  DEVOTIONAL PIPELINE"), _CYAN, _BOLD))
    print(_c(_box_row("  Step 2 of 2  —  Collect + Build devotional records"), _CYAN))
    print(_c(_box_bot(), _CYAN, _BOLD))
    print()

def _print_state_header(state: dict, state_path: str) -> None:
    provider  = state.get("provider", "?")
    model_id  = state.get("model_id", "?")
    job_id    = state.get("job_id", "?")
    lang      = state.get("master_lang", "?")
    version   = state.get("master_version", "?")
    seed      = state.get("seed_path", "?")
    out_dir   = state.get("output_dir", "?")
    submitted = state.get("submitted_at", "unknown")
    dates     = state.get("dates", [])

    label_w = 12
    def row(label: str, value: str) -> str:
        lbl = _c(f"{label:<{label_w}}", _YELLOW, _BOLD)
        return _box_row(f"  {lbl}  {value}")

    print(_c(_box_top("JOB"), _CYAN, _BOLD))
    print(_c(row("Provider",  provider),  _WHITE))
    print(_c(row("Model",     model_id),  _WHITE))
    print(_c(row("Job ID",    job_id[:52] + ("…" if len(job_id) > 52 else "")), _WHITE))
    print(_c(row("Lang",      f"{lang}  |  version: {version}"), _WHITE))
    print(_c(row("Dates",     f"{len(dates)} entries  ({dates[0]} → {dates[-1]})" if len(dates) >= 2 else str(len(dates))), _WHITE))
    print(_c(row("Seed",      Path(seed).name), _WHITE))
    print(_c(row("Output",    out_dir), _WHITE))
    print(_c(row("Submitted", submitted), _WHITE))
    print(_c(_box_bot(), _CYAN, _BOLD))
    print()

def _print_result_row(date_key: str, status: str, detail: str = "") -> None:
    icons = {
        "OK":          _c("  OK  ", _CAMO,   _BOLD),
        "WARN":        _c(" WARN ", _ORANGE, _BOLD),
        "ERROR":       _c(" ERR  ", _RED,    _BOLD),
        "PARSE_ERROR": _c(" PRSE ", _RED,    _BOLD),
        "EMPTY":       _c(" EMPT ", _RED,    _BOLD),
        "NO_SEED":     _c(" SEED ", _RED,    _BOLD),
        "BUILD_ERROR": _c(" BILD ", _RED,    _BOLD),
    }
    icon   = icons.get(status, f" {status[:4]:4s} ")
    dk     = _c(date_key, _WHITE, _BOLD)
    detail_str = _c(f"  {detail}", _DIM) if detail else ""
    print(_box_row(f" [{icon}]  {dk}{detail_str}"))

def _print_summary(total: int, built: int, errors: int, warnings: int) -> None:
    print()
    print(_c(_box_top("RESULTS"), _CYAN, _BOLD))

    def srow(label: str, val: int, color: str) -> str:
        bar_max  = max(total, 1)
        filled   = int((val / bar_max) * 30)
        bar      = _c("█" * filled, color) + _c("░" * (30 - filled), _DIM)
        numstr   = _c(f"{val:>4}", color, _BOLD)
        lbl      = _c(f"{label:<10}", _YELLOW)
        return _box_row(f"  {lbl}  {numstr}  {bar}")

    print(_c(_box_row(f"  {'Total':<10}  {total:>4}"), _WHITE))
    print(_c(srow("Built",    built,    _CAMO)))
    print(_c(srow("Warnings", warnings, _ORANGE)))
    print(_c(srow("Errors",   errors,   _RED)))
    print(_c(_box_bot(), _CYAN, _BOLD))
    print()


def find_latest_state_file() -> Optional[str]:
    files = sorted(_SCRIPT_DIR.glob("batch_state_*.json"), key=os.path.getmtime, reverse=True)
    if not files:
        return None
    if len(files) == 1:
        return str(files[0])

    # ── Multi-file ASCII picker ────────────────────────────────────────────
    _print_banner()
    print(_c(_box_top("PENDING BATCHES"), _CYAN, _BOLD))

    entries = []
    for i, f in enumerate(files, 1):
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
        try:
            with open(f, encoding="utf-8") as fh:
                s = json.load(fh)
            provider = s.get("provider", "?")
            lang     = s.get("master_lang", "?")
            version  = s.get("master_version", "?")
            dates    = s.get("dates", [])
            count    = len(dates)
            dr       = f"{dates[0]} → {dates[-1]}" if count >= 2 else (dates[0] if count else "—")
        except Exception:
            provider, lang, version, count, dr = "?", "?", "?", 0, "—"
            mtime = "?"

        entries.append((f, provider, lang, version, count, dr, mtime))

        num     = _c(f"  [{i}]", _YELLOW, _BOLD)
        fname   = _c(f.name, _WHITE, _BOLD)
        prov    = _c(provider, _CYAN)
        lv      = _c(f"{lang} {version}", _GREEN)
        cnt     = _c(str(count), _WHITE, _BOLD)
        dates_s = _c(dr, _DIM)
        ts      = _c(f"submitted {mtime}", _DIM)

        print(_box_row(f"{num}  {fname}"))
        print(_box_row(f"       {prov}  |  {lv}  |  {cnt} dates  ({dates_s})"))
        print(_box_row(f"       {ts}"))
        print(_box_row(""))

    print(_c(_box_bot(), _CYAN, _BOLD))
    print()

    while True:
        raw = input(_c(f"  Select batch [1-{len(files)}]  or  Enter = latest: ", _YELLOW, _BOLD)).strip()
        if raw == "":
            print(_c(f"\n  >> Using latest: {files[0].name}\n", _GREEN))
            return str(files[0])
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(files):
                print(_c(f"\n  >> Selected: {files[idx].name}\n", _GREEN))
                return str(files[idx])
        print(_c(f"  Invalid — enter a number between 1 and {len(files)}.", _RED))


def load_state(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Main collect logic
# ─────────────────────────────────────────────────────────────────────────────

def collect(state_path: str, results_file: Optional[str] = None) -> None:
    SEP = "=" * 60
    state = load_state(state_path)

    provider       = state["provider"]
    model_alias    = state.get("model_alias") or None
    job_id         = state["job_id"]
    seed_path      = state["seed_path"]
    master_lang    = state["master_lang"]
    master_version = state["master_version"]
    output_dir     = state["output_dir"]

    _print_banner()
    _print_state_header(state, state_path)
    print(_c(_box_top("PROCESSING"), _CYAN, _BOLD))

    # ── Load seed ──────────────────────────────────────────────────────────
    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    # Rebuild BatchRequest list from state (needed by adapters)
    requests: list[BatchRequest] = []
    for date_key in state.get("dates", []):
        seed_entry = seed.get(date_key, {})
        cita  = seed_entry.get("versiculo", {}).get("cita", "")
        topic = seed_entry.get("topic")
        requests.append(BatchRequest(
            date_key=date_key,
            custom_id=_safe_custom_id(date_key),
            prompt=build_prompt(cita, master_lang, topic),
            model_id=state.get("model_id", ""),
        ))

    # ── Load adapter + collect ─────────────────────────────────────────────
    if model_alias == "default":
        model_alias = None
    adapter = load_adapter(provider, model_alias)

    # For openai_batch_file, job_id is the submitted file path —
    # results come from a separate file the user downloads from Fireworks.
    batch_strategy = state.get("batch_strategy") or adapter.batch_strategy
    if batch_strategy == "openai_batch_file":
        if not results_file:
            print("ERROR: --results <results.jsonl> is required for fireworks_batch provider.")
            print("       Download the results JSONL from Fireworks AI and pass it here.")
            sys.exit(1)
        collect_job_id = results_file
    else:
        collect_job_id = job_id

    print(_c(_box_row(f"  Collecting {len(requests)} results from {provider}..."), _DIM))
    print(_c(_box_row(""), _CYAN))
    raw_results: list[RawResult] = adapter.collect(collect_job_id, requests)

    # ── Process results ────────────────────────────────────────────────────
    completed:    dict[str, dict] = {}
    error_records: list[dict]     = []
    val_warnings:  list[dict]     = []

    for result in raw_results:
        date_key = result.date_key

        if not result.succeeded:
            _print_result_row(date_key, "ERROR", result.error or "")
            error_records.append({"date": date_key, "reason": "provider_error",
                                   "detail": result.error})
            continue

        # Parse JSON
        data = repair_json(result.raw_text)
        if data is None:
            _print_result_row(date_key, "PARSE_ERROR", "all JSON repair strategies failed")
            error_records.append({"date": date_key, "reason": "parse_error",
                                   "detail": result.raw_text[:200]})
            continue

        reflexion = data.get("reflexion", "").strip()
        oracion   = data.get("oracion",   "").strip()

        if not reflexion or not oracion:
            _print_result_row(date_key, "EMPTY", f"reflexion={bool(reflexion)} oracion={bool(oracion)}")
            error_records.append({"date": date_key, "reason": "empty_fields",
                                   "detail": f"reflexion={bool(reflexion)} oracion={bool(oracion)}"})
            continue

        # Phase 1 validation
        p1_passed, p1_issues = run_phase1(reflexion, oracion, master_lang)
        if not p1_passed:
            val_warnings.append({"date": date_key, "issue": "phase1", "detail": p1_issues})

        # Build devotional record
        seed_entry = seed.get(date_key, {})
        if not seed_entry:
            _print_result_row(date_key, "NO_SEED", "seed entry missing")
            error_records.append({"date": date_key, "reason": "seed_entry_missing"})
            continue

        try:
            devo = build_devotional(date_key, seed_entry, master_lang, master_version,
                                    reflexion, oracion)
            completed[date_key] = devo
            if p1_issues:
                _print_result_row(date_key, "WARN", f"r:{len(reflexion)} o:{len(oracion)}  {len(p1_issues)} issue(s)")
            else:
                _print_result_row(date_key, "OK", f"r:{len(reflexion)} o:{len(oracion)}")
        except Exception as e:
            _print_result_row(date_key, "BUILD_ERROR", str(e))
            error_records.append({"date": date_key, "reason": "build_error", "detail": str(e)})

    print(_c(_box_bot(), _CYAN, _BOLD))

    # ── Save outputs ───────────────────────────────────────────────────────
    _print_summary(len(requests), len(completed), len(error_records), len(val_warnings))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(_c(_box_top("OUTPUT FILES"), _CYAN, _BOLD))
    if completed:
        out_path = save_output(completed, master_lang, master_version, provider, output_dir)
        print(_c(_box_row(f"  {_c('raw output', _CAMO,   _BOLD)}   →  {out_path}"), _WHITE))

    if error_records:
        err_path = Path(output_dir) / f"batch_errors_{master_lang}_{master_version}_{provider}_{ts}.json"
        _save_json(error_records, err_path)
        print(_c(_box_row(f"  {_c('errors    ', _RED,   _BOLD)}   →  {err_path}"), _WHITE))

    if val_warnings:
        warn_path = Path(output_dir) / f"batch_val_warnings_{master_lang}_{master_version}_{provider}_{ts}.json"
        _save_json(val_warnings, warn_path)
        print(_c(_box_row(f"  {_c('warnings  ', _ORANGE, _BOLD)}   →  {warn_path}"), _WHITE))

    if not completed and not error_records and not val_warnings:
        print(_c(_box_row("  No output files written."), _DIM))

    print(_c(_box_bot(), _CYAN, _BOLD))
    print()

    if error_records:
        print(_c(f"  !! {len(error_records)} error(s) — run batch_repair.py next:", _RED, _BOLD))
        print(_c(f"     python batch_repair.py --state {state_path}", _DIM))
        print()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Collect provider results and build devotionals (Step 2/2)."
    )
    parser.add_argument("--state", type=str, default=None,
                        help="Path to batch_state_*.json (default: auto-find latest)")
    parser.add_argument("--results", type=str, default=None,
                        help="Path to results JSONL downloaded from Fireworks AI "
                             "(required for --provider fireworks_batch)")
    args = parser.parse_args()

    state_path = args.state
    if not state_path:
        state_path = find_latest_state_file()
        if not state_path:
            print("ERROR: No batch state file found. Run batch_submit.py first.")
            sys.exit(1)
        print(_c(f"\n  >> Auto-selected: {state_path}\n", _GREEN))

    if not os.path.exists(state_path):
        print(f"ERROR: State file not found: {state_path}")
        sys.exit(1)

    collect(state_path, results_file=args.results)


if __name__ == "__main__":
    main()

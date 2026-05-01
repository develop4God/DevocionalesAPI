#!/usr/bin/env python3
"""
main.py — GEP Genome Evolution Protocol
Interactive launcher. Single entry point. No flags to memorize.


Run:  python3 main.py


Three-stage pipeline:
  1. PREPARE  — select dataset (lang / version / year / source file)
  2. CRITIQUE — run the AI critic (batch cloud or local overnight)
  3. REVIEW   — read results (flags report, genome status)


UI style: ASCII box-drawing + ANSI colors (matches batch_collect.py).
Session:  .gep_session.json — remembers last lang/version/year.
"""
from __future__ import annotations


import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


import paths as _paths


# ── ANSI palette (identical to batch_collect.py) ──────────────────────────────
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_WHITE  = "\033[97m"
_CAMO   = "\033[38;2;120;134;107m"   # #78866B — OK / success
_ORANGE = "\033[38;2;255;90;45m"     # #FF5A2D — warning


_W = 70  # box inner width


def _c(text: str, *codes: str) -> str:
    return "".join(codes) + text + _RESET




# ── Box-drawing helpers ───────────────────────────────────────────────────────


def _box_top(title: str = "") -> str:
    if title:
        pad   = _W - len(title) - 2
        inner = "─" * (pad // 2) + f" {title} " + "─" * (pad - pad // 2)
    else:
        inner = "─" * _W
    return f"╔{inner}╗"


def _box_mid(title: str = "") -> str:
    if title:
        pad   = _W - len(title) - 2
        inner = "─" * (pad // 2) + f" {title} " + "─" * (pad - pad // 2)
    else:
        inner = "─" * _W
    return f"╠{inner}╣"


def _box_bot() -> str:
    return f"╚{'─' * _W}╝"


def _box_row(text: str) -> str:
    import re
    clean   = re.sub(r"\033\[[0-9;]*m", "", text)
    padding = _W - len(clean)
    return f"║ {text}{' ' * max(0, padding - 1)}║"


def _box_blank() -> str:
    return f"║{' ' * _W}║"




# ── Banner ────────────────────────────────────────────────────────────────────


def _banner() -> None:
    print()
    print(_c(_box_top("G E P  —  Genome Evolution Protocol"), _CYAN, _BOLD))
    print(_c(_box_row("  Devotional QA Pipeline  //  Simulated Reader Critic"), _CYAN))
    print(_c(_box_bot(), _CYAN, _BOLD))
    print()




# ── Session ───────────────────────────────────────────────────────────────────


_SESSION_FILE = Path(__file__).parent / ".gep_session.json"


@dataclass
class Session:
    lang:        str = "es"
    version:     str = "RVR1960"
    year:        int = 2025
    local_file:  str = ""   # last used --local path


def _load_session() -> Session:
    try:
        if _SESSION_FILE.exists():
            raw = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
            return Session(**{k: v for k, v in raw.items() if k in Session.__dataclass_fields__})
    except Exception:
        pass
    return Session()


def _save_session(s: Session) -> None:
    try:
        _SESSION_FILE.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
    except Exception:
        pass




# ── Input helpers ─────────────────────────────────────────────────────────────


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(_c(f"  {prompt}{suffix}: ", _YELLOW)).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val if val else default


def _confirm(prompt: str, default: bool = True) -> bool:
    yn = "Y/n" if default else "y/N"
    try:
        val = input(_c(f"  {prompt} [{yn}]: ", _YELLOW)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    return (val in ("y", "yes")) if val else default


def _numbered_menu(title: str, options: list[tuple[str, str]],
                   back_label: str = "Back") -> str:
    """
    Numbered menu with box-drawing UI.
    Returns the key of the chosen option, or '0' for back/exit.
    """
    print(_c(_box_top(title), _CYAN, _BOLD))
    for i, (_, label) in enumerate(options, 1):
        num = _c(f"  [{i}]", _YELLOW, _BOLD)
        print(_c(_box_row(f"{num}  {label}"), _WHITE))
    print(_c(_box_row(""), _WHITE))
    zero = _c("  [0]", _DIM)
    print(_c(_box_row(f"{zero}  {back_label}"), _DIM))
    print(_c(_box_bot(), _CYAN, _BOLD))


    while True:
        try:
            raw = input(_c("\n  Choice: ", _YELLOW, _BOLD)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "0"
        if raw == "0":
            return "0"
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        except ValueError:
            pass
        print(_c("  Invalid — try again.", _RED))




def _file_picker(title: str, folder: Path, pattern: str = "*.json*") -> str:
    """
    List files in folder by modified date. Returns chosen path or "".
    """
    files = sorted(folder.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        print(_c(f"  (no files found in {folder})", _DIM))
        return _ask("Enter full path manually", "")


    print(_c(_box_top(title), _CYAN, _BOLD))
    for i, f in enumerate(files, 1):
        size_kb = f.stat().st_size // 1024
        num     = _c(f"  [{i}]", _YELLOW, _BOLD)
        fname   = _c(f.name, _WHITE, _BOLD)
        meta    = _c(f"  {size_kb} KB", _DIM)
        print(_c(_box_row(f"{num}  {fname}{meta}"), _WHITE))
    print(_c(_box_row(""), _WHITE))
    zero = _c("  [0]", _DIM)
    print(_c(_box_row(f"{zero}  Enter path manually"), _DIM))
    print(_c(_box_bot(), _CYAN, _BOLD))


    while True:
        try:
            raw = input(_c("\n  Choice: ", _YELLOW, _BOLD)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ""
        if raw == "0":
            return _ask("Full path", "")
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(files):
                return str(files[idx])
        except ValueError:
            pass
        print(_c("  Invalid.", _RED))




# ── STAGE 1 — PREPARE ────────────────────────────────────────────────────────


def _stage_prepare(session: Session) -> Session:
    """
    Pick lang → version (from registry) → year → optional local file.
    Returns updated session. Caller saves to disk on confirm.
    """
    from lang_registry import list_languages, list_versions, get as get_lang


    os.system("clear")
    _banner()


    # Lang
    langs       = list_languages()
    lang_opts   = []
    for code in langs:
        cfg    = get_lang(code)
        marker = _c("◀", _CAMO) if code == session.lang else " "
        lang_opts.append((code, f"{marker}  {code}  —  {cfg.language_name}"))


    choice = _numbered_menu("PREPARE  1/3  —  Language", lang_opts, back_label="Cancel")
    if choice == "0":
        return session
    lang = choice


    # Version — auto-built from registry (no free-text)
    print()
    versions = list_versions(lang)
    ver_opts = []
    for v in versions:
        marker = _c("◀", _CAMO) if v == session.version else " "
        ver_opts.append((v, f"{marker}  {v}"))


    choice = _numbered_menu("PREPARE  2/3  —  Bible Version", ver_opts, back_label="Back")
    if choice == "0":
        return session
    version = choice


    # Year
    print()
    year_str = _ask("PREPARE  3/3  —  Year", str(session.year))
    try:
        year = int(year_str)
    except ValueError:
        year = session.year


    # Optional local file
    print()
    print(_c(_box_top("LOCAL SOURCE FILE  (optional)"), _CYAN, _BOLD))
    print(_c(_box_row("  Leave blank → fetch from GitHub automatically."), _DIM))
    print(_c(_box_bot(), _CYAN, _BOLD))
    print()


    local_file = ""
    if _confirm("Use a local source file?", default=bool(session.local_file)):
        _paths.ensure_dirs()
        local_file = _file_picker("Select from data/source/", _paths.SOURCE_DIR, "*.json")
        if not local_file:
            # Also check seed_generation yearly folders
            seed_dir = Path(__file__).parent.parent / "seed_generation" / "2025" / "yearly_devotionals"
            if seed_dir.exists():
                local_file = _file_picker("Select from seed_generation/yearly/", seed_dir, "*.json")


    new_session = Session(lang=lang, version=version, year=year, local_file=local_file)


    # Summary
    print()
    print(_c(_box_top("DATASET SUMMARY"), _CYAN, _BOLD))
    lbl_w = 10
    def srow(label: str, value: str, color: str = _WHITE) -> None:
        lbl = _c(f"  {label:<{lbl_w}}", _YELLOW, _BOLD)
        print(_c(_box_row(f"{lbl}  {_c(value, color, _BOLD)}"), _WHITE))


    srow("Language", f"{lang}  —  {get_lang(lang).language_name}", _CAMO)
    srow("Version",  version,                                       _CAMO)
    srow("Year",     str(year),                                     _CAMO)
    srow("Source",   Path(local_file).name if local_file else "GitHub (auto)", _CYAN)
    print(_c(_box_bot(), _CYAN, _BOLD))
    print()


    if _confirm("Confirm dataset?"):
        return new_session
    return session




# ── Providers from providers.yml ──────────────────────────────────────────────


def _load_providers_for_phase(phase: int) -> list[tuple[str, str]]:
    """Build provider menu from providers.yml. Falls back to static list."""
    phase_key = f"phase{phase}"
    try:
        from cloud_client import _load_config
        opts = []
        for p in _load_config().get("providers", []):
            if p.get("phase") == phase_key:
                opts.append((p["id"], f"{p.get('name', p['id'])}  ({p.get('model', '')})"))
        if opts:
            return opts
    except Exception:
        pass
    return [
        ("fireworks_batch_phase1", "Fireworks  (qwen3-vl-30b-a3b-thinking)"),
        ("dashscope_batch_phase1", "DashScope  (qwen-plus)"),
    ]




# ── STAGE 2 — CRITIQUE ────────────────────────────────────────────────────────


def _stage_critique(session: Session) -> None:
    os.system("clear")
    _banner()


    modes = [
        ("batch",    "Cloud batch     —  build JSONL → submit → poll → collect"),
        ("dry",      "Dry run         —  build JSONL only, no submit (cost estimate)"),
        ("overnight","Local overnight —  Ollama, all pending entries unattended"),
    ]
    choice = _numbered_menu("CRITIQUE  —  Select mode", modes, back_label="Back")
    if choice == "0":
        return


    # Phase
    print()
    phase_opts = [
        ("1",   "Phase 1  —  Linguistic scan  (fast, low cost)"),
        ("2",   "Phase 2  —  Content coherence (deep, higher cost)"),
        ("1,2", "Both phases in one request"),
    ]
    phase_str = _numbered_menu("Select Phase", phase_opts, back_label="Back")
    if phase_str == "0":
        return
    phases    = sorted(set(int(p) for p in phase_str.split(",")))
    phase_arg = ",".join(str(p) for p in phases)


    if choice in ("batch", "dry"):
        # Provider
        print()
        prov_opts = _load_providers_for_phase(phases[0])
        provider  = _numbered_menu("Select Provider", prov_opts, back_label="Back")
        if provider == "0":
            return


        cmd = [
            sys.executable, "batch_pipeline.py",
            "--lang",     session.lang,
            "--version",  session.version,
            "--year",     str(session.year),
            "--phase",    phase_arg,
            "--provider", provider,
        ]
        if session.local_file:
            cmd += ["--local", session.local_file]
        if choice == "dry":
            cmd.append("--dry-run")


        _run_cmd(cmd)


    elif choice == "overnight":
        cmd = [
            sys.executable, "critic_v3.py",
            "--lang",    session.lang,
            "--version", session.version,
            "--year",    str(session.year),
            "--mode",    "overnight",
            "--provider","auto",
        ]
        _run_cmd(cmd)




# ── STAGE 3 — REVIEW ─────────────────────────────────────────────────────────


def _stage_review(session: Session) -> None:
    os.system("clear")
    _banner()


    opts = [
        ("flags",  "Flags report    —  FLAG verdicts only (human-readable)"),
        ("all",    "Full report     —  all verdicts  (PASS + FLAG + SKIP)"),
        ("genome", "Genome status   —  fragment count, confidence, top patterns"),
        # Publish slot — wired when genome publish is ready
        # ("publish", _c("Publish genome  —  [coming soon]", _DIM)),
    ]
    choice = _numbered_menu("REVIEW  —  Select report", opts, back_label="Back")
    if choice == "0":
        return


    if choice in ("flags", "all"):
        verdict = "ALL" if choice == "all" else "FLAG"


        print()
        phase_opts = [("1", "Phase 1"), ("2", "Phase 2"), ("1,2", "Both")]
        phase_str  = _numbered_menu("Select Phase", phase_opts, back_label="Back")
        if phase_str == "0":
            return


        # Optional: specific files
        print()
        print(_c(_box_top("RESULT FILES  (optional)"), _CYAN, _BOLD))
        print(_c(_box_row("  Press Enter → auto-resolve from audit log."), _DIM))
        print(_c(_box_bot(), _CYAN, _BOLD))
        print()


        input_file = results_file = ""
        if _confirm("Pick specific input/results files?", default=False):
            _paths.ensure_dirs()
            input_file   = _file_picker("batch_input  file",  _paths.BATCH_INPUT_DIR,  "*.jsonl")
            results_file = _file_picker("batch_output file",  _paths.BATCH_OUTPUT_DIR, "*.jsonl")


        cmd = [
            sys.executable, "review_flags.py",
            "--lang",    session.lang,
            "--version", session.version,
            "--year",    str(session.year),
            "--phase",   phase_str,
            "--verdict", verdict,
        ]
        if input_file:   cmd += ["--input",   input_file]
        if results_file: cmd += ["--results", results_file]
        _run_cmd(cmd)


    elif choice == "genome":
        _show_genome_status(session)




# ── Genome status (inline — no subprocess) ───────────────────────────────────


def _show_genome_status(session: Session) -> None:
    """
    Inline genome summary with box-drawing UI.
    Publish slot reserved for future flow.
    """
    from genome import load_genome
    from collections import Counter


    os.system("clear")
    _banner()


    genome = load_genome(session.lang, session.version, session.year)


    print(_c(_box_top(f"GENOME  —  {session.lang} / {session.version} / {session.year}"), _CYAN, _BOLD))


    if not genome:
        print(_c(_box_row(""), _WHITE))
        print(_c(_box_row(
            f"  {_c('No genome found.', _ORANGE, _BOLD)}  "
            "Run CRITIQUE first to build one."
        ), _WHITE))
        print(_c(_box_row(""), _WHITE))
        print(_c(_box_bot(), _CYAN, _BOLD))
        input(_c("\n  Press Enter to continue…", _DIM))
        return


    lbl_w = 22
    def grow(label: str, value: str, color: str = _WHITE) -> None:
        lbl = _c(f"  {label:<{lbl_w}}", _YELLOW, _BOLD)
        print(_c(_box_row(f"{lbl}  {_c(value, color, _BOLD)}"), _WHITE))


    grow("Entries reviewed",  str(genome.total_entries_reviewed), _CAMO)
    grow("Total pauses",      str(genome.total_pauses),           _ORANGE)
    grow("Genome version",    genome.genome_version,              _CYAN)
    grow("Last updated",      (genome.updated_at[:19] if genome.updated_at else "—"), _DIM)
    grow("Fragments total",   str(len(genome.fragments)),         _WHITE)


    if genome.fragments:
        # By category
        print(_c(_box_mid("FRAGMENTS BY CATEGORY"), _CYAN, _BOLD))
        cats    = Counter(f.category.value for f in genome.fragments)
        max_cnt = max(cats.values())
        for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
            bar_filled = int((count / max_cnt) * 20)
            bar        = _c("█" * bar_filled, _CAMO) + _c("░" * (20 - bar_filled), _DIM)
            cnt        = _c(f"{count:>3}", _WHITE, _BOLD)
            cat_lbl    = _c(f"  {cat:<25}", _YELLOW)
            print(_c(_box_row(f"{cat_lbl}  {cnt}  {bar}"), _WHITE))


        # Top by confidence
        print(_c(_box_mid("TOP FRAGMENTS  (confidence)"), _CYAN, _BOLD))
        top = sorted(genome.fragments, key=lambda f: f.confidence, reverse=True)[:5]
        for i, frag in enumerate(top, 1):
            conf = _c(f"{frag.confidence:.2f}", _CAMO, _BOLD)
            cat  = _c(f"[{frag.category.value}]", _YELLOW)
            pat  = frag.pattern[:42] + ("…" if len(frag.pattern) > 42 else "")
            ev   = _c(f"  ({len(frag.evidence_dates)} dates)", _DIM)
            print(_c(_box_row(f"  {i}. {cat}  {conf}  {pat}{ev}"), _WHITE))
    else:
        print(_c(_box_row("  No fragments yet."), _DIM))


    # ── Publish slot ─────────────────────────────────────────────────────────
    # Will be wired to promote_genome / publish flow in next iteration.
    print(_c(_box_mid("ACTIONS"), _CYAN, _BOLD))
    print(_c(_box_row(
        _c("  [coming soon]  Publish genome — promote fragments to production", _DIM)
    ), _DIM))
    print(_c(_box_bot(), _CYAN, _BOLD))


    input(_c("\n  Press Enter to continue…", _DIM))




# ── Session status row ────────────────────────────────────────────────────────


def _print_session_row(session: Session) -> None:
    try:
        from lang_registry import get as get_lang
        lang_name = get_lang(session.lang).language_name
    except ValueError:
        lang_name = session.lang


    src = Path(session.local_file).name if session.local_file else "GitHub"
    row = "".join([
        _c(f"  {session.lang}", _CAMO, _BOLD),
        _c(f" / {session.version}", _CAMO),
        _c(f" / {session.year}", _CAMO),
        _c(f"   {lang_name}", _DIM),
        _c(f"   source: {src}", _DIM),
    ])
    print(_c(_box_top("ACTIVE DATASET"), _CYAN, _BOLD))
    print(_c(_box_row(row), _WHITE))
    print(_c(_box_bot(), _CYAN, _BOLD))
    print()




# ── Command runner ────────────────────────────────────────────────────────────


def _run_cmd(cmd: list[str]) -> None:
    cmd_str = " \\\n      ".join(cmd)
    print()
    print(_c(_box_top("COMMAND"), _CYAN, _BOLD))
    print(_c(_box_row(f"  {_c(cmd_str, _WHITE)}"), _CYAN))
    print(_c(_box_bot(), _CYAN, _BOLD))
    print()
    if not _confirm("Run?"):
        return
    print()
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(_c(f"\n  ⚠️  Exited with code {result.returncode}", _ORANGE, _BOLD))
    input(_c("\n  Press Enter to continue…", _DIM))




# ── Main menu ─────────────────────────────────────────────────────────────────


_MAIN_OPTIONS = [
    ("prepare",  "PREPARE   —  set lang / version / year / source file"),
    ("critique", "CRITIQUE  —  run the AI critic  (cloud batch or local)"),
    ("review",   "REVIEW    —  flags report / genome status"),
]




def main() -> None:
    session = _load_session()


    while True:
        os.system("clear")
        _banner()
        _print_session_row(session)


        choice = _numbered_menu("MAIN MENU", _MAIN_OPTIONS, back_label="Exit")


        if choice == "0":
            print(_c("\n  Goodbye.\n", _DIM))
            break
        elif choice == "prepare":
            new = _stage_prepare(session)
            if new is not session:
                session = new
                _save_session(session)
        elif choice == "critique":
            _stage_critique(session)
        elif choice == "review":
            _stage_review(session)




if __name__ == "__main__":
    main()

"""
dashboard.py — Seed generation checkpoint dashboard.
Interactive launcher. No flags to memorize.

Run:  python3 -m seed_generation.dashboard

Auto-discovers every checkpoint under seed_generation/data/output/*/,
shows each as a progress bar (done/pending/total), and lets you resume
one by number — no --seed/--lang/--version/--provider/--model retyping.

UI style: ASCII box-drawing + ANSI colors (matches GEP's main.py).
"""

from __future__ import annotations

import glob
import os

from seed_generation.generate_from_seed import generate_from_seed
from seed_generation.shared.generation_core import checkpoint_status

# ── ANSI palette (matches GEP main.py) ──────────────────────────────────────
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_WHITE = "\033[97m"
_CAMO = "\033[38;2;120;134;107m"  # OK / progress
_ORANGE = "\033[38;2;255;90;45m"  # warning / mismatch

_W = 70


def _c(text: str, *codes: str) -> str:
    return "".join(codes) + text + _RESET


def _box_top(title: str = "") -> str:
    if title:
        pad = _W - len(title) - 2
        inner = "─" * (pad // 2) + f" {title} " + "─" * (pad - pad // 2)
    else:
        inner = "─" * _W
    return f"╔{inner}╗"


def _box_bot() -> str:
    return f"╚{'─' * _W}╝"


def _box_row(text: str) -> str:
    import re

    clean = re.sub(r"\033\[[0-9;]*m", "", text)
    padding = _W - len(clean)
    return f"║ {text}{' ' * max(0, padding - 1)}║"


def _bar(done: int, total: int, width: int = 24) -> str:
    if total <= 0:
        filled = 0
    else:
        filled = int((done / total) * width)
    return _c("█" * filled, _CAMO) + _c("░" * (width - filled), _DIM)


# ── Discovery ────────────────────────────────────────────────────────────────

_OUTPUT_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "output"
)


def discover_checkpoints(output_root: str = _OUTPUT_ROOT) -> list[dict]:
    """Find every checkpoint under output_root, with its status attached.

    Returns entries sorted by pending count descending (most work left first)
    then by checkpoint path, so the busiest run is always item [1].
    """
    pattern = os.path.join(output_root, "*", "generate_from_seed_checkpoint_*.json")
    entries = []
    for checkpoint_file in sorted(glob.glob(pattern)):
        status = checkpoint_status(checkpoint_file)
        if status is None:
            continue
        total = _seed_total(status.get("checkpoint_seed_path"))
        status["seed_exists"] = total is not None
        status["total"] = total if total is not None else status["done"]
        status["pending"] = max(status["total"] - status["done"], 0)
        entries.append(status)
    return sorted(entries, key=lambda s: (-s["pending"], s["checkpoint_file"]))


def _seed_total(seed_path: str | None) -> int | None:
    if not seed_path or not os.path.exists(seed_path):
        return None
    import json

    try:
        with open(seed_path, encoding="utf-8") as f:
            return len(json.load(f))
    except (OSError, json.JSONDecodeError):
        return None


# ── UI ───────────────────────────────────────────────────────────────────────


def _banner() -> None:
    print()
    print(_c(_box_top("SEED GENERATION — Checkpoint Dashboard"), _CYAN, _BOLD))
    print(
        _c(
            _box_row("  Devotional generation progress  //  resume without flags"),
            _CYAN,
        )
    )
    print(_c(_box_bot(), _CYAN, _BOLD))
    print()


def _print_checkpoints(entries: list[dict]) -> None:
    print(_c(_box_top("IN-PROGRESS CHECKPOINTS"), _CYAN, _BOLD))
    if not entries:
        print(_c(_box_row("  (none found)"), _DIM))
    for i, e in enumerate(entries, 1):
        lang = e.get("master_lang") or "?"
        version = e.get("master_version") or "?"
        model = e.get("model") or e.get("provider") or "?"
        num = _c(f"  [{i}]", _YELLOW, _BOLD)
        header = f"{num}  {lang}/{version}  —  {model}"
        print(_c(_box_row(header), _WHITE))
        bar = _bar(e["done"], e["total"])
        counts = _c(f"{e['done']}/{e['total']} done, {e['pending']} pending", _WHITE)
        print(_c(_box_row(f"       {bar}  {counts}"), _WHITE))
        if not e.get("seed_exists", True):
            print(
                _c(
                    _box_row(
                        "       ⚠ seed file referenced by this checkpoint no longer exists"
                    ),
                    _ORANGE,
                )
            )
        ts = (e.get("timestamp") or "—")[:19]
        print(_c(_box_row(f"       last saved {ts}"), _DIM))
    print(_c(_box_bot(), _CYAN, _BOLD))


def _choose(entries: list[dict]) -> dict | None:
    if not entries:
        return None
    try:
        raw = input(
            _c("\n  Resume which? [number, or Enter to exit]: ", _YELLOW, _BOLD)
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not raw:
        return None
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(entries):
            return entries[idx]
    except ValueError:
        pass
    print(_c("  Invalid choice.", _ORANGE))
    return None


def _resume(entry: dict) -> None:
    seed_path = entry.get("checkpoint_seed_path")
    if not seed_path or not os.path.exists(seed_path):
        print(_c(f"\n  ⚠ Seed file not found: {seed_path}", _ORANGE, _BOLD))
        input(_c("\n  Press Enter to continue…", _DIM))
        return
    provider = entry.get("provider")
    if not provider:
        print(
            _c(
                "\n  ⚠ This checkpoint predates provider/model tracking — cannot auto-resume.",
                _ORANGE,
                _BOLD,
            )
        )
        print(
            _c(
                "    Re-run generate_from_seed.py directly with the original --provider/--model.",
                _DIM,
            )
        )
        input(_c("\n  Press Enter to continue…", _DIM))
        return
    print()
    generate_from_seed(
        seed_path=seed_path,
        master_lang=entry["master_lang"],
        master_version=entry["master_version"],
        provider=provider,
        output_dir=entry.get("output_dir") or os.path.dirname(entry["checkpoint_file"]),
        model=entry.get("model"),
        resume=True,
    )
    input(_c("\n  Press Enter to continue…", _DIM))


def run_dashboard(output_root: str = _OUTPUT_ROOT) -> None:
    """The dashboard's interactive loop — reusable from a wrapping menu
    (seed_generation/main.py) as well as standalone `python -m ...dashboard`.
    """
    while True:
        os.system("clear")
        _banner()
        entries = discover_checkpoints(output_root)
        _print_checkpoints(entries)
        chosen = _choose(entries)
        if chosen is None:
            print(_c("\n  Goodbye.\n", _DIM))
            break
        _resume(chosen)


def main() -> None:
    run_dashboard()


if __name__ == "__main__":
    main()

"""
main.py — Seed generation pipeline. Interactive launcher, no flags to memorize.

Run:  python3 -m seed_generation.main

Two stages:
  1. Generate seed    — fetch verse citations/text for a new lang/year
                         (launches seed_extractor_fetch.py — has its own
                         prompts, fetches from remote sources, unchanged here)
  2. Generate content — checkpoint dashboard: status + resume, no flags
                         (seed_generation/dashboard.py)

UI style: ASCII box-drawing + ANSI colors (matches GEP's main.py).
"""

from __future__ import annotations

import subprocess
import sys

from seed_generation.dashboard import run_dashboard

# ── ANSI palette (matches GEP main.py / dashboard.py) ───────────────────────
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_WHITE = "\033[97m"
_ORANGE = "\033[38;2;255;90;45m"

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


def _banner() -> None:
    print()
    print(_c(_box_top("S E E D   G E N E R A T I O N"), _CYAN, _BOLD))
    print(_c(_box_row("  Devotional Content Pipeline"), _CYAN))
    print(_c(_box_bot(), _CYAN, _BOLD))
    print()


_MAIN_OPTIONS = [
    ("build_seed", "Generate seed    —  fetch verses for a new lang/year"),
    ("generate", "Generate content —  status + resume checkpoints"),
]


def _numbered_menu() -> str:
    print(_c(_box_top("MAIN MENU"), _CYAN, _BOLD))
    for i, (_, label) in enumerate(_MAIN_OPTIONS, 1):
        num = _c(f"  [{i}]", _YELLOW, _BOLD)
        print(_c(_box_row(f"{num}  {label}"), _WHITE))
    print(_c(_box_row(""), _WHITE))
    zero = _c("  [0]", _DIM)
    print(_c(_box_row(f"{zero}  Exit"), _DIM))
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
            if 0 <= idx < len(_MAIN_OPTIONS):
                return _MAIN_OPTIONS[idx][0]
        except ValueError:
            pass
        print(_c("  Invalid — try again.", _ORANGE))


def _build_seed() -> None:
    cmd = [sys.executable, "-m", "seed_generation.tools.seed_extractor_fetch"]
    print()
    print(_c(_box_top("LAUNCHING"), _CYAN, _BOLD))
    print(_c(_box_row(f"  {' '.join(cmd)}"), _CYAN))
    print(
        _c(
            _box_row("  Fetches from remote sources — has its own prompts."),
            _DIM,
        )
    )
    print(_c(_box_bot(), _CYAN, _BOLD))
    print()
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(_c(f"\n  ⚠  Exited with code {result.returncode}", _ORANGE, _BOLD))
    input(_c("\n  Press Enter to continue…", _DIM))


def main() -> None:
    while True:
        _banner()
        choice = _numbered_menu()
        if choice == "0":
            print(_c("\n  Goodbye.\n", _DIM))
            break
        elif choice == "build_seed":
            _build_seed()
        elif choice == "generate":
            run_dashboard()


if __name__ == "__main__":
    main()

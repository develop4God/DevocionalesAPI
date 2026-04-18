"""
models_helper.py — GEP Critic v3
Single responsibility: discover available Ollama models and recommend
the best one for the current machine's available memory.

Run standalone to see what's available:
  python models_helper.py
  python models_helper.py --pull          # suggest pull commands
  python models_helper.py --check llama3.2:3b
"""

import json
import shutil
import subprocess
import urllib.request
import urllib.error
from dataclasses import dataclass

OLLAMA_URL      = "http://localhost:11434"
OLLAMA_API_TAGS = f"{OLLAMA_URL}/api/tags"


# ── Known models catalogue ─────────────────────────────────────────────────────
# ram_gb: minimum RAM to run comfortably (quantized, CPU+GPU mixed)
# quality: subjective score for devotional text review (1–5)
# multilingual: handles non-Latin scripts well

@dataclass
class ModelSpec:
    tag: str
    ram_gb: float
    quality: int        # 1–5 for this task
    multilingual: bool
    notes: str


CATALOGUE: list[ModelSpec] = [
    # ── Tiny (≤4 GB) ──────────────────────────────────────────────────────────
    ModelSpec("qwen2.5:3b",          2.5,  3, True,  "Good multilingual, limited reasoning"),
    ModelSpec("llama3.2:3b",         2.5,  3, False, "English-focused, fast"),
    ModelSpec("gemma2:2b",           2.0,  2, False, "Very fast, low quality for nuanced text"),

    # ── Small (4–8 GB) ────────────────────────────────────────────────────────
    ModelSpec("qwen2.5:7b",          5.0,  4, True,  "Best multilingual under 8GB — recommended"),
    ModelSpec("llama3.1:8b",         6.0,  4, False, "Strong English reasoning"),
    ModelSpec("mistral:7b",          5.0,  3, False, "Good grammar, weaker multilingual"),
    ModelSpec("gemma2:9b",           7.0,  4, False, "Strong reasoning, English-focused"),
    ModelSpec("phi3.5:3.8b",         3.5,  3, False, "Microsoft, compact, decent quality"),

    # ── Medium (8–16 GB) ──────────────────────────────────────────────────────
    ModelSpec("qwen2.5:14b",        10.0,  5, True,  "Excellent multilingual, fits ~11GB free"),
    ModelSpec("llama3.1:70b-q4",    14.0,  5, False, "Top English quality, large"),
    ModelSpec("mistral-nemo:12b",    9.0,  4, True,  "Good multilingual, 12B Mistral"),

    # ── Large (16+ GB) ────────────────────────────────────────────────────────
    ModelSpec("qwen2.5:27b",        18.0,  5, True,  "Needs ~18GB — likely too large for 11GB free"),
    ModelSpec("llama3.3:70b-q4",    40.0,  5, False, "Server-grade only"),
]

# ── RAM detection ──────────────────────────────────────────────────────────────

def get_free_ram_gb() -> float | None:
    """Returns free RAM in GB from /proc/meminfo (Linux) or vm_stat (macOS)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / 1_048_576
    except FileNotFoundError:
        pass
    try:
        result = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=3
        )
        pages_free = 0
        for line in result.stdout.splitlines():
            if "Pages free" in line or "Pages inactive" in line:
                pages_free += int(line.split(":")[1].strip().rstrip("."))
        return (pages_free * 4096) / 1_073_741_824
    except Exception:
        return None


# ── Ollama discovery ───────────────────────────────────────────────────────────

def list_installed_models() -> list[dict]:
    """Returns list of installed models from Ollama API."""
    try:
        req = urllib.request.Request(
            OLLAMA_API_TAGS,
            headers={"User-Agent": "gep-critic/3.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data.get("models", [])
    except (urllib.error.URLError, json.JSONDecodeError):
        return []


def ollama_running() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}", timeout=3)
        return True
    except Exception:
        return False


# ── Recommendation logic ───────────────────────────────────────────────────────

def recommend(
    free_ram_gb: float | None,
    installed_tags: set[str],
    multilingual_needed: bool = True,
) -> ModelSpec | None:
    """
    Returns the best available model for the task.
    Priority: installed > quality > multilingual fit.
    """
    ram_limit = (free_ram_gb * 0.85) if free_ram_gb else 999  # 85% headroom

    candidates = [
        m for m in CATALOGUE
        if m.ram_gb <= ram_limit
        and (not multilingual_needed or m.multilingual)
    ]

    # Prefer installed models first
    installed_candidates = [m for m in candidates if m.tag in installed_tags]
    pool = installed_candidates if installed_candidates else candidates

    if not pool:
        return None

    # Sort by quality desc, then ram_gb asc (prefer larger within same quality)
    pool.sort(key=lambda m: (-m.quality, m.ram_gb))
    return pool[0]


# ── Public API (used by ollama_client.py) ─────────────────────────────────────

def get_best_model(multilingual: bool = True) -> str:
    """
    Returns the best model tag for current machine.
    Falls back to qwen2.5:7b if detection fails.
    """
    installed_raw  = list_installed_models()
    installed_tags = {m["name"] for m in installed_raw}
    free_ram       = get_free_ram_gb()
    spec           = recommend(free_ram, installed_tags, multilingual)
    return spec.tag if spec else "qwen2.5:7b"


def get_model_for_key(key: str) -> str:
    """
    Resolves a model key to a tag.
    Keys: 'auto', 'fast', 'best', or any direct Ollama tag.
    """
    if key == "auto":
        return get_best_model(multilingual=True)
    if key == "fast":
        # Always pick smallest installed model
        installed_raw  = list_installed_models()
        installed_tags = {m["name"] for m in installed_raw}
        free_ram       = get_free_ram_gb()
        candidates     = [
            m for m in CATALOGUE
            if m.tag in installed_tags and m.ram_gb <= ((free_ram or 999) * 0.85)
        ]
        if candidates:
            return sorted(candidates, key=lambda m: m.ram_gb)[0].tag
        return "qwen2.5:3b"
    if key == "best":
        installed_raw  = list_installed_models()
        installed_tags = {m["name"] for m in installed_raw}
        free_ram       = get_free_ram_gb()
        spec           = recommend(free_ram, installed_tags, multilingual=True)
        return spec.tag if spec else "qwen2.5:7b"
    # Direct tag passthrough
    return key


# ── CLI ────────────────────────────────────────────────────────────────────────

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="GEP Critic — Models Helper")
    parser.add_argument("--pull",  action="store_true", help="Show pull commands for recommended models")
    parser.add_argument("--check", metavar="TAG",       help="Check if a specific model tag is available")
    args = parser.parse_args()

    free_ram = get_free_ram_gb()
    running  = ollama_running()
    installed_raw  = list_installed_models()
    installed_tags = {m["name"] for m in installed_raw}

    print(f"\n  🖥️  Free RAM : {free_ram:.1f} GB" if free_ram else "\n  🖥️  Free RAM : unknown")
    print(f"  ⚡ Ollama   : {'running' if running else 'NOT running'}")

    if installed_raw:
        print(f"\n  Installed models ({len(installed_raw)}):")
        for m in installed_raw:
            size_gb = m.get("size", 0) / 1e9
            print(f"    {m['name']:<40} {size_gb:.1f} GB")

    ram_limit = (free_ram * 0.85) if free_ram else 999
    print(f"\n  Models that fit your machine (≤ {ram_limit:.1f} GB RAM):")
    for spec in CATALOGUE:
        if spec.ram_gb <= ram_limit:
            installed_marker = "✅" if spec.tag in installed_tags else "  "
            ml_marker        = "🌍" if spec.multilingual else "  "
            print(f"    {installed_marker} {ml_marker} {'★'*spec.quality:<5} {spec.tag:<35} ~{spec.ram_gb:.0f}GB  {spec.notes}")

    rec = recommend(free_ram, installed_tags, multilingual_needed=True)
    if rec:
        installed_note = "(already installed)" if rec.tag in installed_tags else "(needs: ollama pull " + rec.tag + ")"
        print(f"\n  ⭐ Recommended for this task: {rec.tag}  {installed_note}")
        print(f"     {rec.notes}")

    if args.check:
        found = args.check in installed_tags
        print(f"\n  Check '{args.check}': {'✅ installed' if found else '❌ not installed'}")

    if args.pull:
        print(f"\n  Pull commands for top multilingual models that fit your machine:")
        for spec in sorted(
            [m for m in CATALOGUE if m.ram_gb <= ram_limit and m.multilingual],
            key=lambda m: -m.quality
        )[:4]:
            print(f"    ollama pull {spec.tag}")

    print()


if __name__ == "__main__":
    _cli()

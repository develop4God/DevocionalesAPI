import json
import re
import unicodedata
from pathlib import Path

# ── thresholds ────────────────────────────────────────────────────────────────
REFLEXION_MIN = 800
ORACION_MIN   = 150

# ── Amén normalizer ───────────────────────────────────────────────────────────
def _normalize(word: str) -> str:
    return unicodedata.normalize("NFD", word).encode("ascii", "ignore").decode().lower()

# ── validator (with Amén fix applied) ─────────────────────────────────────────
def run_checks(oracion: str, reflexion: str, lang: str = "de") -> list[str]:
    issues = []
    prayer_endings = {"de": ["Amen"], "es": ["Amen", "Amén"], "en": ["Amen"]}
    endings = prayer_endings.get(lang, ["Amen"])

    # 1. Min length
    if len(reflexion.strip()) < REFLEXION_MIN:
        issues.append(f"reflexion too short: {len(reflexion.strip())} chars")
    if len(oracion.strip()) < ORACION_MIN:
        issues.append(f"oracion too short: {len(oracion.strip())} chars")

    # 2. Ends with Amen (accent-normalised)
    last_word = oracion.strip().rstrip(".!,;").split()[-1]
    if not any(_normalize(e) == _normalize(last_word) for e in endings):
        issues.append(f"does not end with Amen — last word: '{last_word}'")

    # 3. Double Amen artifact
    tail_matches = list(re.finditer(r'\bAm[eé]n\b', oracion[-120:], re.IGNORECASE))
    if len(tail_matches) >= 2:
        issues.append("double Amen artifact in closing")

    # 4. Consecutive duplicate words — oracion
    words = oracion.split()
    for i in range(len(words) - 1):
        w1 = words[i].strip(".,;:!?").lower()
        w2 = words[i+1].strip(".,;:!?").lower()
        if w1 == w2 and len(w1) > 2:
            issues.append(f"consecutive duplicate in oracion: '{words[i]} {words[i+1]}'")
            break

    # 5. Consecutive duplicate words — reflexion
    words = reflexion.split()
    for i in range(len(words) - 1):
        w1 = words[i].strip(".,;:!?").lower()
        w2 = words[i+1].strip(".,;:!?").lower()
        if w1 == w2 and len(w1) > 2:
            issues.append(f"consecutive duplicate in reflexion: '{words[i]} {words[i+1]}'")
            break

    return issues


def validate_progress_json(json_path: str):
    path = Path(json_path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    entries = data.get("completed", {})
    print(f"Entries to validate: {len(entries)}\n{'='*60}")

    failed = {}
    for date, entry in sorted(entries.items()):
        oracion   = entry.get("oracion", "")
        reflexion = entry.get("reflexion", "")
        lang      = entry.get("language", "de")
        issues    = run_checks(oracion, reflexion, lang)
        if issues:
            failed[date] = {"id": entry.get("id"), "issues": issues}

    print(f"\nResults: {len(entries) - len(failed)}/{len(entries)} passed\n")
    if failed:
        print(f"{'='*60}\nFAILED ENTRIES ({len(failed)})\n{'='*60}")
        for date, info in failed.items():
            print(f"\n❌ {date} — {info['id']}")
            for issue in info["issues"]:
                print(f"   • {issue}")
    else:
        print("✅ ALL ENTRIES PASSED")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python validation_helper.py <path_to_progress_json>")
        sys.exit(1)
    validate_progress_json(sys.argv[1])

"""
test_lang_registry.py — GEP Critic v3 language registry tests.

Validates:
    - All registered languages resolve correctly
    - Unknown languages raise ValueError with helpful messages
    - Version validation works for registered and unknown versions
    - Helper functions return correct data structures
    - Registry is consistent across all access methods
    - Edge cases and error messages are user-friendly
"""

import sys
import traceback
from typing import List, Tuple

import lang_registry

PASS = "✅ PASS"
FAIL = "❌ FAIL"

results: List[Tuple[str, str, str]] = []


def check(name: str, condition: bool, detail: str = ""):
    """Record test result."""
    icon = PASS if condition else FAIL
    results.append((icon, name, detail))
    print(f"  {icon}  {name}" + (f" — {detail}" if detail else ""))


# ════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  🧪 GEP Critic v3 — Language Registry Tests")
print("═" * 60)

# ── Test 1: List all registered languages ────────────────────────────────────
print("\n[1] Testing list_languages()...")
langs = lang_registry.list_languages()
check("list_languages() returns a list", isinstance(langs, list))
check("At least 10 languages registered", len(langs) >= 10, f"found {len(langs)}")
check("Languages are sorted", langs == sorted(langs))

# ── Test 2: Validate all registered languages resolve ────────────────────────
print("\n[2] Testing get() for all registered languages...")
expected_langs = ["ar", "de", "en", "es", "fil", "fr", "hi", "ja", "pt", "zh"]
for lang in expected_langs:
    try:
        cfg = lang_registry.get(lang)
        check(f"get('{lang}') returns LangConfig", cfg is not None)
        check(f"  {lang}: code matches", cfg.code == lang)
        check(f"  {lang}: has language_name", len(cfg.language_name) > 0)
        check(f"  {lang}: has country", len(cfg.country) > 0)
        check(f"  {lang}: has known_versions", len(cfg.known_versions) > 0)
        check(f"  {lang}: has labels", len(cfg.labels) == 4)
        check(f"  {lang}: has filename_pattern", "{year}" in cfg.filename_pattern)
        check(f"  {lang}: has persona", len(cfg.persona) > 50)
    except ValueError as e:
        check(f"get('{lang}') raises ValueError", False, str(e))

# ── Test 3: Unknown language raises ValueError ───────────────────────────────
print("\n[3] Testing unknown language...")
try:
    lang_registry.get("xx")
    check("get('xx') raises ValueError", False, "Should have raised ValueError")
except ValueError as e:
    error_msg = str(e)
    check("get('xx') raises ValueError", True)
    check("  Error mentions 'not registered'", "not registered" in error_msg)
    check("  Error lists supported languages", "Supported:" in error_msg)
    check("  Error suggests how to add", "To add it:" in error_msg)

# ── Test 4: 'tl' (old Tagalog code) is NOT registered ────────────────────────
print("\n[4] Testing 'tl' (old code) is not registered...")
try:
    lang_registry.get("tl")
    check(
        "get('tl') raises ValueError",
        False,
        "Should have raised ValueError — tl replaced with fil",
    )
except ValueError as e:
    error_msg = str(e)
    check("get('tl') raises ValueError", True, "Correct — tl replaced with fil")
    check("  Error suggests 'fil' in supported list", "fil" in error_msg.lower())

# ── Test 5: 'fil' (correct Filipino code) IS registered ──────────────────────
print("\n[5] Testing 'fil' (correct Filipino ISO code)...")
try:
    cfg = lang_registry.get("fil")
    check("get('fil') returns LangConfig", cfg is not None)
    check("  fil: language_name is 'Tagalog'", cfg.language_name == "Tagalog")
    check("  fil: country is 'Philippines'", cfg.country == "Philippines")
    check("  fil: MBB05 in known_versions", "MBB05" in cfg.known_versions)
    check("  fil: ASND in known_versions", "ASND" in cfg.known_versions)
    check(
        "  fil: ADB NOT in known_versions",
        "ADB" not in cfg.known_versions,
        "ADB removed per issue requirements",
    )
except ValueError as e:
    check("get('fil') should not raise ValueError", False, str(e))

# ── Test 6: Version validation (happy path) ───────────────────────────────────
print("\n[6] Testing validate_version() — happy path...")
try:
    lang_registry.validate_version("es", "RVR1960")
    check("validate_version('es', 'RVR1960') passes", True)
    lang_registry.validate_version("fil", "MBB05")
    check("validate_version('fil', 'MBB05') passes", True)
    lang_registry.validate_version("pt", "NVI")
    check("validate_version('pt', 'NVI') passes", True)
except ValueError as e:
    check("validate_version() should not raise on valid input", False, str(e))

# ── Test 7: Version validation (unknown version) ──────────────────────────────
print("\n[7] Testing validate_version() — unknown version...")
try:
    lang_registry.validate_version("fil", "INVALID")
    check(
        "validate_version('fil', 'INVALID') raises ValueError",
        False,
        "Should have raised ValueError",
    )
except ValueError as e:
    error_msg = str(e)
    check("validate_version('fil', 'INVALID') raises ValueError", True)
    check("  Error mentions version not registered", "not registered" in error_msg)
    check("  Error lists supported versions", "Supported versions" in error_msg)
    check("  Error suggests how to add", "To add it:" in error_msg)

# ── Test 8: Version validation (unknown language) ─────────────────────────────
print("\n[8] Testing validate_version() — unknown language...")
try:
    lang_registry.validate_version("ko", "KJV")
    check(
        "validate_version('ko', 'KJV') raises ValueError",
        False,
        "Should have raised ValueError",
    )
except ValueError as e:
    error_msg = str(e)
    check("validate_version('ko', 'KJV') raises ValueError", True)
    check("  Error mentions language not registered", "not registered" in error_msg)

# ── Test 9: Helper functions return correct types ─────────────────────────────
print("\n[9] Testing helper functions...")
try:
    native_info = lang_registry.get_native_speaker_info("es")
    check("get_native_speaker_info('es') returns tuple", isinstance(native_info, tuple))
    check("  Returns (language, country)", len(native_info) == 2)
    check("  language is non-empty", len(native_info[0]) > 0)
    check("  country is non-empty", len(native_info[1]) > 0)

    labels = lang_registry.get_section_labels("es")
    check("get_section_labels('es') returns dict", isinstance(labels, dict))
    check("  Has 'verse' key", "verse" in labels)
    check("  Has 'reflection' key", "reflection" in labels)
    check("  Has 'prayer' key", "prayer" in labels)
    check("  Has 'meditate' key", "meditate" in labels)

    persona = lang_registry.get_persona("es")
    check("get_persona('es') returns string", isinstance(persona, str))
    check("  Persona is non-trivial", len(persona) > 50)

    versions = lang_registry.list_versions("es")
    check("list_versions('es') returns list", isinstance(versions, list))
    check("  Returns non-empty list", len(versions) > 0)

    pattern = lang_registry.get_filename_pattern("es", "RVR1960")
    check(
        "get_filename_pattern('es', 'RVR1960') returns string", isinstance(pattern, str)
    )
    check("  Pattern contains {year} placeholder", "{year}" in pattern)

except Exception as e:
    check("Helper functions should not raise on valid input", False, str(e))
    traceback.print_exc()

# ── Test 10: All registered languages have consistent data ────────────────────
print("\n[10] Testing data consistency across all languages...")
for lang in lang_registry.list_languages():
    try:
        cfg = lang_registry.get(lang)

        # Check all required fields are present and non-empty
        check(f"  {lang}: code is '{lang}'", cfg.code == lang)
        check(f"  {lang}: language_name non-empty", len(cfg.language_name) > 0)
        check(f"  {lang}: country non-empty", len(cfg.country) > 0)
        check(f"  {lang}: at least 1 known version", len(cfg.known_versions) >= 1)

        # Check labels structure
        required_label_keys = {"verse", "reflection", "prayer", "meditate"}
        check(
            f"  {lang}: labels has all 4 keys",
            set(cfg.labels.keys()) == required_label_keys,
        )

        # Check filename pattern placeholders
        check(
            f"  {lang}: filename_pattern has {{year}}", "{year}" in cfg.filename_pattern
        )

        # Check persona is substantial
        check(f"  {lang}: persona is substantial", len(cfg.persona) > 50)

        # Validate all registered versions work
        for version in cfg.known_versions:
            try:
                lang_registry.validate_version(lang, version)
                # Success — no need to report each one
            except ValueError as e:
                check(f"  {lang}/{version}: validate_version fails", False, str(e))

    except Exception as e:
        check(f"  {lang}: consistency check failed", False, str(e))
        traceback.print_exc()

# ── Test 11: Edge cases ────────────────────────────────────────────────────────
print("\n[11] Testing edge cases...")

# Case-sensitivity
try:
    lang_registry.get("ES")  # Should fail — codes are lowercase
    check("get('ES') is case-sensitive", False, "Should reject uppercase")
except ValueError:
    check(
        "get('ES') is case-sensitive", True, "Correct — only lowercase codes accepted"
    )

# Empty string
try:
    lang_registry.get("")
    check("get('') raises ValueError", False, "Should reject empty string")
except ValueError:
    check("get('') raises ValueError", True)

# None (should raise TypeError, not ValueError)
try:
    lang_registry.get(None)  # type: ignore
    check("get(None) raises error", False, "Should reject None")
except (ValueError, TypeError, AttributeError):
    check("get(None) raises error", True)

# ── Test 12: Verify fil/tl migration is complete ──────────────────────────────
print("\n[12] Testing fil/tl migration...")
check("'fil' is in list_languages()", "fil" in lang_registry.list_languages())
check("'tl' is NOT in list_languages()", "tl" not in lang_registry.list_languages())

try:
    fil_cfg = lang_registry.get("fil")
    check("fil has MBB05", "MBB05" in fil_cfg.known_versions)
    check("fil has ASND", "ASND" in fil_cfg.known_versions)
    check(
        "fil does NOT have ADB",
        "ADB" not in fil_cfg.known_versions,
        "ADB removed per issue",
    )
except ValueError as e:
    check("fil should be registered", False, str(e))

# ── Test 13: Integration with prompts.py and source.py ────────────────────────
print("\n[13] Testing integration with other modules...")
try:
    # Test that prompts.py can use the registry
    from prompts import get_persona, get_section_labels, get_native_speaker_info

    persona_es = get_persona("es")
    check("prompts.get_persona('es') works", len(persona_es) > 50)

    labels_es = get_section_labels("es")
    check("prompts.get_section_labels('es') works", "verse" in labels_es)

    native_es = get_native_speaker_info("es")
    check("prompts.get_native_speaker_info('es') works", len(native_es) == 2)

    # Test that source.py can use the registry
    from source import build_url

    url_es = build_url("es", "RVR1960", 2025)
    check(
        "source.build_url('es', 'RVR1960', 2025) works",
        "Devocional_year_2025" in url_es,
    )

    url_fil = build_url("fil", "MBB05", 2026)
    check(
        "source.build_url('fil', 'MBB05', 2026) works",
        "Devocional_year_2026" in url_fil,
    )

except Exception as e:
    check("Integration with prompts/source failed", False, str(e))
    traceback.print_exc()

# ════════════════════════════════════════════════════════════════════════════
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"\n{'═' * 60}")
print(f"  Results: {passed} passed / {failed} failed")
if failed == 0:
    print("  🎉 All tests passed — language registry is working correctly.")
else:
    print("  ⚠️  Fix failures before deployment.")
    for icon, name, detail in results:
        if icon == FAIL:
            print(f"     → {name}" + (f": {detail}" if detail else ""))
print(f"{'═' * 60}\n")

sys.exit(0 if failed == 0 else 1)

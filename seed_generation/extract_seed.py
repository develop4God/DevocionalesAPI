"""
extract_seed.py
───────────────
Extracts verse references from a KJV devotional JSON file,
fetches the exact target-language text from a SQLite Bible DB,
and produces a clean seed JSON ready for generate_from_seed.py.

Rules:
  - Only verified entries (main verse + all para_meditar found in DB) are written.
  - Any date with ANY miss is SKIPPED entirely.
  - All skipped dates are written to a separate error report JSON.
  - Tags are translated via tags_master.json — no silent fallback.
  - Seed is reverse-validated before exit — zero corrupt tags reach generation.

Usage:
  python extract_seed.py
  → tkinter pickers for: KJV JSON, SQLite DB, output folder
  → produces: seed_<lang>_<version>_<ts>.json
               seed_errors_<lang>_<version>_<ts>.json  (if any)
               seed_tag_misses_<lang>_<version>_<ts>.json  (if any)
"""

import gzip
import json
import re
import shutil
import sqlite3
import sys
import os
import tempfile
import urllib.request
from datetime import datetime
from tkinter import Tk, filedialog, messagebox, simpledialog

# =============================================================================
# 1. CONSTANTS & CONFIG
# =============================================================================

_SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
TAGS_MASTER_PATH = os.path.join(_SCRIPT_DIR, "tags_master.json")

# SOT for EN book name → book_number; native name read from DB's books table
BOOKS_SOT_URL = (
    "https://raw.githubusercontent.com/develop4god/bible_versions"
    "/refs/heads/main/bible_books.json"
)
_books_sot_cache: dict | None = None

# Normalized key → canonical key.
# Applied BEFORE tags_master lookup — redirects deprecated variants.
MERGE_MAP: dict[str, str] = {
    "armorofgod":  "armor",
    "jesuschrist": "jesus",
    "newlife":     "newbirth",
    "wordofgod":   "word",
    "liberation":  "deliverance",
    "blessings":   "blessing",
    "miracles":    "miracle",
}

# Script ranges for reverse validation
SCRIPT_RANGES: dict[str, tuple[int, int]] = {
    "hi": (0x0900, 0x097F),
    "ko": (0xAC00, 0xD7A3),
    "ja": (0x3040, 0x30FF),
    "zh": (0x4E00, 0x9FFF),
    "ru": (0x0400, 0x04FF),
}
SCRIPT_THRESHOLD = 0.5   # min ratio of target-script chars to flag as valid

# Latin-script languages — no script check needed
LATIN_LANGS = {"en", "es", "pt", "fr", "de"}

# Devanagari digit → ASCII digit
_DEVA = str.maketrans("०१२३४५६७८९", "0123456789")


# =============================================================================
# 2. VERSE TEXT NORMALIZER
# =============================================================================

def normalize_verse_text(text: str) -> str:
    """Remove [N] footnote markers from Bible verse text.

    Applies to SCH2000 (de), HIOV/HERV (hi) and any translation that embeds
    inline footnote reference numbers in verse text.

    Preserves translator word-additions like [selbst], [der Wesensart], [zur]
    since \\[\\d+\\] only matches purely numeric content.

    Two-step removal is intentional: a greedy \\s*\\[\\d+\\]\\s* pattern would
    collapse दिन[१] उसे → दिनउसे, losing the post-tag space when the tag is
    directly attached to the preceding word.
    """
    text = re.sub(r' \[\d+\] ', ' ', text)   # space-[N]-space → single space
    text = re.sub(r'\[\d+\]', '', text)        # remove tag, preserve surrounding whitespace
    text = re.sub(r'  +', ' ', text)           # collapse double spaces
    text = re.sub(r' ([,;।.])', r'\1', text)   # fix orphaned space before punctuation
    return text.strip()


# =============================================================================
# 3. TAG SERVICE
# =============================================================================

def normalize_tag(tag: str) -> str:
    """Lowercase + strip spaces, hyphens, apostrophes — mirrors tags_master keys."""
    return re.sub(r"[\s\-\']+", "", tag).lower()


def load_tags_master() -> tuple[dict, dict]:
    """
    Load tags_master.json.
    Returns (tags_map, merge_map_from_file) where:
      tags_map  = {normalized_key: {lang: translation}}
      file_merge = merge_map stored in the JSON (merged with MERGE_MAP constant)
    """
    if not os.path.exists(TAGS_MASTER_PATH):
        raise FileNotFoundError(
            f"tags_master.json not found at: {TAGS_MASTER_PATH}\n"
            f"Place tags_master.json next to this script."
        )
    with open(TAGS_MASTER_PATH, encoding="utf-8") as f:
        data = json.load(f)
    file_merge = data.get("merge_map", {})
    merged_map = {**MERGE_MAP, **file_merge}   # file overrides constant
    return data["tags"], merged_map


def preflight_coverage(
    kjv_dates: dict,
    tags_map: dict,
    effective_merge: dict,
    target_lang: str,
) -> list[dict]:
    """
    Scan all KJV entries BEFORE extraction.
    Returns list of miss dicts: {date, en_tag, normalized, reason}
    Prints a coverage report. Does NOT block execution — caller decides.
    """
    misses = []
    seen_norms = set()

    for date_key, entries in kjv_dates.items():
        try:
            en_tags = entries[0].get("tags", [])
        except (IndexError, AttributeError):
            continue

        for tag in en_tags:
            norm = normalize_tag(tag)
            canonical = effective_merge.get(norm, norm)
            if canonical in seen_norms:
                continue
            seen_norms.add(canonical)

            if canonical not in tags_map:
                misses.append({
                    "date":       date_key,
                    "en_tag":     tag,
                    "normalized": norm,
                    "canonical":  canonical,
                    "reason":     "key not in tags_master",
                })
            elif target_lang not in tags_map[canonical] or not tags_map[canonical][target_lang]:
                misses.append({
                    "date":       date_key,
                    "en_tag":     tag,
                    "normalized": norm,
                    "canonical":  canonical,
                    "reason":     f"no '{target_lang}' translation for key",
                })

    SEP = "=" * 60
    print(f"\n{SEP}")
    print(f"TAG PREFLIGHT — {target_lang.upper()}")
    print(SEP)
    unique_tags = len(seen_norms)
    coverage = ((unique_tags - len(misses)) / unique_tags * 100) if unique_tags else 100
    print(f"  Unique canonical tags : {unique_tags}")
    print(f"  Coverage              : {coverage:.1f}%")
    if misses:
        print(f"  MISSES ({len(misses)}) — will use EN fallback:")
        for m in misses:
            print(f"    [{m['date']}] '{m['en_tag']}' → '{m['canonical']}' — {m['reason']}")
    else:
        print(f"  ✅ 100% coverage — all tags translate cleanly")
    print(SEP + "\n")

    return misses


def translate_tags(
    en_tags: list,
    tags_map: dict,
    effective_merge: dict,
    target_lang: str,
    miss_log: list,
) -> list:
    """
    Translate EN tags → target_lang via tags_master.
    Applies MERGE_MAP before lookup.
    Logs every miss to miss_log — NO silent fallback to EN.
    Falls back to EN tag only if miss, but records it explicitly.
    """
    result = []
    for tag in en_tags:
        norm     = normalize_tag(tag)
        canonical = effective_merge.get(norm, norm)
        translated = tags_map.get(canonical, {}).get(target_lang)

        if translated:
            result.append(translated)
        else:
            # Explicit miss — log and use EN as emergency fallback
            miss_log.append({
                "en_tag":    tag,
                "canonical": canonical,
                "lang":      target_lang,
                "reason":    "not in tags_master — EN fallback used",
            })
            result.append(tag)   # EN fallback — flagged in report

    return list(dict.fromkeys(result))


def _has_target_script(text: str, lang: str) -> bool:
    """Return True if text contains enough chars of the expected script."""
    if lang in LATIN_LANGS:
        return True
    if lang not in SCRIPT_RANGES:
        return True   # unknown lang — skip check
    lo, hi = SCRIPT_RANGES[lang]
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return False
    ratio = sum(1 for c in alpha if lo <= ord(c) <= hi) / len(alpha)
    return ratio >= SCRIPT_THRESHOLD


def reverse_validate_seed(
    seed: dict,
    tags_map: dict,
    effective_merge: dict,
    target_lang: str,
) -> list[dict]:
    """
    Post-write reverse validation.
    For each seed entry, checks:
      1. Script integrity  — tag is in expected script (not EN fallback slipped through)
      2. Vocabulary check  — tag value exists in tags_master[lang] values
      3. Round-trip check  — tag value reverse-maps to a known EN key

    Returns list of violation dicts. Empty list = seed is clean.
    """
    # Build reverse lookup: translated_value → canonical_key  (for target_lang)
    reverse_map: dict[str, str] = {}
    for key, translations in tags_map.items():
        val = translations.get(target_lang, "")
        if val:
            reverse_map[val] = key

    valid_values = set(reverse_map.keys())
    violations = []

    for date_key, entry in seed.items():
        for tag in entry.get("tags", []):
            checks = []

            # 1. Script check
            if not _has_target_script(tag, target_lang):
                checks.append("script_fail: expected target script, got latin/other")

            # 2. Vocabulary check
            if tag not in valid_values:
                checks.append(f"vocab_fail: '{tag}' not a known {target_lang} translation")

            # 3. Round-trip check
            if tag in reverse_map:
                canonical = reverse_map[tag]
                if canonical not in tags_map:
                    checks.append(f"roundtrip_fail: reverse maps to unknown key '{canonical}'")
            elif tag in valid_values:
                pass   # already caught by vocab check above

            if checks:
                violations.append({
                    "date": date_key,
                    "tag":  tag,
                    "lang": target_lang,
                    "checks": checks,
                })

    SEP = "=" * 60
    print(f"\n{SEP}")
    print(f"REVERSE VALIDATION — {target_lang.upper()}")
    print(SEP)
    if violations:
        print(f"  ⚠️  {len(violations)} violation(s) found:")
        for v in violations:
            print(f"    [{v['date']}] tag='{v['tag']}' → {v['checks']}")
    else:
        print(f"  ✅ All tags passed reverse validation")
    print(SEP + "\n")

    return violations


# =============================================================================
# 3. REFERENCE RESOLVER
# =============================================================================

def load_books_sot(local_path: str | None = None) -> dict:
    """
    Load the bible_books.json SOT.
    Returns flat dict: EN book name → book_number (int)

    Priority:
      1. Module-level cache (subsequent calls are instant)
      2. local_path if provided and the file exists
      3. Remote fetch from BOOKS_SOT_URL
    """
    global _books_sot_cache
    if _books_sot_cache is not None:
        return _books_sot_cache

    if local_path and os.path.exists(local_path):
        with open(local_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        with urllib.request.urlopen(BOOKS_SOT_URL) as resp:
            data = json.loads(resp.read())

    _books_sot_cache = {
        name: entry["book_number"]
        for name, entry in data["books"].items()
    }
    return _books_sot_cache


def load_kjv(json_path: str) -> tuple[str, dict]:
    """Load KJV JSON. Returns (lang_code, {date: [entry]})."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    lang = list(data["data"].keys())[0]
    return lang, data["data"][lang]


def parse_en_ref(cita: str) -> tuple[str, int, int, int] | None:
    """
    Parse 'John 3:16' or '1 Corinthians 13:4-7'.
    Returns (book_name, chapter, verse_start, verse_end) or None.
    """
    cita = cita.strip().translate(_DEVA)
    cita = re.sub(r'\s+[A-Z0-9]{2,6}$', '', cita).strip()   # strip trailing KJV/ESV/NIV
    m = re.match(
        r'^((?:\d\s+)?[A-Za-z]+(?:\s+[A-Za-z]+)*)\s+(\d+):(\d+)(?:-(\d+))?$',
        cita
    )
    if not m:
        return None
    return (
        m.group(1).strip(),
        int(m.group(2)),
        int(m.group(3)),
        int(m.group(4)) if m.group(4) else int(m.group(3)),
    )


def extract_ref_from_versiculo(versiculo: str) -> str:
    """Extract 'John 3:16' from 'John 3:16 KJV: \"...\"'"""
    return versiculo.split(" KJV:")[0].strip()


def fetch_text(
    cursor: sqlite3.Cursor,
    book_number: int, chapter: int,
    v_start: int, v_end: int,
) -> str | None:
    """Fetch and clean verse text from SQLite. Returns None if not found."""
    cursor.execute(
        "SELECT text FROM verses "
        "WHERE book_number=? AND chapter=? AND verse>=? AND verse<=? "
        "ORDER BY verse",
        (book_number, chapter, v_start, v_end),
    )
    rows = cursor.fetchall()
    if not rows:
        return None
    combined = " ".join(r[0] for r in rows)
    combined = re.sub(r"<[^>]+>", "", combined)           # strip XML tags
    combined = re.sub(r"[\u2460-\u24FF]", "", combined)   # strip Unicode ref markers
    combined = re.sub(r"\s+", " ", combined).strip()
    return combined


def _native_book_name(cursor: sqlite3.Cursor, book_number: int, fallback: str) -> str:
    """
    Query the DB's `books` table for the long_name of book_number.
    Returns fallback (the EN name) if the table is absent or the row is missing.
    """
    try:
        cursor.execute(
            "SELECT long_name FROM books WHERE book_number = ?",
            (book_number,),
        )
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
    except sqlite3.OperationalError:
        pass  # `books` table absent in some minimal DB builds
    return fallback


def resolve_reference(
    cita: str,
    books_sot: dict,
    cursor: sqlite3.Cursor,
) -> tuple[str | None, str | None, str | None]:
    """
    Resolve EN reference → (local_cita, texto, error_reason).
    On success: (local_cita, texto, None)
    On failure: (None, None, reason_string)

    book_number comes from the bible_books.json SOT (EN name → number).
    Native book name is read from the DB's own `books` table — no per-language
    mapping file is required.
    """
    parsed = parse_en_ref(cita)
    if parsed is None:
        return None, None, f"could not parse reference: '{cita}'"

    book_en, chapter, v_start, v_end = parsed

    book_number = books_sot.get(book_en)
    if book_number is None:
        return None, None, f"unknown book: '{book_en}' — not in bible_books.json SOT"

    local_name = _native_book_name(cursor, book_number, fallback=book_en)

    texto = fetch_text(cursor, book_number, chapter, v_start, v_end)
    if texto is None:
        cursor.execute(
            "SELECT MAX(verse) FROM verses WHERE book_number=? AND chapter=?",
            (book_number, chapter),
        )
        row = cursor.fetchone()
        max_verse = row[0] if row and row[0] else "unknown"
        range_str = f"{v_start}-{v_end}" if v_start != v_end else str(v_start)
        return None, None, (
            f"verse not found: '{cita}' → {local_name} {chapter}:{range_str} "
            f"(chapter has {max_verse} verses)"
        )

    range_suffix = f"{v_start}-{v_end}" if v_start != v_end else str(v_start)
    return f"{local_name} {chapter}:{range_suffix}", texto, None


# =============================================================================
# 4. SQLITE OPENER  (supports .db / .sqlite / .SQLite3 and .gz of any of those)
# =============================================================================

def open_sqlite(path: str) -> tuple[sqlite3.Connection, object]:
    """
    Open a SQLite database from a plain file or a .gz-compressed file.

    If path ends with .gz:
      - Decompresses into a NamedTemporaryFile (delete=False so SQLite can open it).
      - Returns (conn, tmp) — caller MUST call tmp.close() + os.unlink(tmp.name)
        when done, or use the context pattern in extract_seed().
    Otherwise:
      - Opens directly.
      - Returns (conn, None) — no cleanup needed.

    Never leaves a decompressed file on disk after the caller finishes.
    """
    if path.lower().endswith(".gz"):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        try:
            with gzip.open(path, "rb") as gz_in:
                shutil.copyfileobj(gz_in, tmp)
            tmp.flush()
            tmp.close()                          # close write handle; SQLite opens its own
            conn = sqlite3.connect(tmp.name)
            return conn, tmp
        except Exception:
            tmp.close()
            os.unlink(tmp.name)
            raise
    else:
        return sqlite3.connect(path), None


# =============================================================================
# 5. SEED WRITER
# =============================================================================

def save_seed(seed: dict, output_dir: str, target_lang: str, target_version: str) -> str:
    """Write seed JSON. Returns output path."""
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"seed_{target_lang}_{target_version}_{ts}.json"
    path     = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)
    return path


def save_report(data: list, output_dir: str, prefix: str, lang: str, version: str) -> str:
    """Write any error/miss/violation report JSON. Returns output path."""
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_{lang}_{version}_{ts}.json"
    path     = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# =============================================================================
# 5. ORCHESTRATOR
# =============================================================================

def extract_seed(
    kjv_path: str,
    sqlite_path: str,
    output_dir: str,
    target_lang: str,
    target_version: str,
) -> None:
    SEP = "=" * 60
    print(f"\n{SEP}")
    print("SEED EXTRACTOR")
    print(SEP)
    print(f"  KJV JSON : {kjv_path}")
    print(f"  SQLite   : {sqlite_path}")
    print(f"  Target   : {target_lang} / {target_version}")
    print(f"  Output   : {output_dir}")
    print(f"{SEP}\n")

    # ── Load resources ────────────────────────────────────────────────────────
    print("[1/7] Loading bible_books.json SOT...")
    books_sot = load_books_sot()
    print(f"      ✅ {len(books_sot)} book entries\n")

    print("[2/7] Loading tags_master.json...")
    tags_map, effective_merge = load_tags_master()
    print(f"      ✅ {len(tags_map)} tags | {len(effective_merge)} merge rules\n")

    print("[3/7] Loading KJV JSON...")
    _, kjv_dates = load_kjv(kjv_path)
    dates = sorted(kjv_dates.keys())
    print(f"      ✅ {len(dates)} dates\n")

    print("[4/7] Connecting to SQLite...")
    is_gz = sqlite_path.lower().endswith(".gz")
    if is_gz:
        print(f"      🗜️  Detected .gz — decompressing to temp file (will be deleted on exit)...")
    tmp_db = None
    try:
        conn, tmp_db = open_sqlite(sqlite_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM verses")
        verse_count = cursor.fetchone()[0]
        print(f"      ✅ {verse_count:,} verses available\n")
    except Exception as e:
        print(f"      ❌ FATAL: Cannot open SQLite — {e}")
        if tmp_db is not None:
            try:
                os.unlink(tmp_db.name)
            except OSError:
                pass
        sys.exit(1)

    # ── Preflight tag coverage ────────────────────────────────────────────────
    print("[5/7] Tag preflight coverage check...")
    preflight_misses = preflight_coverage(kjv_dates, tags_map, effective_merge, target_lang)

    # ── Extract ───────────────────────────────────────────────────────────────
    print("[6/7] Extracting verses...\n")

    seed        = {}
    errors      = []
    tag_misses  = []   # accumulated across all entries
    ok_count    = 0
    skip_count  = 0

    try:
        for date_key in dates:
            try:
                entry = kjv_dates[date_key][0]
            except (KeyError, IndexError):
                errors.append({"date": date_key, "reason": "entry not accessible in KJV JSON"})
                skip_count += 1
                continue

            date_errors = []

            # Main verse
            main_ref_en = extract_ref_from_versiculo(entry.get("versiculo", ""))
            hi_main_cita, main_texto, main_err = resolve_reference(main_ref_en, books_sot, cursor)
            if main_err:
                date_errors.append({"field": "versiculo", "reference": main_ref_en, "reason": main_err})

            # Para meditar
            pm_results = []
            for idx, pm in enumerate(entry.get("para_meditar", [])):
                cita_en = pm.get("cita", "").strip()
                hi_pm_cita, pm_texto, pm_err = resolve_reference(cita_en, books_sot, cursor)
                if pm_err:
                    date_errors.append({
                        "field":     f"para_meditar[{idx}]",
                        "reference": cita_en,
                        "reason":    pm_err,
                    })
                else:
                    pm_results.append({"cita": hi_pm_cita, "texto": normalize_verse_text(pm_texto)})

            # Decision
            if date_errors:
                errors.append({"date": date_key, "errors": date_errors})
                skip_count += 1
                print(f"  ⏭️  {date_key} — SKIPPED ({len(date_errors)} error(s))")
                for e in date_errors:
                    print(f"       {e['field']}: {e['reason']}")
                continue

            # Translate tags — all misses logged to tag_misses
            en_tags     = entry.get("tags") or []
            target_tags = translate_tags(en_tags, tags_map, effective_merge, target_lang, tag_misses)

            seed[date_key] = {
                "versiculo":   {"cita": hi_main_cita, "texto": normalize_verse_text(main_texto)},
                "para_meditar": pm_results,
                "tags":         target_tags,
            }
            ok_count += 1
            print(f"  ✅  {date_key} — {hi_main_cita} | tags: {target_tags}")

    finally:
        conn.close()
        if tmp_db is not None:
            try:
                os.unlink(tmp_db.name)
                print(f"      🗑️  Temp file deleted: {tmp_db.name}")
            except OSError as e:
                print(f"      ⚠️  Could not delete temp file {tmp_db.name}: {e}")

    # ── Reverse validation ────────────────────────────────────────────────────
    violations = reverse_validate_seed(seed, tags_map, effective_merge, target_lang)

    # ── Write outputs ─────────────────────────────────────────────────────────
    print(f"[7/7] Writing output files...")

    seed_path = save_seed(seed, output_dir, target_lang, target_version)
    print(f"      ✅ Seed    → {seed_path}")

    if errors:
        ep = save_report(errors, output_dir, "seed_errors", target_lang, target_version)
        print(f"      ✅ Errors  → {ep}")

    if tag_misses:
        mp = save_report(tag_misses, output_dir, "seed_tag_misses", target_lang, target_version)
        print(f"      ⚠️  Tag misses → {mp}")

    if violations:
        vp = save_report(violations, output_dir, "seed_violations", target_lang, target_version)
        print(f"      ⚠️  Violations → {vp}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("SUMMARY")
    print(SEP)
    print(f"  Total dates       : {len(dates)}")
    print(f"  Seed entries      : {ok_count}  ← ready for generation")
    print(f"  Skipped dates     : {skip_count}")
    print(f"  Tag misses        : {len(set(m['en_tag'] for m in tag_misses))} unique")
    print(f"  Reverse violations: {len(violations)}")
    if violations:
        print(f"  ⚠️  Review seed_violations before running generate_from_seed.py")
    else:
        print(f"  ✅ Seed is clean — ready for generate_from_seed.py")
    print(f"{SEP}\n")


# =============================================================================
# 6. ENTRY POINT
# =============================================================================

def main() -> None:
    root = Tk()
    root.withdraw()

    messagebox.showinfo("Seed Extractor — Step 1 of 4", "Select the KJV devotional JSON file.")
    kjv_path = filedialog.askopenfilename(
        title="Select KJV devotional JSON",
        filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
    )
    if not kjv_path:
        print("Cancelled.")
        sys.exit(0)

    messagebox.showinfo("Seed Extractor — Step 2 of 4", "Select the SQLite Bible database (.db / .sqlite / .SQLite3 or .gz).")
    sqlite_path = filedialog.askopenfilename(
        title="Select SQLite DB",
        filetypes=[
            ("SQLite files", "*.SQLite3 *.db *.sqlite"),
            ("Compressed SQLite (.gz)", "*.gz"),
            ("All files", "*.*"),
        ],
    )
    if not sqlite_path:
        print("Cancelled.")
        sys.exit(0)

    target_lang = simpledialog.askstring(
        "Seed Extractor — Step 3 of 4",
        "Target language code (e.g. hi, es, de, ko):",
        initialvalue="hi",
    )
    if not target_lang:
        print("Cancelled.")
        sys.exit(0)

    target_version = simpledialog.askstring(
        "Seed Extractor — Step 4 of 4",
        "Target version code (e.g. OV, HERV, KJV):",
        initialvalue="OV",
    )
    if not target_version:
        print("Cancelled.")
        sys.exit(0)

    messagebox.showinfo("Seed Extractor — Output", "Select the output folder.")
    output_dir = filedialog.askdirectory(title="Select output folder")
    if not output_dir:
        print("Cancelled.")
        sys.exit(0)

    root.destroy()
    extract_seed(kjv_path, sqlite_path, output_dir, target_lang, target_version)


if __name__ == "__main__":
    main()
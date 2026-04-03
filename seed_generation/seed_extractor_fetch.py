"""
seed_extractor_fetch.py  —  SOT Edition
────────────────────────────────────────────────────────────────────────────────
Improved seed extractor.  All source data is fetched from authoritative remote
sources — no manual file pickers for KJV JSON or Bible DBs.

Sources (in priority order):
  • KJV devotional JSON   — local BASE file if present, else GitHub remote
  • bible_books.json SOT  — github.com/develop4God/bible_versions  (cached in memory)
  • Bible versions index  — github.com/develop4God/bible_versions  (cached in memory)
  • Target SQLite DB      — Bibles/ folder if already present, else downloaded
                           from the remote index URL and decompressed in place
  • tags_master.json      — local file next to this script (required)

Interaction:
  Terminal prompts → select year  →  select language  →  select version
  Single tkinter dialog           →  choose output folder

Output files (in chosen folder):
  seed_<lang>_<version>_<year>_<ts>.json
  seed_errors_<...>.json      (skipped dates, if any)
  seed_tag_misses_<...>.json  (untranslated tags, if any)
  seed_violations_<...>.json  (reverse-validation failures, if any)
"""

import argparse
import gzip
import json
import os
import re
import shutil
import sqlite3
import sys
import urllib.request
from datetime import datetime
try:
    from tkinter import Tk, filedialog
    _HAS_TKINTER = True
except ImportError:
    _HAS_TKINTER = False

# ─────────────────────────────────────────────────────────────────────────────
# 1.  CONSTANTS & PATHS
# ─────────────────────────────────────────────────────────────────────────────

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

BOOKS_SOT_URL      = (
    "https://raw.githubusercontent.com/develop4god/bible_versions"
    "/refs/heads/main/bible_books.json"
)
VERSIONS_INDEX_URL = (
    "https://raw.githubusercontent.com/develop4God/bible_versions"
    "/refs/heads/main/index.json"
)
KJV_URLS = {
    2025: (
        "https://raw.githubusercontent.com/develop4God/devocionales-json"
        "/refs/heads/main/Devocional_year_2025_en_KJV.json"
    ),
    2026: (
        "https://raw.githubusercontent.com/develop4God/devocionales-json"
        "/refs/heads/main/Devocional_year_2026_en_KJV.json"
    ),
}

# Local BASE files — checked before fetching from remote
_LOCAL_KJV = {
    2025: os.path.join(_SCRIPT_DIR, "BASE1-Devocional_year_2025_en_KJV.json"),
    2026: os.path.join(_SCRIPT_DIR, "BASE2-Devocional_year_2026_en_KJV.json"),
}

DB_DIR           = os.path.join(_SCRIPT_DIR, "Bibles")
TAGS_MASTER_PATH = os.path.join(_SCRIPT_DIR, "tags_master.json")

# Normalized key → canonical key (applied before tags_master lookup)
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
SCRIPT_THRESHOLD = 0.5
LATIN_LANGS      = {"en", "es", "pt", "fr", "de"}

# Devanagari digit → ASCII digit
_DEVA = str.maketrans("०१२३४५६७८९", "0123456789")

SEP = "=" * 62

# Module-level caches — fetched once per run
_books_sot_cache:      dict | None = None
_versions_index_cache: dict | None = None


# ─────────────────────────────────────────────────────────────────────────────
# 2.  VERSE TEXT NORMALIZER
# ─────────────────────────────────────────────────────────────────────────────

def normalize_verse_text(text: str) -> str:
    """Strip inline footnote markers like [1], [२] from verse text."""
    text = re.sub(r' \[\d+\] ', ' ', text)
    text = re.sub(r'\[\d+\]',   '',  text)
    text = re.sub(r'  +',       ' ', text)
    text = re.sub(r' ([,;।.])', r'\1', text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# 3.  REMOTE FETCHERS
# ─────────────────────────────────────────────────────────────────────────────

def load_books_sot() -> dict:
    """
    Return flat dict: EN book name → book_number.
    Fetched once from BOOKS_SOT_URL and cached for the lifetime of the process.
    """
    global _books_sot_cache
    if _books_sot_cache is not None:
        return _books_sot_cache
    print(f"  Fetching bible_books.json SOT...")
    with urllib.request.urlopen(BOOKS_SOT_URL) as resp:
        data = json.loads(resp.read())
    _books_sot_cache = {
        name: entry["book_number"]
        for name, entry in data["books"].items()
    }
    print(f"      ✅ {len(_books_sot_cache)} books\n")
    return _books_sot_cache


def fetch_versions_index() -> dict:
    """Return the full bible versions index. Fetched once and cached."""
    global _versions_index_cache
    if _versions_index_cache is not None:
        return _versions_index_cache
    print("  Fetching bible versions index...")
    with urllib.request.urlopen(VERSIONS_INDEX_URL) as resp:
        _versions_index_cache = json.loads(resp.read())
    langs     = _versions_index_cache["languages"]
    n_langs   = len(langs)
    n_versions = sum(len(v["versions"]) for v in langs.values())
    print(f"      ✅ {n_langs} languages, {n_versions} versions\n")
    return _versions_index_cache


def fetch_kjv(year: int) -> tuple[str, dict]:
    """
    Load the KJV devotional JSON for *year*.
    Checks the local BASE file first; falls back to the remote GitHub URL.
    Returns (lang_code, {date: [entry]}).
    """
    local = _LOCAL_KJV.get(year, "")
    if local and os.path.exists(local):
        print(f"  Loading KJV {year} from local file...")
        src = "local"
        with open(local, encoding="utf-8") as f:
            data = json.load(f)
    else:
        url = KJV_URLS[year]
        print(f"  Fetching KJV {year} from GitHub...")
        src = "remote"
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())

    lang  = list(data["data"].keys())[0]
    dates = data["data"][lang]
    print(f"      ✅ {len(dates)} dates  ({src})\n")
    return lang, dates


# ─────────────────────────────────────────────────────────────────────────────
# 4.  DB MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def _make_progress_hook():
    """Return a urllib reporthook that prints a simple progress bar."""
    def hook(block_count: int, block_size: int, total_size: int) -> None:
        downloaded = block_count * block_size
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(
                f"\r      [{bar}] {pct:3d}%  "
                f"({downloaded / 1_048_576:.1f} / {total_size / 1_048_576:.1f} MB)",
                end="", flush=True,
            )
        else:
            print(f"\r      {downloaded / 1_048_576:.1f} MB downloaded", end="", flush=True)
    return hook


def find_or_download_db(version_entry: dict, lang_code: str) -> str:
    """
    Ensure the SQLite DB for *version_entry* is present locally.

    Search order:
      1. Bibles/<VERSION>_<LANG>.SQLite3         (flat cache — preferred)
      2. Bibles/<LANG_UPPER>/<VERSION>_<LANG>.SQLite3  (legacy subfolder)

    If not found, download the .gz from the remote URL, decompress to (1),
    then delete the .gz.

    Returns the path to the ready-to-use .SQLite3 file.
    """
    remote_file = version_entry["file"]              # e.g. "LU17_de.SQLite3.gz"
    db_filename = remote_file.replace(".gz", "")     # e.g. "LU17_de.SQLite3"

    flat_path = os.path.join(DB_DIR, db_filename)
    sub_path  = os.path.join(DB_DIR, lang_code.upper(), db_filename)

    if os.path.exists(flat_path):
        print(f"      ✅ Using cached  {flat_path}")
        return flat_path
    if os.path.exists(sub_path):
        print(f"      ✅ Using cached  {sub_path}")
        return sub_path

    # ── Download ──────────────────────────────────────────────────────────────
    os.makedirs(DB_DIR, exist_ok=True)
    gz_path = os.path.join(DB_DIR, remote_file)

    if not os.path.exists(gz_path):
        print(f"      ⬇  Downloading {remote_file}...")
        urllib.request.urlretrieve(version_entry["url"], gz_path, _make_progress_hook())
        print()   # newline after progress bar

    # ── Decompress ────────────────────────────────────────────────────────────
    print(f"      🗜  Decompressing {remote_file}...")
    with gzip.open(gz_path, "rb") as gz_in, open(flat_path, "wb") as db_out:
        shutil.copyfileobj(gz_in, db_out)
    os.unlink(gz_path)
    print(f"      ✅ {db_filename}\n")
    return flat_path


# ─────────────────────────────────────────────────────────────────────────────
# 5.  REFERENCE RESOLVER
# ─────────────────────────────────────────────────────────────────────────────

def _native_book_name(cursor: sqlite3.Cursor, book_number: int, fallback: str) -> str:
    """
    Query the DB's `books` table for the native long_name of *book_number*.
    Returns *fallback* (the EN name) if the table is absent or the row is missing.
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
        pass   # `books` table absent in minimal DB builds
    return fallback


def parse_en_ref(cita: str) -> tuple[str, int, int, int] | None:
    """
    Parse an English Bible reference string.
    Accepts: "John 3:16", "1 Corinthians 13:4-7", trailing version codes, Devanagari digits.
    Returns (book_name, chapter, verse_start, verse_end) or None.
    """
    cita = cita.strip().translate(_DEVA)
    cita = re.sub(r'\s+[A-Z0-9]{2,6}$', '', cita).strip()
    m = re.match(
        r'^((?:\d\s+)?[A-Za-z]+(?:\s+[A-Za-z]+)*)\s+(\d+):(\d+)(?:-(\d+))?$',
        cita,
    )
    if not m:
        return None
    return (
        m.group(1).strip(),
        int(m.group(2)),
        int(m.group(3)),
        int(m.group(4)) if m.group(4) else int(m.group(3)),
    )


def fetch_text(
    cursor: sqlite3.Cursor,
    book_number: int,
    chapter: int,
    v_start: int,
    v_end: int,
) -> str | None:
    """Fetch and clean verse text from a SQLite Bible DB. Returns None if not found."""
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
    combined = re.sub(r"<[^>]+>",        "",  combined)
    combined = re.sub(r"[\u2460-\u24FF]", "",  combined)
    combined = re.sub(r"\s+",            " ", combined)
    return combined.strip()


def resolve_reference(
    cita: str,
    books_sot: dict,
    cursor: sqlite3.Cursor,
) -> tuple[str | None, str | None, str | None]:
    """
    Resolve an EN reference string to (native_citation, verse_text, error).
    book_number comes from bible_books.json SOT; native book name comes from the DB.
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
        row       = cursor.fetchone()
        max_verse = row[0] if row and row[0] else "unknown"
        range_str = f"{v_start}-{v_end}" if v_start != v_end else str(v_start)
        return None, None, (
            f"verse not found: '{cita}' → {local_name} {chapter}:{range_str} "
            f"(chapter has {max_verse} verses)"
        )

    range_suffix = f"{v_start}-{v_end}" if v_start != v_end else str(v_start)
    return f"{local_name} {chapter}:{range_suffix}", texto, None


def extract_ref_from_versiculo(versiculo: str) -> str:
    """Extract the reference portion from 'Romans 9:15-16 KJV: \"...\"'"""
    return versiculo.split(" KJV:")[0].strip()


# ─────────────────────────────────────────────────────────────────────────────
# 6.  TAG SERVICE
# ─────────────────────────────────────────────────────────────────────────────

def normalize_tag(tag: str) -> str:
    return re.sub(r"[\s\-\']+", "", tag).lower()


def load_tags_master() -> tuple[dict, dict]:
    """Load tags_master.json. Returns (tags_map, effective_merge_map)."""
    if not os.path.exists(TAGS_MASTER_PATH):
        raise FileNotFoundError(
            f"tags_master.json not found at: {TAGS_MASTER_PATH}\n"
            "Place tags_master.json next to this script."
        )
    with open(TAGS_MASTER_PATH, encoding="utf-8") as f:
        data = json.load(f)
    file_merge  = data.get("merge_map", {})
    merged_map  = {**MERGE_MAP, **file_merge}
    return data["tags"], merged_map


def preflight_coverage(
    kjv_dates: dict,
    tags_map: dict,
    effective_merge: dict,
    target_lang: str,
) -> list[dict]:
    """Scan all KJV entries before extraction; report tag coverage. Does not block."""
    misses    = []
    seen_norms: set[str] = set()

    for date_key, entries in kjv_dates.items():
        try:
            en_tags = entries[0].get("tags", [])
        except (IndexError, AttributeError):
            continue

        for tag in en_tags:
            norm      = normalize_tag(tag)
            canonical = effective_merge.get(norm, norm)
            if canonical in seen_norms:
                continue
            seen_norms.add(canonical)

            if canonical not in tags_map:
                misses.append({
                    "date": date_key, "en_tag": tag,
                    "normalized": norm, "canonical": canonical,
                    "reason": "key not in tags_master",
                })
            elif target_lang not in tags_map[canonical] or not tags_map[canonical][target_lang]:
                misses.append({
                    "date": date_key, "en_tag": tag,
                    "normalized": norm, "canonical": canonical,
                    "reason": f"no '{target_lang}' translation for key",
                })

    print(f"\n{SEP}")
    print(f"TAG PREFLIGHT — {target_lang.upper()}")
    print(SEP)
    unique = len(seen_norms)
    pct    = ((unique - len(misses)) / unique * 100) if unique else 100.0
    print(f"  Unique canonical tags : {unique}")
    print(f"  Coverage              : {pct:.1f}%")
    if misses:
        print(f"  MISSES ({len(misses)}) — EN tag used as fallback:")
        for m in misses:
            print(f"    [{m['date']}] '{m['en_tag']}' → '{m['canonical']}' — {m['reason']}")
    else:
        print("  ✅ 100% coverage — all tags translate cleanly")
    print(SEP + "\n")
    return misses


def translate_tags(
    en_tags: list,
    tags_map: dict,
    effective_merge: dict,
    target_lang: str,
    miss_log: list,
) -> list:
    """Translate EN tags → target_lang via tags_master. Every miss is logged."""
    result = []
    for tag in en_tags:
        norm      = normalize_tag(tag)
        canonical = effective_merge.get(norm, norm)
        translated = tags_map.get(canonical, {}).get(target_lang)
        if translated:
            result.append(translated)
        else:
            miss_log.append({
                "en_tag": tag, "canonical": canonical,
                "lang":   target_lang,
                "reason": "not in tags_master — EN fallback used",
            })
            result.append(tag)
    return list(dict.fromkeys(result))


def _has_target_script(text: str, lang: str) -> bool:
    if lang in LATIN_LANGS:
        return True
    if lang not in SCRIPT_RANGES:
        return True
    lo, hi = SCRIPT_RANGES[lang]
    alpha  = [c for c in text if c.isalpha()]
    if not alpha:
        return False
    ratio  = sum(1 for c in alpha if lo <= ord(c) <= hi) / len(alpha)
    return ratio >= SCRIPT_THRESHOLD


def reverse_validate_seed(
    seed: dict,
    tags_map: dict,
    effective_merge: dict,
    target_lang: str,
) -> list[dict]:
    """Post-write reverse validation: script, vocabulary, and round-trip checks."""
    reverse_map: dict[str, str] = {}
    for key, translations in tags_map.items():
        val = translations.get(target_lang, "")
        if val:
            reverse_map[val] = key

    valid_values = set(reverse_map)
    violations   = []

    for date_key, entry in seed.items():
        for tag in entry.get("tags", []):
            checks: list[str] = []

            if not _has_target_script(tag, target_lang):
                checks.append("script_fail: expected target script, got latin/other")
            if tag not in valid_values:
                checks.append(f"vocab_fail: '{tag}' not a known {target_lang} translation")
            if tag in reverse_map and reverse_map[tag] not in tags_map:
                checks.append(f"roundtrip_fail: reverse maps to unknown key '{reverse_map[tag]}'")

            if checks:
                violations.append({"date": date_key, "tag": tag, "lang": target_lang, "checks": checks})

    print(f"\n{SEP}")
    print(f"REVERSE VALIDATION — {target_lang.upper()}")
    print(SEP)
    if violations:
        print(f"  ⚠️  {len(violations)} violation(s):")
        for v in violations:
            print(f"    [{v['date']}] tag='{v['tag']}' → {v['checks']}")
    else:
        print("  ✅ All tags passed reverse validation")
    print(SEP + "\n")
    return violations


# ─────────────────────────────────────────────────────────────────────────────
# 7.  OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_seed(seed: dict, output_dir: str, lang: str, version: str, year: int) -> str:
    path = os.path.join(output_dir, f"seed_{lang}_{version}_{year}_{_ts()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)
    return path


def save_report(
    data: list, output_dir: str, prefix: str, lang: str, version: str, year: int,
) -> str:
    path = os.path.join(output_dir, f"{prefix}_{lang}_{version}_{year}_{_ts()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# 8.  INTERACTIVE UI
# ─────────────────────────────────────────────────────────────────────────────

def select_year() -> int:
    """Terminal prompt — returns 2025 or 2026."""
    print("Select year:")
    print("  1.  2025")
    print("  2.  2026")
    while True:
        choice = input("  > ").strip()
        if choice == "1":
            return 2025
        if choice == "2":
            return 2026
        print("  Please enter 1 or 2.")


def select_language_version(
    index: dict,
) -> tuple[str, str, dict]:
    """
    Interactive terminal menus to choose language then version.
    Returns (lang_code, version_code, version_entry).
    """
    languages = list(index["languages"].items())

    print("Select target language:")
    for i, (code, entry) in enumerate(languages, 1):
        primary  = entry["primary_version"]
        fallback = entry["fallback_version"]
        print(f"  {i:2d}.  {code:<4}  {entry['name']:<16}  "
              f"primary={primary}  fallback={fallback}")
    while True:
        try:
            n = int(input("  > ").strip())
            if 1 <= n <= len(languages):
                lang_code, lang_entry = languages[n - 1]
                break
        except ValueError:
            pass
        print(f"  Enter a number between 1 and {len(languages)}.")

    print(f"\nSelect version for language '{lang_code}':")
    versions = list(lang_entry["versions"].items())
    primary  = lang_entry["primary_version"]
    fallback = lang_entry["fallback_version"]
    for i, (code, ventry) in enumerate(versions, 1):
        label = " ← primary" if code == primary else (" ← fallback" if code == fallback else "")
        print(f"  {i}.  {code:<12}  {ventry['name']}{label}")
    while True:
        try:
            n = int(input("  > ").strip())
            if 1 <= n <= len(versions):
                version_code, version_entry = versions[n - 1]
                return lang_code, version_code, version_entry
        except ValueError:
            pass
        print(f"  Enter a number between 1 and {len(versions)}.")


def pick_output_folder() -> str:
    """Minimal tkinter dialog to choose an output folder."""
    if not _HAS_TKINTER:
        print("ERROR: tkinter is not available. Use --output <folder> to specify output folder.")
        sys.exit(1)
    root = Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title="Select output folder for seed files")
    root.destroy()
    if not folder:
        print("No folder selected — cancelled.")
        sys.exit(0)
    return folder


# ─────────────────────────────────────────────────────────────────────────────
# 9.  ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def run(
    year:          int,
    lang_code:     str,
    version_code:  str,
    version_entry: dict,
    output_dir:    str,
) -> None:
    display_name = version_entry["name"]

    print(f"\n{SEP}")
    print(f"  SEED EXTRACTOR FETCH  —  SOT Edition")
    print(SEP)
    print(f"  Year        : {year}")
    print(f"  Target      : {lang_code}  /  {version_code}  ({display_name})")
    print(f"  Output      : {output_dir}")
    print(f"{SEP}\n")

    # ── Step 1: book numbers SOT ─────────────────────────────────────────────
    print("[1/7] Bible books SOT...")
    books_sot = load_books_sot()

    # ── Step 2: tags_master ──────────────────────────────────────────────────
    print("[2/7] Loading tags_master.json...")
    tags_map, effective_merge = load_tags_master()
    print(f"      ✅ {len(tags_map)} tags | {len(effective_merge)} merge rules\n")

    # ── Step 3: KJV devotional JSON ──────────────────────────────────────────
    print("[3/7] KJV devotional JSON...")
    _, kjv_dates = fetch_kjv(year)
    dates = sorted(kjv_dates.keys())

    # ── Step 4: SQLite DB ────────────────────────────────────────────────────
    print("[4/7] Bible SQLite DB...")
    local_db = find_or_download_db(version_entry, lang_code)

    conn   = sqlite3.connect(local_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM verses")
    verse_count = cursor.fetchone()[0]
    print(f"      ✅ {verse_count:,} verses loaded\n")

    # ── Step 5: Preflight tag coverage ──────────────────────────────────────
    print("[5/7] Tag preflight coverage check...")
    _preflight_misses = preflight_coverage(kjv_dates, tags_map, effective_merge, lang_code)

    # ── Step 6: Extraction ───────────────────────────────────────────────────
    print("[6/7] Extracting verses...\n")

    seed       = {}
    errors     = []
    tag_misses: list[dict] = []
    ok_count   = 0
    skip_count = 0

    try:
        for date_key in dates:
            try:
                entry = kjv_dates[date_key][0]
            except (KeyError, IndexError):
                errors.append({"date": date_key, "reason": "entry not accessible in KJV JSON"})
                skip_count += 1
                continue

            date_errors: list[dict] = []

            # Main verse
            main_ref_en = extract_ref_from_versiculo(entry.get("versiculo", ""))
            main_cita, main_texto, main_err = resolve_reference(main_ref_en, books_sot, cursor)
            if main_err:
                date_errors.append({"field": "versiculo", "reference": main_ref_en, "reason": main_err})

            # Para meditar verses
            pm_results: list[dict] = []
            for idx, pm in enumerate(entry.get("para_meditar", [])):
                cita_en = pm.get("cita", "").strip()
                pm_cita, pm_texto, pm_err = resolve_reference(cita_en, books_sot, cursor)
                if pm_err:
                    date_errors.append({
                        "field":     f"para_meditar[{idx}]",
                        "reference": cita_en,
                        "reason":    pm_err,
                    })
                else:
                    pm_results.append({
                        "cita":  pm_cita,
                        "texto": normalize_verse_text(pm_texto),
                    })

            if date_errors:
                errors.append({"date": date_key, "errors": date_errors})
                skip_count += 1
                print(f"  ⏭️  {date_key} — SKIPPED ({len(date_errors)} error(s))")
                for e in date_errors:
                    print(f"       {e['field']}: {e['reason']}")
                continue

            # Translate tags
            en_tags     = entry.get("tags") or []
            target_tags = translate_tags(en_tags, tags_map, effective_merge, lang_code, tag_misses)

            seed[date_key] = {
                "versiculo":    {"cita": main_cita, "texto": normalize_verse_text(main_texto)},
                "para_meditar": pm_results,
                "tags":         target_tags,
            }
            ok_count += 1
            print(f"  ✅  {date_key}  {main_cita}  |  tags: {target_tags}")

    finally:
        conn.close()

    # ── Step 7: Reverse validation & write outputs ───────────────────────────
    violations = reverse_validate_seed(seed, tags_map, effective_merge, lang_code)

    print("[7/7] Writing output files...")
    seed_path = save_seed(seed, output_dir, lang_code, version_code, year)
    print(f"      ✅ Seed        → {seed_path}")

    if errors:
        ep = save_report(errors, output_dir, "seed_errors", lang_code, version_code, year)
        print(f"      ✅ Errors      → {ep}")

    if tag_misses:
        mp = save_report(tag_misses, output_dir, "seed_tag_misses", lang_code, version_code, year)
        print(f"      ⚠️  Tag misses  → {mp}")

    if violations:
        vp = save_report(violations, output_dir, "seed_violations", lang_code, version_code, year)
        print(f"      ⚠️  Violations  → {vp}")

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  SUMMARY")
    print(SEP)
    print(f"  Year              : {year}")
    print(f"  Language/Version  : {lang_code} / {version_code}  ({display_name})")
    print(f"  Total dates       : {len(dates)}")
    print(f"  Seed entries      : {ok_count:<6} ← ready for generate_from_seed.py")
    print(f"  Skipped dates     : {skip_count}")
    print(f"  Tag misses        : {len({m['en_tag'] for m in tag_misses})} unique")
    print(f"  Reverse violations: {len(violations)}")
    if violations:
        print("  ⚠️  Review seed_violations file before running generate_from_seed.py")
    else:
        print("  ✅ Seed is clean — ready for generate_from_seed.py")
    print(SEP + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# 10.  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def _parse_cli() -> argparse.Namespace | None:
    """
    Parse CLI arguments when all four required flags are present.
    Returns a Namespace if CLI mode is active, or None for interactive mode.

    CLI usage example:
      python seed_extractor_fetch.py --year 2025 --lang ar --version ALAB --output ./seeds/2025
    """
    parser = argparse.ArgumentParser(
        description="Seed Extractor Fetch — SOT Edition",
        add_help=True,
    )
    parser.add_argument("--year",    type=int, help="Target year (2025 or 2026)")
    parser.add_argument("--lang",    type=str, help="Target language code  (e.g. ar, de, hi)")
    parser.add_argument("--version", type=str, help="Bible version code    (e.g. ALAB, LU17, HERV)")
    parser.add_argument("--output",  type=str, help="Output folder path")

    args, _ = parser.parse_known_args()
    if args.year and args.lang and args.version and args.output:
        return args
    return None   # fall through to interactive mode


def main() -> None:
    print(f"\n{SEP}")
    print("  SEED EXTRACTOR FETCH  —  SOT Edition")
    print("  All source data resolved from authoritative remote sources.")
    print(SEP + "\n")

    # ── Pre-fetch shared remote resources ────────────────────────────────────
    books_sot = load_books_sot()       # cached — reused in run()
    index     = fetch_versions_index() # cached — reused in run()

    _ = books_sot  # already fetched; run() will call load_books_sot() → cache hit

    # ── CLI or interactive? ───────────────────────────────────────────────────
    cli = _parse_cli()
    if cli:
        year = cli.year
        lang_code = cli.lang.lower()
        version_code = cli.version.upper()
        output_dir = cli.output

        # Resolve version_entry from the remote index
        lang_entry = index["languages"].get(lang_code)
        if lang_entry is None:
            print(f"ERROR: language '{lang_code}' not found in versions index.")
            sys.exit(1)
        version_entry = lang_entry["versions"].get(version_code)
        if version_entry is None:
            available = list(lang_entry["versions"].keys())
            print(f"ERROR: version '{version_code}' not found for language '{lang_code}'.")
            print(f"       Available versions: {available}")
            sys.exit(1)
    else:
        # ── Interactive selections ────────────────────────────────────────────
        year = select_year()
        print()

        lang_code, version_code, version_entry = select_language_version(index)
        print()

        output_dir = pick_output_folder()

    os.makedirs(output_dir, exist_ok=True)

    # ── Run ───────────────────────────────────────────────────────────────────
    run(year, lang_code, version_code, version_entry, output_dir)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
validate_devocional_gui.py — v5
- Non-Latin langs (hi, ja, zh): flag Latin chars, group allowed labels
- Latin langs (en, pt, fr): detect Spanish leaks
- Content quality checks (Phase 1): truncation, min length, prayer ending,
  double Amen, consecutive duplicate words (len > 3)

Usage:
  GUI mode  : python validate_devocional_gui.py
  CLI mode  : python validate_devocional_gui.py --file path.json --lang de --version LU17
"""

import json, re, sys, argparse, tkinter as tk
from tkinter import filedialog, ttk, scrolledtext
from pathlib import Path
from datetime import date, timedelta

REQUIRED_FIELDS = ["id", "date", "language", "version", "versiculo",
                   "reflexion", "para_meditar", "oracion", "tags"]
PARA_MEDITAR_FIELDS = ["cita", "texto"]
EXPECTED_MIN_ENTRIES = 365
FILENAME_PATTERN = re.compile(
    r"Devocional_year_(?P<year>\d{4})_(?P<lang>[a-z]{2})_(?P<version>.+)\.json$"
)

NON_LATIN_LANGS = {"ar", "hi", "ja", "zh"}
ALWAYS_ALLOWED  = {"HIOV", "OV", "HERV", "ERV", "KJV", "NIV", "NVI",
                   "RVR1960", "ARC", "TOB", "LSG1910"}
LATIN_RE = re.compile(r"[a-zA-Z]+")

# Spanish leak words to detect in non-Spanish Latin-script files
# Removed: 'Jesus' (valid EN/PT/FR), 'amen' (universal liturgical),
#          'Salvador'/'salvador' (valid PT: savior),
#          'santo'/'santa' (valid PT: Espírito Santo, etc.)
SPANISH_LEAKS = {
    "Amén", "Jesús", "Señor", "señor", "también",
    "Espíritu", "espíritu", "oración", "Padre",
    "padre", "gracia", "gloria", "alabanza", "misericordia",
    "bendición", "bendicion", "nombre",
    "corazón", "corazon", "Dios",
}


# ── Content quality constants (Phase 1) ──────────────────────────────────────
REFLEXION_MIN_CHARS  = 800
ORACION_MIN_CHARS    = 150
# CJK languages use denser characters; 250 CJK chars ≈ 900+ Latin chars of content
CJK_REFLEXION_MIN    = 250
CJK_ORACION_MIN      = 80
SENTENCE_ENDINGS     = ('.', '!', '?', '»', '"', "'", '\u2018', '\u2019', '\u201c', '\u201d', '।', '。', '！', '？')
# Words whose consecutive repetition is grammatically valid (not a copy error):
#   - liturgical: intentional repetition (heilig heilig, holy holy)
#   - reflexive pronouns: 'nous nous' in FR is a standard reflexive-verb construction
CONSECUTIVE_DUP_SKIP = frozenset({
    'heilig', 'holy', 'kadosh', 'halleluja', 'hosanna', 'amen', 'amén', 'āmen',
    'nous', 'vous',  # French reflexive pronouns (nous nous aimons = we love one another)
    'पवित्र',        # Hindi 'holy' — intentional liturgical repetition (cf. Rev 4:8)
    'saul',          # Biblical quote: "Saul, Saul, why do you persecute me?" (Acts 9:4)
    'banal',         # Tagalog 'holy' — Trisagion "Banal, banal, banal ang Panginoon" (Rev 4:8)
})
# Sentence-ending punctuation: repetition across a sentence boundary is rhetorical, not an error
SENT_END_PUNCT = frozenset({'.', '!', '?', ':', '»', '\u201d'})
AMEN_VARIANTS = frozenset({'amen', 'amén', 'āmen', 'amem'})  # amem covers PT 'amém'
CJK_AMEN_VARIANTS = frozenset({'阿们', '阿门', '阿們', '阿門', 'アーメン', 'ア-メン'})
HINDI_AMEN_VARIANTS = frozenset({'आमीन', 'आमेन', 'आमीन', 'आमेन'})

# Load all Amen variants from prayer_endings.json (covers ar, ru, ko, uk, etc.)
_PRAYER_ENDINGS_FILE = Path(__file__).with_name('prayer_endings.json')
UNICODE_AMEN_VARIANTS: frozenset = frozenset()
try:
    with open(_PRAYER_ENDINGS_FILE, encoding='utf-8') as _f:
        _pe = json.load(_f)
    UNICODE_AMEN_VARIANTS = frozenset(
        v.lower() for variants in _pe.values()
        if isinstance(variants, list)
        for v in variants
    )
except FileNotFoundError:
    print(
        f"WARNING: {_PRAYER_ENDINGS_FILE.name} not found — "
        "Arabic/Korean/Russian Amen validation will be disabled.",
        file=sys.stderr,
    )
except Exception as _ex:
    print(f"WARNING: Could not load {_PRAYER_ENDINGS_FILE.name}: {_ex}", file=sys.stderr)


def _find_consecutive_dup(text: str):
    """Returns first consecutive duplicate word (len > 3, not in skip list), or None.

    Skips repetition at sentence boundaries (rhetorical anaphora) and
    known grammatical constructions (e.g. French reflexive 'nous nous').
    """
    words = text.split()
    for i in range(len(words) - 1):
        raw1 = words[i]
        # Skip sentence-boundary repetition (e.g. 'love. Love,' or 'grace: Grace')
        if raw1 and raw1[-1] in SENT_END_PUNCT:
            continue
        w1 = raw1.strip('.,;:!?').lower()
        w2 = words[i + 1].strip('.,;:!?').lower()
        if w1 == w2 and len(w1) > 3 and w1 not in CONSECUTIVE_DUP_SKIP:
            return f'"{raw1} {words[i+1]}"'
    return None


def _check_prayer_ending(oracion: str) -> bool:
    import unicodedata
    text = oracion.strip()
    # CJK scripts: check suffix directly (no word boundaries)
    stripped_cjk = text.rstrip('。！？.!,;')
    for cjk in CJK_AMEN_VARIANTS:
        if stripped_cjk.endswith(cjk):
            return True
    # All other scripts: check last 1–3 whitespace-separated words to support
    # multi-word endings like 'Siya nawa' (tl).
    # U+060C (،) is the Arabic comma — must be stripped so آمين، is found correctly
    parts = text.rstrip('.!,;،।。！？').split()
    if not parts:
        return False
    import re as _re
    for n in range(1, 4):
        if len(parts) < n:
            break
        tail = ' '.join(parts[-n:]).strip('.,;:!?،।').lower()
        # Unicode variants from prayer_endings.json (ar: آمين, ru: аминь, ko: 아멘, tl: siya nawa…)
        # Normalize Arabic diacritics (tashkeel U+064B–U+065F) before lookup
        tail_normalized = _re.sub(r'[\u064b-\u065f]', '', tail)
        if tail_normalized in UNICODE_AMEN_VARIANTS:
            return True
        # Latin-script fallback (normalize accents: PT amém → amem)
        tail_ascii = unicodedata.normalize('NFD', tail).encode('ascii', 'ignore').decode()
        if tail_ascii in AMEN_VARIANTS:
            return True
    return False


def check_content_quality(entry: dict, lang: str = '') -> list:
    """
    Phase 1 content checks — returns list of issue strings.
    Checks: min length, truncation, prayer ending, double Amen, dup words.
    lang: ISO 639-1 code used to apply language-appropriate thresholds.
    """
    issues = []
    r = entry.get('reflexion', '').strip()
    o = entry.get('oracion',   '').strip()

    cjk = lang in ('zh', 'ja')
    reflexion_min = CJK_REFLEXION_MIN if cjk else REFLEXION_MIN_CHARS
    oracion_min   = CJK_ORACION_MIN   if cjk else ORACION_MIN_CHARS

    if len(r) < reflexion_min:
        issues.append(f'reflexion too short: {len(r)} chars (min {reflexion_min})')
    if len(o) < oracion_min:
        issues.append(f'oracion too short: {len(o)} chars (min {oracion_min})')
    if r and not r.endswith(SENTENCE_ENDINGS):
        issues.append(f'reflexion truncated — ends: ...{r.rstrip()[-40:]}')
    closing = o[-15:]
    _latin_amens   = len(re.findall(r'\bAm[eé]n\b', closing, re.IGNORECASE))
    # Exclude Latin AMEN_VARIANTS from unicode count to avoid double-counting:
    # _latin_amens already covers {'amen','amén','āmen','amem'} via regex.
    _non_latin_unicode = UNICODE_AMEN_VARIANTS - AMEN_VARIANTS
    _unicode_amens = sum(closing.count(v) for v in (_non_latin_unicode | CJK_AMEN_VARIANTS))
    if _latin_amens + _unicode_amens >= 2:
        issues.append('double_amen: duplicate Amen in closing')
    if o and not _check_prayer_ending(o):
        issues.append(f'prayer_ending: oracion does not end with Amen variant')
    dup = _find_consecutive_dup(r)
    if dup: issues.append(f'dup_words_reflexion: {dup}')
    dup = _find_consecutive_dup(o)
    if dup: issues.append(f'dup_words_oracion: {dup}')

    return issues


def validate_entry(entry: dict, date_key: str, lang: str, version: str) -> list:
    """
    Validate a single devotional entry.
    Returns list of issue strings (empty if valid).
    
    Checks:
    - All REQUIRED_FIELDS present and non-empty
    - language and version match expected values
    - date field matches date_key
    - para_meditar is a list (optional, but if present must be list of dicts with cita/texto)
    - tags is a list (if present)
    - Content quality checks (via check_content_quality)
    """
    issues = []
    eid = entry.get("id", f"UNKNOWN@{date_key}")
    
    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in entry:
            issues.append(f"missing '{field}'")
        elif isinstance(entry[field], str) and not entry[field].strip():
            issues.append(f"empty '{field}'")
    
    # Check language and version
    if entry.get("language") != lang:
        issues.append(f"language mismatch: got '{entry.get('language')}'")
    if entry.get("version") != version:
        issues.append(f"version mismatch: got '{entry.get('version')}'")
    
    # Check date field
    if entry.get("date") != date_key:
        issues.append(f"date field '{entry.get('date')}' ≠ key '{date_key}'")
    
    # Check para_meditar structure
    pm = entry.get("para_meditar")
    if pm is not None:
        if not isinstance(pm, list):
            issues.append("para_meditar not a list")
        else:
            for j, ref in enumerate(pm):
                if not isinstance(ref, dict):
                    issues.append(f"para_meditar[{j}] not a dict")
                else:
                    for pf in PARA_MEDITAR_FIELDS:
                        if pf not in ref:
                            issues.append(f"para_meditar[{j}] missing '{pf}'")
    
    # Check tags
    if "tags" in entry and not isinstance(entry["tags"], list):
        issues.append("tags not a list")
    
    # Content quality checks (Phase 1)
    issues.extend(check_content_quality(entry, lang=lang))
    
    return issues


def check_latin(text: str, extra: set) -> tuple[list, dict]:
    """Returns (bad_words, label_counts)"""
    allowed = ALWAYS_ALLOWED | extra
    bad, labels = [], {}
    for m in LATIN_RE.findall(text):
        if m in allowed:
            labels[m] = labels.get(m, 0) + 1
        else:
            bad.append(m)
    return bad, labels


def check_spanish_leak(text: str) -> list:
    words = set(re.findall(r"[A-Za-zÁáÉéÍíÓóÚúÑñÜü]+", text))
    return [w for w in SPANISH_LEAKS if w in words]


def validate(filepath, lang_override, version_override, expected_min: int = 0):
    lines, errors = [], []
    summary = {"file": "", "entries": 0, "version_err": 0, "lang_err": 0,
               "date_gaps": 0, "latin_err": 0, "content_err": 0, "other_err": 0, "status": "—"}

    def e(msg): errors.append(msg); lines.append(f"  ❌ {msg}")
    def w(msg): lines.append(f"  ⚠️  {msg}")
    def ok(msg): lines.append(f"  ✅ {msg}")
    def info(msg): lines.append(f"  ℹ️  {msg}")

    path = Path(filepath)
    summary["file"] = path.name
    lines += ["=" * 58, f"📋 File: {path.name}", "=" * 58]

    if not path.exists():
        e("File not found"); summary["status"] = "❌ NOT FOUND"
        return False, "\n".join(lines), summary

    match = FILENAME_PATTERN.search(path.name)
    expected_year    = int(match.group("year"))    if match else None
    expected_lang    = lang_override.strip()    or (match.group("lang")    if match else None)
    expected_version = version_override.strip() or (match.group("version") if match else None)
    ok(f"Lang: {expected_lang} | Version: {expected_version} | Year: {expected_year}")

    non_latin = expected_lang in NON_LATIN_LANGS
    latin_script = expected_lang in {"en", "pt", "fr"}
    info(f"Latin check: {'ENABLED — non-Latin script' if non_latin else 'Spanish leak check' if latin_script else 'skipped'}")

    try:
        with open(path, encoding="utf-8") as _f:
            data = json.load(_f)
    except json.JSONDecodeError as ex:
        e(f"Invalid JSON: {ex}"); summary["status"] = "❌ INVALID JSON"
        return False, "\n".join(lines), summary

    if "data" not in data:
        e('Missing root "data" key'); summary["status"] = "❌ FAILED"
        return False, "\n".join(lines), summary

    lang_root = data["data"]
    if expected_lang not in lang_root:
        e(f'Language key "{expected_lang}" not found. Found: {list(lang_root.keys())}')
        summary["status"] = "❌ FAILED"
        return False, "\n".join(lines), summary

    date_map = lang_root[expected_lang]
    ok(f"Structure valid — language key: {expected_lang}")

    all_entries = []
    for date_key, date_value in date_map.items():
        if not isinstance(date_value, list):
            e(f"Date {date_key}: not a list"); continue
        for i, entry in enumerate(date_value):
            if isinstance(entry, dict): all_entries.append((date_key, entry))
            else: e(f"Date {date_key}[{i}]: not a dict")

    total = len(all_entries)
    summary["entries"] = total
    ok(f"Total entries: {total}")
    # Auto-derive expected count from the file's own date range; overridable via --expected
    if date_map:
        _rdates = sorted(date_map.keys())
        try:
            _d0 = date.fromisoformat(_rdates[0])
            _d1 = date.fromisoformat(_rdates[-1])
            _auto_expected = (_d1 - _d0).days + 1
            _range_label   = f"{_rdates[0]} → {_rdates[-1]}"
        except ValueError:
            _auto_expected = EXPECTED_MIN_ENTRIES
            _range_label   = "unknown range"
    else:
        _auto_expected = EXPECTED_MIN_ENTRIES
        _range_label   = "unknown range"
    _expected = expected_min if expected_min > 0 else _auto_expected
    if total < _expected:
        w(f"Only {total} entries — expected ≥ {_expected} (date range: {_range_label})")
    if total == 0:
        e("No entries — aborting"); summary["status"] = "❌ FAILED"
        return False, "\n".join(lines), summary

    seen_dates = {}
    version_mismatches, lang_mismatches, latin_issues, spanish_issues, content_issues = [], [], [], [], []
    label_totals = {}
    extra_allowed = {expected_version} if expected_version else set()

    for date_key, entry in all_entries:
        eid = entry.get("id", f"UNKNOWN@{date_key}")

        for field in REQUIRED_FIELDS:
            if field not in entry: e(f"[{eid}] Missing: '{field}'")
            elif isinstance(entry[field], str) and not entry[field].strip(): e(f"[{eid}] Empty: '{field}'")

        if expected_version and entry.get("version") != expected_version:
            version_mismatches.append(f"[{eid}] version='{entry.get('version')}' expected='{expected_version}'")
        if expected_lang and entry.get("language") != expected_lang:
            lang_mismatches.append(f"[{eid}] language='{entry.get('language')}' expected='{expected_lang}'")

        entry_date = entry.get("date", "")
        if entry_date != date_key: e(f"[{eid}] date '{entry_date}' ≠ key '{date_key}'")
        if entry_date in seen_dates: e(f"Duplicate: {entry_date}")
        else: seen_dates[entry_date] = eid

        pm = entry.get("para_meditar")
        if pm is not None:
            if not isinstance(pm, list): e(f"[{eid}] para_meditar not a list")
            else:
                for j, ref in enumerate(pm):
                    for pf in PARA_MEDITAR_FIELDS:
                        if pf not in ref: e(f"[{eid}] para_meditar[{j}] missing '{pf}'")
        if "tags" in entry and not isinstance(entry["tags"], list): e(f"[{eid}] tags not a list")

        # Non-Latin script check
        if non_latin:
            for field in ["reflexion", "oracion", "versiculo"]:
                bad, labels = check_latin(entry.get(field, ""), extra_allowed)
                for word in bad: latin_issues.append(f"{date_key} [{field}]: \"{word}\"")
                for lbl, cnt in labels.items(): label_totals[lbl] = label_totals.get(lbl, 0) + cnt

        # Spanish leak check
        if latin_script:
            for field in ["reflexion", "oracion"]:
                leaks = check_spanish_leak(entry.get(field, ""))
                for word in leaks: spanish_issues.append(f"{date_key} [{field}]: \"{word}\"")

        # Content quality check (Phase 1)
        for issue in check_content_quality(entry, lang=expected_lang):
            content_issues.append(f"{date_key}: {issue}")

    # Summaries
    if version_mismatches:
        e(f"VERSION MISMATCH — {len(version_mismatches)} entries")
        for m in version_mismatches[:5]: lines.append(f"    {m}")
        if len(version_mismatches) > 5: lines.append(f"    ... and {len(version_mismatches)-5} more")

    if lang_mismatches:
        e(f"LANGUAGE MISMATCH — {len(lang_mismatches)} entries")

    if latin_issues:
        e(f"LATIN CHARS FOUND — {len(latin_issues)} occurrences")
        for li in latin_issues[:15]: lines.append(f"    {li}")
        if len(latin_issues) > 15: lines.append(f"    ... and {len(latin_issues)-15} more")

    if label_totals:
        info(f"Allowed labels (correct): " + ", ".join(f"{k}×{v}" for k, v in sorted(label_totals.items())))

    if spanish_issues:
        deduped = list(dict.fromkeys(spanish_issues))
        e(f"SPANISH LEAK — {len(deduped)} occurrences")
        for si in deduped[:15]: lines.append(f"    {si}")
        if len(deduped) > 15: lines.append(f"    ... and {len(deduped)-15} more")

    if content_issues:
        e(f"CONTENT ISSUES — {len(content_issues)} entries")
        for ci in content_issues[:20]: lines.append(f"    {ci}")
        if len(content_issues) > 20: lines.append(f"    ... and {len(content_issues)-20} more")
    else:
        ok("Content quality: all entries pass Phase 1")

    # Date gaps
    gap_count = 0
    if seen_dates:
        sorted_dates = sorted(seen_dates.keys())
        try:
            d_start, d_end = date.fromisoformat(sorted_dates[0]), date.fromisoformat(sorted_dates[-1])
            expected_set = set()
            cur = d_start
            while cur <= d_end: expected_set.add(cur.isoformat()); cur += timedelta(days=1)
            missing = sorted(expected_set - set(sorted_dates))
            gap_count = len(missing)
            if missing: w(f"Missing dates ({len(missing)}): {missing[:10]}{'...' if len(missing)>10 else ''}")
            else: ok(f"No date gaps — {sorted_dates[0]} → {sorted_dates[-1]}")
        except ValueError: w("Could not parse dates")

    lines += ["", "=" * 58]
    passed = len(errors) == 0
    lines.append("✅ PASSED" if passed else f"❌ FAILED — {len(errors)} error(s)")
    lines.append("=" * 58)

    summary.update({
        "version_err": len(version_mismatches), "lang_err": len(lang_mismatches),
        "date_gaps": gap_count, "latin_err": len(latin_issues) + len(spanish_issues),
        "content_err": len(content_issues),
        "other_err": 0, "status": "✅ PASSED" if passed else "❌ FAILED",
    })
    return passed, "\n".join(lines), summary


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Devocional JSON Validator v5")
        self.resizable(True, True)
        self.minsize(820, 640)
        self._last_result = ""
        self._build()

    def _build(self):
        pad = {"padx": 10, "pady": 6}
        ff = ttk.LabelFrame(self, text="JSON File", padding=8)
        ff.pack(fill="x", **pad)
        self.file_var = tk.StringVar()
        ttk.Entry(ff, textvariable=self.file_var, width=65).pack(side="left", fill="x", expand=True)
        ttk.Button(ff, text="Browse…", command=self._browse).pack(side="left", padx=(6,0))

        mf = ttk.LabelFrame(self, text="Metadata Override (blank = auto-detect)", padding=8)
        mf.pack(fill="x", **pad)
        ttk.Label(mf, text="Language:").grid(row=0, column=0, sticky="w")
        self.lang_var = tk.StringVar()
        ttk.Entry(mf, textvariable=self.lang_var, width=8).grid(row=0, column=1, sticky="w", padx=(4,0))
        ttk.Label(mf, text="(hi, es, en, ja, zh…)").grid(row=0, column=2, sticky="w", padx=(8,0))
        ttk.Label(mf, text="Version:").grid(row=1, column=0, sticky="w", pady=(6,0))
        self.version_var = tk.StringVar()
        ttk.Entry(mf, textvariable=self.version_var, width=18).grid(row=1, column=1, sticky="w", padx=(4,0), pady=(6,0))
        ttk.Label(mf, text="(HIOV, RVR1960, KJV…)").grid(row=1, column=2, sticky="w", padx=(8,0), pady=(6,0))

        bf = tk.Frame(self); bf.pack(pady=(4,0))
        ttk.Button(bf, text="▶  Validate",     command=self._run).pack(side="left", padx=4)
        ttk.Button(bf, text="🗑  Clear Table",  command=self._clear).pack(side="left", padx=4)
        ttk.Button(bf, text="💾  Save Report",  command=self._save_report).pack(side="left", padx=4)

        tf = ttk.LabelFrame(self, text="Validation Summary Table", padding=8)
        tf.pack(fill="x", **pad)
        cols = ("File", "Entries", "Version ❌", "Lang ❌", "Gaps ⚠️", "Char Issues ❌", "Content ❌", "Status")
        widths = [200, 60, 90, 70, 70, 100, 90, 90]
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=5)
        for col, w in zip(cols, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor="center")
        self.tree.pack(fill="x")

        of = ttk.LabelFrame(self, text="Detailed Results", padding=8)
        of.pack(fill="both", expand=True, **pad)
        self.output = scrolledtext.ScrolledText(of, wrap="word", font=("Courier", 10), state="disabled")
        self.output.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w").pack(
            fill="x", side="bottom", padx=10, pady=(0,6))

    def _browse(self):
        p = filedialog.askopenfilename(title="Select devotional JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if p:
            self.file_var.set(p)
            m = FILENAME_PATTERN.search(Path(p).name)
            if m:
                if not self.lang_var.get():    self.lang_var.set(m.group("lang"))
                if not self.version_var.get(): self.version_var.set(m.group("version"))

    def _run(self):
        fp = self.file_var.get().strip()
        if not fp: self.status_var.set("⚠️  Please select a file first"); return
        self.status_var.set("Validating…"); self.update()
        passed, result, summary = validate(fp, self.lang_var.get(), self.version_var.get())
        self._last_result = result
        self.output.config(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("end", result)
        self.output.config(state="disabled")
        self.tree.insert("", "end", values=(
            summary["file"], summary["entries"],
            summary["version_err"] or "✅", summary["lang_err"] or "✅",
            summary["date_gaps"]   or "✅", summary["latin_err"] or "✅",
            summary["content_err"] or "✅",
            summary["status"],
        ))
        self.status_var.set("✅ Passed" if passed else "❌ Failed — see results above")

    def _clear(self):
        for row in self.tree.get_children(): self.tree.delete(row)

    def _save_report(self):
        if not self._last_result:
            self.status_var.set("⚠️  No report to save — run validation first")
            return
        fp = filedialog.asksaveasfilename(
            title="Save validation report",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not fp:
            return
        try:
            Path(fp).write_text(self._last_result, encoding="utf-8")
            self.status_var.set(f"✅ Report saved: {Path(fp).name}")
        except OSError as ex:
            self.status_var.set(f"❌ Save failed: {ex}")

if __name__ == "__main__":
    # ── CLI mode: at least --file provided ────────────────────────────────────
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(
            description="Devocional JSON Validator v5",
            epilog=(
                "Examples:\n"
                "  python validate_devocional_gui.py --file path.json --lang de --version LU17\n"
                "  python validate_devocional_gui.py --file path.json --lang hi --version HIOV\n"
                "  python validate_devocional_gui.py   (no args → GUI mode)"
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument("--file",    required=True, help="Path to devotional JSON file")
        parser.add_argument("--lang",    default="",    help="Language code (e.g. de, hi, es, en)")
        parser.add_argument("--version", default="",    help="Bible version code (e.g. LU17, HIOV, RVR1960)")
        parser.add_argument("--expected", type=int, default=0,
                            help="Expected minimum entry count (0 = auto-detect from date range)")
        args = parser.parse_args()

        passed, result, summary = validate(args.file, args.lang, args.version, args.expected)
        print(result)
        print()
        print(f"  Entries   : {summary['entries']}")
        print(f"  Version ❌ : {summary['version_err'] or '✅'}")
        print(f"  Lang ❌    : {summary['lang_err'] or '✅'}")
        print(f"  Gaps ⚠️   : {summary['date_gaps'] or '✅'}")
        print(f"  Char Issues: {summary['latin_err'] or '✅'}")
        print(f"  Content ❌ : {summary['content_err'] or '✅'}")
        print(f"  Status     : {summary['status']}")
        sys.exit(0 if passed else 1)

    # ── GUI mode: no args ─────────────────────────────────────────────────────
    else:
        App().mainloop()
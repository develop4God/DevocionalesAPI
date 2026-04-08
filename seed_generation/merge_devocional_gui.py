#!/usr/bin/env python3
"""
merge_devocional_gui.py — Devocional Partial Merge Tool v1.0

Intelligently loads, validates and merges partial devotional JSON files.

Features:
  - Auto-scan a folder for partial devotional JSON files
  - File list with enable/disable checkboxes
  - 365-day coverage map (color-coded)
  - Entry browser with filter, lint preview, and detail panel
  - Lint validation report per file + merged summary
  - One-click merge → Devocional_year_YYYY_lang_VERSION.json
    saved in the same folder as the partial files

Usage:
  python merge_devocional_gui.py
"""

import json, re, os, unicodedata
import tkinter as tk
from tkinter import filedialog, ttk, scrolledtext, messagebox
from pathlib import Path
from datetime import date, timedelta

# ─────────────────────────── constants (shared with validator) ──────────────
REQUIRED_FIELDS = [
    "id", "date", "language", "version",
    "versiculo", "reflexion", "para_meditar", "oracion", "tags",
]
PARA_MEDITAR_FIELDS = ["cita", "texto"]

# Matches both partial ("163-Devocional_year_...") and full ("Devocional_year_...")
FILENAME_PATTERN = re.compile(
    r"(?:(?P<count>\d+)-)?Devocional_year_(?P<year>\d{4})_(?P<lang>[a-z]{2})_(?P<version>[^\.]+)\.json$"
)

NON_LATIN_LANGS  = {"hi", "ja", "zh"}
ALWAYS_ALLOWED   = {
    "HIOV", "OV", "HERV", "ERV", "KJV", "NIV", "NVI",
    "RVR1960", "ARC", "TOB", "LSG1910",
}
LATIN_RE = re.compile(r"[a-zA-Z]+")
SPANISH_LEAKS = {
    "Amén", "Jesús", "Señor", "señor", "también",
    "Espíritu", "espíritu", "oración", "Padre",
    "padre", "gracia", "gloria", "alabanza", "misericordia",
    "bendición", "bendicion", "nombre", "corazón", "corazon", "Dios",
}

REFLEXION_MIN_CHARS = 800
ORACION_MIN_CHARS   = 150
CJK_REFLEXION_MIN   = 250
CJK_ORACION_MIN     = 80
SENTENCE_ENDINGS    = (
    '.', '!', '?', '»', '"', "'",
    '\u2018', '\u2019', '\u201c', '\u201d', '।', '。', '！', '？',
)
CONSECUTIVE_DUP_SKIP = frozenset({
    'heilig', 'holy', 'kadosh', 'halleluja', 'hosanna',
    'amen', 'amén', 'āmen', 'nous', 'vous', 'पवित्र', 'saul',
})
SENT_END_PUNCT    = frozenset({'.', '!', '?', ':', '»', '\u201d'})
AMEN_VARIANTS     = frozenset({'amen', 'amén', 'āmen', 'amem'})
CJK_AMEN_VARIANTS = frozenset({'阿们', '阿门', '阿們', '阿門', 'アーメン', 'ア-メン'})
HINDI_AMEN       = frozenset({'आमीन', 'आमेन'})

# Load all Amen variants from prayer_endings.json (covers ar, ru, ko, uk, etc.)
_PRAYER_ENDINGS_FILE = Path(__file__).with_name('prayer_endings.json')
UNICODE_AMEN_VARIANTS: frozenset = frozenset()
try:
    _pe = json.load(open(_PRAYER_ENDINGS_FILE, encoding='utf-8'))
    UNICODE_AMEN_VARIANTS = frozenset(
        v.lower() for variants in _pe.values()
        if isinstance(variants, list)
        for v in variants
    )
except Exception:
    pass

# ─────────────────────────── validation helpers ─────────────────────────────

def _find_consecutive_dup(text: str):
    words = text.split()
    for i in range(len(words) - 1):
        raw1 = words[i]
        if raw1 and raw1[-1] in SENT_END_PUNCT:
            continue
        w1 = raw1.strip('.,;:!?').lower()
        w2 = words[i + 1].strip('.,;:!?').lower()
        if w1 == w2 and len(w1) > 3 and w1 not in CONSECUTIVE_DUP_SKIP:
            return f'"{raw1} {words[i+1]}"'
    return None


def _check_prayer_ending(oracion: str) -> bool:
    text = oracion.strip()
    # CJK: check suffix directly (no word boundaries)
    stripped_cjk = text.rstrip('。！？.!,;')
    for cjk in CJK_AMEN_VARIANTS:
        if stripped_cjk.endswith(cjk):
            return True
    # All other scripts: check last whitespace-separated word
    parts = text.rstrip('.!,;।。！？').split()
    if not parts:
        return False
    last_w = parts[-1].strip('.,;:!?।').lower()
    # Unicode variants from prayer_endings.json (ar: آمين, ru: аминь, ko: 아멘, …)
    if last_w in UNICODE_AMEN_VARIANTS:
        return True
    # Latin-script fallback (normalize accents: PT amém → amem)
    last_ascii = unicodedata.normalize('NFD', last_w).encode('ascii', 'ignore').decode()
    return last_ascii in AMEN_VARIANTS


def check_content_quality(entry: dict, lang: str = '') -> list:
    issues = []
    r = entry.get('reflexion', '').strip()
    o = entry.get('oracion',   '').strip()
    cjk = lang in ('zh', 'ja')
    r_min = CJK_REFLEXION_MIN if cjk else REFLEXION_MIN_CHARS
    o_min = CJK_ORACION_MIN   if cjk else ORACION_MIN_CHARS

    if len(r) < r_min:
        issues.append(f'reflexion too short ({len(r)}<{r_min})')
    if len(o) < o_min:
        issues.append(f'oracion too short ({len(o)}<{o_min})')
    if r and not r.endswith(SENTENCE_ENDINGS):
        issues.append(f'reflexion truncated')
    if len(re.findall(r'\bAm[eé]n\b', o[-120:], re.IGNORECASE)) >= 2:
        issues.append('double_amen')
    if o and not _check_prayer_ending(o):
        issues.append('no_amen_ending')
    dup = _find_consecutive_dup(r)
    if dup:
        issues.append(f'dup_words: {dup}')
    return issues


def validate_entry(entry: dict, date_key: str, lang: str, version: str) -> list:
    """Full per-entry validation. Returns list of issue strings."""
    issues = []
    for field in REQUIRED_FIELDS:
        if field not in entry:
            issues.append(f"missing '{field}'")
        elif isinstance(entry[field], str) and not entry[field].strip():
            issues.append(f"empty '{field}'")

    if entry.get('version') != version:
        issues.append(f"version mismatch: got '{entry.get('version')}'")
    if entry.get('language') != lang:
        issues.append(f"lang mismatch: got '{entry.get('language')}'")
    if entry.get('date') != date_key:
        issues.append(f"date field '{entry.get('date')}' ≠ key '{date_key}'")

    pm = entry.get('para_meditar')
    if pm is not None:
        if not isinstance(pm, list):
            issues.append("para_meditar not a list")
        else:
            for j, ref in enumerate(pm):
                if not isinstance(ref, dict):
                    issues.append(f"para_meditar[{j}] not a dict")
                    continue
                for pf in PARA_MEDITAR_FIELDS:
                    if pf not in ref:
                        issues.append(f"para_meditar[{j}] missing '{pf}'")

    if 'tags' in entry and not isinstance(entry['tags'], list):
        issues.append("tags not a list")

    extra = {version} if version else set()
    if lang in NON_LATIN_LANGS:
        for field in ("reflexion", "oracion", "versiculo"):
            bad = [w for w in LATIN_RE.findall(entry.get(field, ''))
                   if w not in (ALWAYS_ALLOWED | extra)]
            if bad:
                issues.append(f"latin_chars in {field}: {bad[:3]}")

    if lang in ("en", "pt", "fr"):
        for field in ("reflexion", "oracion"):
            leaks = [w for w in SPANISH_LEAKS
                     if w in set(re.findall(r"[A-Za-zÁáÉéÍíÓóÚúÑñÜü]+", entry.get(field, '')))]
            if leaks:
                issues.append(f"spanish_leak in {field}: {leaks}")

    for qi in check_content_quality(entry, lang=lang):
        issues.append(qi)

    return issues


# ─────────────────────────── PartialFile model ──────────────────────────────

class PartialFile:
    """Represents one loaded partial JSON file."""

    def __init__(self, path: Path):
        self.path    = path
        self.name    = path.name
        self.lang    = None
        self.version = None
        self.year    = None
        self.entries = {}        # date_key → entry dict (first entry per date)
        self.errors  = []        # fatal load errors
        self.entry_issues = {}   # date_key → [issue strings]
        self.enabled = True
        self._load()

    def _load(self):
        m = FILENAME_PATTERN.search(self.path.name)
        if not m:
            self.errors.append(f"Filename doesn't match pattern")
            return
        self.year    = int(m.group('year'))
        self.lang    = m.group('lang')
        self.version = m.group('version')

        try:
            data = json.load(open(self.path, encoding='utf-8'))
        except json.JSONDecodeError as ex:
            self.errors.append(f"Invalid JSON: {ex}")
            return

        if 'data' not in data:
            self.errors.append("Missing root 'data' key")
            return
        if self.lang not in data['data']:
            self.errors.append(f"Language key '{self.lang}' not found")
            return

        date_map = data['data'][self.lang]
        for date_key, date_value in date_map.items():
            if not isinstance(date_value, list) or not date_value:
                continue
            entry = date_value[0]
            if not isinstance(entry, dict):
                continue
            self.entries[date_key] = entry
            issues = validate_entry(entry, date_key, self.lang, self.version)
            if issues:
                self.entry_issues[date_key] = issues

    @property
    def entry_count(self): return len(self.entries)

    @property
    def date_range(self):
        if not self.entries:
            return ('—', '—')
        s = sorted(self.entries.keys())
        return (s[0], s[-1])

    @property
    def issue_count(self): return len(self.entry_issues)

    @property
    def group_key(self):
        return (self.year, self.lang, self.version) if self.lang else None

    @property
    def is_valid(self): return not self.errors


# ─────────────────────────── Main GUI ───────────────────────────────────────

CELL   = 22   # pixels per coverage-map cell
LBL_W  = 72   # label width for month names in coverage map
MON_H  = 16   # extra gap between months in coverage map

COLOR_OK      = "#81c784"
COLOR_MISSING = "#ef9a9a"
COLOR_OVERLAP = "#ffb74d"
COLOR_ISSUES  = "#fff176"
COLOR_EMPTY   = "#e0e0e0"
COLOR_DISABLED= "#cccccc"


class MergeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Devocional Partial Merge Tool v1.0")
        self.resizable(True, True)
        self.minsize(1100, 760)

        self.partial_files: list[PartialFile] = []
        self._cov_date_rects = {}   # rect_id → date_str (kept for legacy compat)
        self._cov_coord_map  = {}   # (col_i, row_i) → date_str  (O(1) lookup)
        self._cov_cache      = ({}, {}, {})   # (merged, sources, overlaps) snapshot
        self._cov_issues     = {}             # issues snapshot

        self._build()

    # ══════════════════════════════════════════ UI Construction ══════════════

    def _build(self):
        # ── Toolbar ──────────────────────────────────────────────────────────
        tb = tk.Frame(self, bd=1, relief="groove")
        tb.pack(fill="x", padx=6, pady=(6, 2))

        ttk.Button(tb, text="📂 Scan Folder", command=self._scan_folder).pack(side="left", padx=3, pady=3)
        ttk.Button(tb, text="➕ Add Files",   command=self._add_files).pack(side="left", padx=3, pady=3)
        ttk.Button(tb, text="🗑 Clear All",   command=self._clear_all).pack(side="left", padx=3, pady=3)
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=4)
        ttk.Button(tb, text="🔄 Refresh",     command=self._refresh_all).pack(side="left", padx=3, pady=3)

        ttk.Button(tb, text="⚡ Merge & Export", command=self._do_merge).pack(side="right", padx=6, pady=3)

        self.status_var = tk.StringVar(value="Ready — scan a folder or add files to begin")
        tk.Label(tb, textvariable=self.status_var, anchor="w", fg="#555").pack(side="left", padx=6)

        # ── Notebook ─────────────────────────────────────────────────────────
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=4)

        self.tab_files    = ttk.Frame(self.nb)
        self.tab_coverage = ttk.Frame(self.nb)
        self.tab_review   = ttk.Frame(self.nb)
        self.tab_lint     = ttk.Frame(self.nb)

        self.nb.add(self.tab_files,    text="📁  Files")
        self.nb.add(self.tab_coverage, text="📅  Coverage Map")
        self.nb.add(self.tab_review,   text="🔍  Entry Review")
        self.nb.add(self.tab_lint,     text="🔎  Lint Report")

        self._build_files_tab()
        self._build_coverage_tab()
        self._build_review_tab()
        self._build_lint_tab()

    # ── Tab 1 : Files ────────────────────────────────────────────────────────

    def _build_files_tab(self):
        # Group header
        gf = ttk.LabelFrame(self.tab_files, text="Detected Groups  (Year / Language / Version)", padding=6)
        gf.pack(fill="x", padx=6, pady=(6, 2))
        self.group_label = tk.Label(gf, text="No files loaded", anchor="w", wraplength=900)
        self.group_label.pack(fill="x")

        # Instruction label
        hint = tk.Label(
            self.tab_files,
            text="Click the ✓ column to enable/disable a file. Disabled files are excluded from merge.",
            anchor="w", fg="#555", font=("Helvetica", 9, "italic"),
        )
        hint.pack(fill="x", padx=8)

        # File table
        COLS   = ("✓", "File", "Entries", "First Date", "Last Date", "Issues", "Status")
        WIDTHS = [30, 350, 65, 100, 100, 65, 110]

        ff = ttk.LabelFrame(self.tab_files, text="Partial Files", padding=6)
        ff.pack(fill="both", expand=True, padx=6, pady=4)

        ys = ttk.Scrollbar(ff, orient="vertical")
        ys.pack(side="right", fill="y")

        self.file_tree = ttk.Treeview(
            ff, columns=COLS, show="headings",
            yscrollcommand=ys.set, height=14,
        )
        ys.config(command=self.file_tree.yview)

        for col, w in zip(COLS, WIDTHS):
            self.file_tree.heading(col, text=col)
            self.file_tree.column(col, width=w,
                                  anchor="center" if col != "File" else "w")

        self.file_tree.pack(fill="both", expand=True)
        self.file_tree.bind("<ButtonRelease-1>", self._on_file_tree_click)

        self.file_tree.tag_configure("ok",       background="#e8f5e9")
        self.file_tree.tag_configure("warn",     background="#fff8e1")
        self.file_tree.tag_configure("error",    background="#ffebee")
        self.file_tree.tag_configure("disabled", background="#eeeeee", foreground="#999")

    # ── Tab 2 : Coverage Map ─────────────────────────────────────────────────

    def _build_coverage_tab(self):
        # Legend
        lf = ttk.LabelFrame(self.tab_coverage, text="Legend", padding=4)
        lf.pack(fill="x", padx=6, pady=(6, 2))

        for label, color in [
            ("Covered ✓", COLOR_OK),
            ("Missing ✗", COLOR_MISSING),
            ("Overlap !", COLOR_OVERLAP),
            ("Issues ⚠",  COLOR_ISSUES),
        ]:
            tk.Frame(lf, bg=color, width=16, height=16).pack(side="left", padx=(2, 0))
            tk.Label(lf, text=label).pack(side="left", padx=(2, 14))

        self.cov_status_label = tk.Label(lf, text="", anchor="e")
        self.cov_status_label.pack(side="right", padx=8)

        # Canvas
        cf = ttk.LabelFrame(self.tab_coverage, text="Day-by-Day Coverage  (click a cell to jump to Entry Review)", padding=6)
        cf.pack(fill="both", expand=True, padx=6, pady=4)

        xs = ttk.Scrollbar(cf, orient="horizontal")
        ys = ttk.Scrollbar(cf, orient="vertical")
        xs.pack(side="bottom", fill="x")
        ys.pack(side="right",  fill="y")

        self.cov_canvas = tk.Canvas(cf, bg="white",
                                    xscrollcommand=xs.set, yscrollcommand=ys.set)
        xs.config(command=self.cov_canvas.xview)
        ys.config(command=self.cov_canvas.yview)
        self.cov_canvas.pack(fill="both", expand=True)

        # Tooltip label
        self.cov_tooltip = tk.Label(self.tab_coverage, text="",
                                    anchor="w", relief="solid", bg="lightyellow")
        self.cov_canvas.bind("<Motion>",     self._on_cov_motion)
        self.cov_canvas.bind("<Leave>",      lambda e: self.cov_tooltip.place_forget())
        self.cov_canvas.bind("<Button-1>",   self._on_cov_click)

    # ── Tab 3 : Entry Review ─────────────────────────────────────────────────

    def _build_review_tab(self):
        # Filter bar
        fb = tk.Frame(self.tab_review)
        fb.pack(fill="x", padx=6, pady=(6, 2))

        tk.Label(fb, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._refresh_review())
        ttk.Entry(fb, textvariable=self.filter_var, width=22).pack(side="left", padx=4)
        ttk.Button(fb, text="✕", width=2,
                   command=lambda: self.filter_var.set("")).pack(side="left")

        self.show_issues_var  = tk.BooleanVar()
        self.show_missing_var = tk.BooleanVar()
        ttk.Checkbutton(fb, text="Only issues",  variable=self.show_issues_var,
                        command=self._refresh_review).pack(side="left", padx=10)
        ttk.Checkbutton(fb, text="Only missing", variable=self.show_missing_var,
                        command=self._refresh_review).pack(side="left")

        self.review_count = tk.StringVar(value="")
        tk.Label(fb, textvariable=self.review_count, anchor="e").pack(side="right", padx=6)

        # Entry table
        COLS   = ("#", "Date", "Source File", "ID", "Versiculo (preview)", "Len(R)", "Issues")
        WIDTHS = [40, 90, 210, 200, 280, 65, 160]

        tf = ttk.LabelFrame(self.tab_review, text="Entries", padding=4)
        tf.pack(fill="both", expand=True, padx=6, pady=2)

        ys2 = ttk.Scrollbar(tf, orient="vertical")
        xs2 = ttk.Scrollbar(tf, orient="horizontal")
        ys2.pack(side="right",  fill="y")
        xs2.pack(side="bottom", fill="x")

        self.review_tree = ttk.Treeview(
            tf, columns=COLS, show="headings",
            yscrollcommand=ys2.set, xscrollcommand=xs2.set, height=17,
        )
        ys2.config(command=self.review_tree.yview)
        xs2.config(command=self.review_tree.xview)

        for col, w in zip(COLS, WIDTHS):
            self.review_tree.heading(col, text=col,
                                     command=lambda c=col: self._sort_review(c))
            self.review_tree.column(col, width=w,
                                    anchor="center" if col in ("#", "Len(R)") else "w")

        self.review_tree.pack(fill="both", expand=True)
        self.review_tree.bind("<<TreeviewSelect>>", self._on_review_select)

        self.review_tree.tag_configure("ok",      background="#e8f5e9")
        self.review_tree.tag_configure("warn",     background="#fff8e1")
        self.review_tree.tag_configure("missing",  background="#ffebee")
        self.review_tree.tag_configure("overlap",  background="#fff3e0")

        # Detail panel
        df = ttk.LabelFrame(self.tab_review, text="Entry Detail", padding=4)
        df.pack(fill="x", padx=6, pady=(0, 6))

        self.detail_text = scrolledtext.ScrolledText(
            df, height=7, wrap="word",
            font=("Courier", 9), state="disabled",
        )
        self.detail_text.pack(fill="x")

    # ── Tab 4 : Lint Report ───────────────────────────────────────────────────

    def _build_lint_tab(self):
        bf = tk.Frame(self.tab_lint)
        bf.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Button(bf, text="🔄 Re-run Lint", command=self._run_lint).pack(side="left", padx=4)

        self.lint_summary_var = tk.StringVar(value="")
        tk.Label(bf, textvariable=self.lint_summary_var, anchor="w", fg="#444").pack(side="left", padx=8)

        lf = ttk.LabelFrame(self.tab_lint, text="Lint Report", padding=6)
        lf.pack(fill="both", expand=True, padx=6, pady=4)

        self.lint_text = scrolledtext.ScrolledText(
            lf, wrap="word", font=("Courier", 9), state="disabled",
        )
        self.lint_text.pack(fill="both", expand=True)

    # ══════════════════════════════════════════ File loading ═════════════════

    def _scan_folder(self):
        folder = filedialog.askdirectory(title="Select folder containing partial devotional JSON files")
        if not folder:
            return
        folder = Path(folder)
        found = sorted(f for f in folder.glob("*.json")
                       if FILENAME_PATTERN.search(f.name))
        if not found:
            messagebox.showinfo("No Files Found",
                                f"No matching devotional JSON files found in:\n{folder}")
            return
        self._load_files(found)
        self.status_var.set(f"Scanned {len(found)} file(s) from '{folder.name}'")

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select partial devotional JSON files",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not paths:
            return
        self._load_files([Path(p) for p in paths])

    def _load_files(self, paths: list):
        existing = {pf.path for pf in self.partial_files}
        added = 0
        for path in paths:
            if path in existing:
                continue
            pf = PartialFile(path)
            self.partial_files.append(pf)
            added += 1
        if added:
            self._refresh_all()
            self.status_var.set(
                f"Loaded {added} new file(s).  Total: {len(self.partial_files)}"
            )

    def _clear_all(self):
        if self.partial_files and not messagebox.askyesno("Clear", "Remove all loaded files?"):
            return
        self.partial_files.clear()
        self._refresh_all()
        self.status_var.set("Cleared")

    def _refresh_all(self):
        self._refresh_file_table()
        self._refresh_coverage()
        self._refresh_review()
        self._run_lint()
        self._update_group_label()

    # ══════════════════════════════════════════ Data helpers ═════════════════

    def _active_files(self) -> list:
        return [pf for pf in self.partial_files if pf.enabled and pf.is_valid]

    def _build_merged(self):
        """Return (merged, sources, overlaps).
        merged   : date_str → entry dict  (last-listed file wins on conflict)
        sources  : date_str → filename
        overlaps : date_str → [all filenames that contain that date]
        """
        merged   = {}
        sources  = {}
        overlaps = {}

        for pf in self._active_files():
            for dk, entry in pf.entries.items():
                if dk in merged:
                    overlaps.setdefault(dk, [sources[dk]])
                    overlaps[dk].append(pf.name)
                merged[dk]  = entry
                sources[dk] = pf.name

        return merged, sources, overlaps

    def _year_range(self):
        """Determine the expected date range from active files' data.
        Returns (start_date, end_date, year_int) or None."""
        active = self._active_files()
        if not active:
            return None
        all_dates = set()
        for pf in active:
            all_dates |= set(pf.entries.keys())
        if not all_dates:
            return None
        try:
            s = date.fromisoformat(min(all_dates))
            e = date.fromisoformat(max(all_dates))
            return s, e, active[0].year
        except ValueError:
            return None

    def _all_dates_in_range(self):
        yr = self._year_range()
        if not yr:
            return []
        s, e, _ = yr
        out = []
        cur = s
        while cur <= e:
            out.append(cur.isoformat())
            cur += timedelta(days=1)
        return out

    def _all_issues(self) -> dict:
        """Issues for the merged result: each date uses the winning (last-loaded) file's issues.

        Using .update() over all files in order would keep stale issues from a losing
        file when the winning file's entry is clean — e.g. if file A has issues for
        date D but file B (which wins for D) does not, the old .update() approach would
        still report D as having issues.  This implementation corrects that.
        """
        # Determine which file actually contributes each date to the merged result
        # (last file in active order wins, matching _build_merged logic)
        source_pf: dict = {}
        for pf in self._active_files():
            for dk in pf.entries:
                source_pf[dk] = pf
        out = {}
        for dk, pf in source_pf.items():
            if dk in pf.entry_issues:
                out[dk] = pf.entry_issues[dk]
        return out

    # ══════════════════════════════════════════ Tab refreshes ════════════════

    # ── Files tab ────────────────────────────────────────────────────────────

    def _update_group_label(self):
        if not self.partial_files:
            self.group_label.config(text="No files loaded")
            return
        groups = {}
        for pf in self.partial_files:
            k = pf.group_key
            if k:
                groups[k] = groups.get(k, 0) + 1
        parts = [
            f"Year {y} | {l} | {v}  →  {c} file(s)"
            for (y, l, v), c in sorted(groups.items())
        ]
        self.group_label.config(text="     ".join(parts) if parts else "Unknown group")

    def _refresh_file_table(self):
        for row in self.file_tree.get_children():
            self.file_tree.delete(row)

        for pf in self.partial_files:
            chk  = "☑" if pf.enabled else "☐"
            dr   = pf.date_range

            if pf.errors:
                tag    = "error"
                status = "❌ Load Error"
                issues = str(len(pf.errors))
            elif not pf.enabled:
                tag    = "disabled"
                status = "— Disabled"
                issues = str(pf.issue_count)
            elif pf.issue_count > 0:
                tag    = "warn"
                status = f"⚠️ {pf.issue_count} issues"
                issues = str(pf.issue_count)
            else:
                tag    = "ok"
                status = "✅ OK"
                issues = "0"

            self.file_tree.insert("", "end", tags=(tag,), values=(
                chk, pf.name, pf.entry_count, dr[0], dr[1], issues, status,
            ))

    def _on_file_tree_click(self, event):
        col = self.file_tree.identify_column(event.x)
        row = self.file_tree.identify_row(event.y)
        if not row:
            return
        if col == "#1":   # checkbox column → toggle
            fname = self.file_tree.item(row, "values")[1]
            for pf in self.partial_files:
                if pf.name == fname:
                    pf.enabled = not pf.enabled
                    break
            self._refresh_all()

    # ── Coverage tab ─────────────────────────────────────────────────────────

    def _refresh_coverage(self):
        self.cov_canvas.delete("all")
        self._cov_date_rects.clear()
        self._cov_coord_map.clear()

        merged, sources, overlaps = self._build_merged()
        all_dates = self._all_dates_in_range()
        issues    = self._all_issues()

        # Cache for O(1) tooltip lookups — avoids rebuilding on every mouse move
        self._cov_cache  = (merged, sources, overlaps)
        self._cov_issues = issues

        if not all_dates:
            self.cov_canvas.create_text(200, 50, text="No data loaded", fill="grey")
            self.cov_status_label.config(text="")
            return

        # Group dates by (year, month)
        month_groups: dict = {}    # (y, m) → [date_str, ...]
        for ds in all_dates:
            d = date.fromisoformat(ds)
            month_groups.setdefault((d.year, d.month), []).append(ds)

        sorted_months = sorted(month_groups.keys())

        # Layout: each month = a row; days = columns
        # Row heading (month label) on the left, then day cells
        TOP    = 30   # top padding
        LEFT   = LBL_W
        HEADER = 18   # day-of-week / day number header height

        # Draw day-of-month headers (1–31)
        for day_n in range(1, 32):
            x = LEFT + (day_n - 1) * CELL + CELL // 2
            self.cov_canvas.create_text(x, TOP - 10, text=str(day_n),
                                        font=("Helvetica", 7), fill="#333")

        row_y = TOP
        for row_i, (yr_m, mo_m) in enumerate(sorted_months):
            month_name = date(yr_m, mo_m, 1).strftime("%b %Y")
            self.cov_canvas.create_text(LEFT - 4, row_y + CELL // 2, text=month_name,
                                        font=("Helvetica", 8, "bold"), anchor="e",
                                        fill="#333")

            for ds in sorted(month_groups[(yr_m, mo_m)]):
                d_obj  = date.fromisoformat(ds)
                col_i  = d_obj.day - 1   # 0-based column
                x1     = LEFT + col_i * CELL
                y1     = row_y
                x2, y2 = x1 + CELL, y1 + CELL

                if ds in overlaps:
                    color = COLOR_OVERLAP
                elif ds in merged:
                    color = COLOR_ISSUES if ds in issues else COLOR_OK
                else:
                    color = COLOR_MISSING

                rect_id = self.cov_canvas.create_rectangle(
                    x1, y1, x2, y2, fill=color, outline="white", width=1,
                )
                self.cov_canvas.create_text(
                    x1 + CELL // 2, y1 + CELL // 2,
                    text=str(d_obj.day), font=("Helvetica", 7),
                )
                self._cov_date_rects[rect_id] = ds
                self._cov_coord_map[(col_i, row_i)] = ds  # O(1) reverse lookup

            row_y += CELL + 2   # small gap between month rows

        # Stats
        total   = len(all_dates)
        covered = sum(1 for d in all_dates if d in merged)
        missing = total - covered
        n_ov    = len(overlaps)
        n_iss   = len(issues)
        self.cov_status_label.config(
            text=f"Total: {total}  |  Covered: {covered}  |  Missing: {missing}  |  Overlaps: {n_ov}  |  Issues: {n_iss}"
        )

        # Canvas scroll region
        max_x = LEFT + 31 * CELL + 20
        max_y = row_y + 10
        self.cov_canvas.configure(scrollregion=(0, 0, max_x, max_y))

    def _canvas_date_at(self, cx, cy):
        """Return date string for canvas coordinates in O(1) using coord grid map."""
        col_i = int((cx - LBL_W) // CELL)
        # Each month row is CELL+2 pixels tall; TOP=30 is the first row offset
        row_i = int((cy - 30) // (CELL + 2))
        if col_i < 0 or row_i < 0:
            return None
        return self._cov_coord_map.get((col_i, row_i))

    def _on_cov_motion(self, event):
        cx = self.cov_canvas.canvasx(event.x)
        cy = self.cov_canvas.canvasy(event.y)
        ds = self._canvas_date_at(cx, cy)
        if ds:
            # Use cached data — avoids rebuilding merged on every mouse-move pixel
            merged, sources, overlaps = self._cov_cache
            issues = self._cov_issues
            parts = [ds]
            if ds in merged:
                parts.append(f"from: {sources[ds]}")
                if ds in overlaps:
                    parts.append(f"⚠ overlap ({len(overlaps[ds])} files)")
                if ds in issues:
                    parts.append(f"⚠ {'; '.join(issues[ds][:2])}")
            else:
                parts.append("MISSING ✗")
            self.cov_tooltip.config(text="  ".join(parts))
            self.cov_tooltip.place(x=event.x + 14, y=event.y - 4)
        else:
            self.cov_tooltip.place_forget()

    def _on_cov_click(self, event):
        cx = self.cov_canvas.canvasx(event.x)
        cy = self.cov_canvas.canvasy(event.y)
        ds = self._canvas_date_at(cx, cy)
        if ds:
            self._jump_to_review(ds)

    def _jump_to_review(self, date_str: str):
        self.nb.select(2)   # switch to Entry Review tab
        for item in self.review_tree.get_children():
            if self.review_tree.item(item, "values")[1] == date_str:
                self.review_tree.selection_set(item)
                self.review_tree.see(item)
                self._on_review_select(None)
                break

    # ── Entry Review tab ─────────────────────────────────────────────────────

    _sort_col = ""
    _sort_rev = False

    def _sort_review(self, col: str):
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False
        self._refresh_review()

    def _refresh_review(self):
        for row in self.review_tree.get_children():
            self.review_tree.delete(row)

        merged, sources, overlaps = self._build_merged()
        all_dates = self._all_dates_in_range()
        issues    = self._all_issues()

        if not all_dates:
            self.review_count.set("0 entries")
            return

        filter_text  = self.filter_var.get().lower()
        only_issues  = self.show_issues_var.get()
        only_missing = self.show_missing_var.get()

        rows_data = []

        for idx, ds in enumerate(all_dates, 1):
            is_miss = ds not in merged
            is_ov   = ds in overlaps
            has_iss = ds in issues

            if only_missing and not is_miss:
                continue
            if only_issues and not (has_iss or is_miss):
                continue

            if is_miss:
                tag   = "missing"
                src   = "—"
                eid   = "—"
                versi = "❌ MISSING"
                rlen  = "—"
                iss_s = "❌ missing"
            else:
                entry = merged[ds]
                src   = sources.get(ds, "?")
                eid   = (entry.get("id", "") or "")[:45]
                versi = (entry.get("versiculo", "") or "")[:70]
                rlen  = str(len(entry.get("reflexion", "") or ""))

                if is_ov:
                    tag   = "overlap"
                    iss_s = f"⚠ overlap ({len(overlaps[ds])} files)"
                elif has_iss:
                    tag   = "warn"
                    iss_s = "; ".join(issues[ds][:2])
                else:
                    tag   = "ok"
                    iss_s = "✅"

            if filter_text:
                search_str = f"{ds} {src} {eid} {versi} {iss_s}".lower()
                if filter_text not in search_str:
                    continue

            rows_data.append((idx, ds, src, eid, versi, rlen, iss_s, tag))

        # Sorting
        COL_IDX = {
            "#": 0, "Date": 1, "Source File": 2, "ID": 3,
            "Versiculo (preview)": 4, "Len(R)": 5, "Issues": 6,
        }
        if self._sort_col in COL_IDX:
            ci = COL_IDX[self._sort_col]
            try:
                rows_data.sort(key=lambda r: int(r[ci]) if ci == 5 else r[ci],
                               reverse=self._sort_rev)
            except (ValueError, TypeError):
                rows_data.sort(key=lambda r: str(r[ci]), reverse=self._sort_rev)

        for row in rows_data:
            self.review_tree.insert("", "end", tags=(row[7],),
                                    values=row[:7])

        covered = sum(1 for d in all_dates if d in merged)
        self.review_count.set(
            f"Showing {len(rows_data)} / {len(all_dates)}   Covered: {covered}/{len(all_dates)}"
        )

    def _on_review_select(self, _event):
        sel = self.review_tree.selection()
        if not sel:
            return
        vals = self.review_tree.item(sel[0], "values")
        if len(vals) < 2:
            return
        date_str = vals[1]

        merged, sources, overlaps = self._build_merged()
        iss = self._all_issues()

        self.detail_text.config(state="normal")
        self.detail_text.delete("1.0", "end")

        if date_str not in merged:
            self.detail_text.insert("end",
                f"❌  {date_str}  —  NOT COVERED by any active file.\n\n"
                "Tip: load the missing partial file or disable non-overlapping ones.\n")
        else:
            entry   = merged[date_str]
            src     = sources.get(date_str, "?")
            my_iss  = iss.get(date_str, [])
            ov      = overlaps.get(date_str, [])

            r = entry.get('reflexion', '') or ''
            o = entry.get('oracion', '')   or ''

            lines = [
                f"Date: {date_str}    Source: {src}",
                f"ID: {entry.get('id', '?')}",
                f"Language: {entry.get('language', '?')}  |  Version: {entry.get('version', '?')}",
                "",
                f"Versiculo:",
                f"  {entry.get('versiculo', '')}",
                "",
                f"Reflexion  [{len(r)} chars]:",
                f"  {r[:600]}{'…' if len(r) > 600 else ''}",
                "",
                f"Oracion  [{len(o)} chars]:",
                f"  {o[:300]}{'…' if len(o) > 300 else ''}",
                "",
                f"Tags: {entry.get('tags', [])}",
            ]
            if my_iss:
                lines += ["", f"⚠  Issues ({len(my_iss)}):"]
                lines += [f"   • {i}" for i in my_iss]
            if ov:
                lines += ["", f"⚠  Overlap: date present in {len(ov)} files:"]
                lines += [f"   • {f}" for f in ov]
                lines.append(f"   → Using entry from: {src}")

            self.detail_text.insert("end", "\n".join(lines))

        self.detail_text.config(state="disabled")

    # ── Lint tab ─────────────────────────────────────────────────────────────

    def _run_lint(self):
        self.lint_text.config(state="normal")
        self.lint_text.delete("1.0", "end")

        lines = []

        if not self.partial_files:
            lines = ["No files loaded.  Use '📂 Scan Folder' or '➕ Add Files' to begin."]
            self.lint_text.insert("end", "\n".join(lines))
            self.lint_text.config(state="disabled")
            self.lint_summary_var.set("")
            return

        merged, sources, overlaps = self._build_merged()
        all_dates = self._all_dates_in_range()
        total_issues = 0

        for pf in self.partial_files:
            enabled_str = "☑ ENABLED" if pf.enabled else "☐ DISABLED"
            lines.append("=" * 64)
            lines.append(f"📄  {pf.name}   [{enabled_str}]")
            lines.append("=" * 64)

            if pf.errors:
                for e in pf.errors:
                    lines.append(f"  ❌ Load Error: {e}")
                lines.append("")
                continue

            dr = pf.date_range
            lines.append(
                f"  ✅ Loaded OK  |  Entries: {pf.entry_count}  |  "
                f"Range: {dr[0]} → {dr[1]}"
            )
            lines.append(f"  Lang: {pf.lang}  |  Version: {pf.version}  |  Year: {pf.year}")

            if pf.issue_count == 0:
                lines.append(f"  ✅ Content quality: all {pf.entry_count} entries pass")
            else:
                lines.append(f"  ⚠️  Content issues: {pf.issue_count} entries")
                shown = 0
                for dk in sorted(pf.entry_issues):
                    if shown >= 25:
                        lines.append(f"      … and {pf.issue_count - shown} more")
                        break
                    lines.append(f"    {dk}: {'; '.join(pf.entry_issues[dk][:3])}")
                    shown += 1

            total_issues += pf.issue_count

            if pf.enabled:
                ov_dates = {d for d in pf.entries if d in overlaps}
                if ov_dates:
                    lines.append(f"  ⚠️  Overlapping dates with other active files: {len(ov_dates)}")
                    for d in sorted(ov_dates)[:5]:
                        others = [f for f in overlaps[d] if f != pf.name]
                        lines.append(f"    {d}  ← also in: {', '.join(others)}")
                    if len(ov_dates) > 5:
                        lines.append(f"    … and {len(ov_dates)-5} more")
                else:
                    lines.append(f"  ✅ No overlaps with other active files")

            lines.append("")

        # Summary block
        if all_dates:
            missing_dates  = [d for d in all_dates if d not in merged]
            overlap_dates  = list(overlaps.keys())
            cov_count      = len(merged)
            total_expected = len(all_dates)

            lines += [
                "=" * 64,
                "📊  MERGE SUMMARY",
                "=" * 64,
                f"  Range   : {all_dates[0]} → {all_dates[-1]}  ({total_expected} days)",
                f"  Active  : {len(self._active_files())} file(s)",
                f"  Covered : {cov_count} / {total_expected}",
                f"  Missing : {len(missing_dates)}",
                f"  Overlaps: {len(overlap_dates)}",
                f"  Issues  : {total_issues}",
            ]

            if missing_dates:
                lines.append(f"\n  ❌  MISSING DATES ({len(missing_dates)}):")
                for i, d in enumerate(missing_dates):
                    if i >= 40:
                        lines.append(f"     … and {len(missing_dates)-40} more")
                        break
                    lines.append(f"     {d}")
            else:
                lines.append(f"\n  ✅  All {total_expected} dates are covered — ready to merge!")

            if overlap_dates:
                lines.append(f"\n  ⚠️   OVERLAPPING DATES ({len(overlap_dates)}) — last file wins:")
                for i, d in enumerate(sorted(overlap_dates)[:10]):
                    lines.append(f"     {d}: {', '.join(overlaps[d])}")
                if len(overlap_dates) > 10:
                    lines.append(f"     … and {len(overlap_dates)-10} more")

            summary_str = (
                f"{cov_count}/{total_expected} covered  |  "
                f"{len(missing_dates)} missing  |  "
                f"{total_issues} issues"
            )
            self.lint_summary_var.set(summary_str)
        else:
            self.lint_summary_var.set("Load files to see summary")

        self.lint_text.insert("end", "\n".join(lines))
        self.lint_text.config(state="disabled")

    # ══════════════════════════════════════════ Merge & Export ═══════════════

    def _do_merge(self):
        active = self._active_files()
        if not active:
            messagebox.showwarning("No Files",
                                   "No valid, enabled partial files are loaded.\n"
                                   "Use '📂 Scan Folder' or '➕ Add Files' first.")
            return

        merged, sources, overlaps = self._build_merged()
        if not merged:
            messagebox.showwarning("Empty", "No entries found in active files.")
            return

        # ── Warnings before export ──────────────────────────────────────────
        all_dates = self._all_dates_in_range()
        missing   = [d for d in all_dates if d not in merged]
        ov_count  = len(overlaps)
        iss_count = sum(pf.issue_count for pf in active)

        warns = []
        if missing:
            warns.append(f"• {len(missing)} date(s) are missing from the merged output.")
        if ov_count:
            warns.append(f"• {ov_count} overlapping date(s) (last file in list wins).")
        if iss_count:
            warns.append(f"• {iss_count} content quality issue(s) across entries.")

        if warns:
            msg = ("Merge warnings:\n\n" + "\n".join(warns) +
                   "\n\nProceed with export anyway?")
            if not messagebox.askyesno("Warnings", msg):
                return

        # ── Determine output path ────────────────────────────────────────────
        ref_pf   = active[0]
        out_dir  = ref_pf.path.parent
        out_name = (f"Devocional_year_{ref_pf.year}_"
                    f"{ref_pf.lang}_{ref_pf.version}.json")
        out_path = out_dir / out_name

        if out_path.exists():
            if messagebox.askyesno("File Exists",
                                   f"Output file already exists:\n{out_path}\n\nOverwrite?"):
                pass   # proceed overwriting
            else:
                out_path = filedialog.asksaveasfilename(
                    title="Save merged devotional JSON",
                    initialdir=str(out_dir),
                    initialfile=out_name,
                    defaultextension=".json",
                    filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                )
                if not out_path:
                    return
                out_path = Path(out_path)

        # ── Build output structure ───────────────────────────────────────────
        lang         = ref_pf.lang
        sorted_dates = sorted(merged.keys())
        output       = {"data": {lang: {}}}
        for dk in sorted_dates:
            output["data"][lang][dk] = [merged[dk]]

        # ── Write ────────────────────────────────────────────────────────────
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(output, fh, ensure_ascii=False, indent=2)
        except OSError as ex:
            messagebox.showerror("Export Error", f"Failed to write file:\n{ex}")
            return

        n = len(sorted_dates)
        total = len(all_dates)
        messagebox.showinfo(
            "Export Complete",
            f"✅  Merged {n} entries → {out_path.name}\n\n"
            f"Coverage : {n} / {total} date(s)\n"
            f"Files merged : {len(active)}\n"
            f"Saved in : {out_path.parent}",
        )
        self.status_var.set(f"✅ Exported: {out_path.name}  ({n} entries)")


# ═══════════════════════════════════════════════════════════════════════════

def main():
    app = MergeApp()
    app.mainloop()


if __name__ == "__main__":
    main()

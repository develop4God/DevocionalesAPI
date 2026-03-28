"""
inject_bible_version.py
───────────────────────
Creates a new devotional file for a different Bible version by:
  1. Loading an existing COMPLETE devotional file (e.g. LU17 2025/2026).
  2. Loading a seed file for the TARGET version (e.g. SCH2000).
  3. For every date in the seed:
       - Reuse reflexion + oracion from the source devotional (no Gemini needed).
       - Replace versiculo text with the seed's translation.
       - Replace para_meditar texts with the seed's translation.
       - Rebuild the ID with the new version code.
  4. Saves a ready-to-use complete devotional JSON.

No API server required — pure offline data injection.

Usage:
  python inject_bible_version.py
  (file pickers guide you through the 3 inputs + output folder)
"""

import json
import os
import re
import sys
from datetime import datetime
from tkinter import Tk, filedialog, messagebox, simpledialog


# ─────────────────────────────────────────────────────────────────────────────
# ID BUILDER  (mirrors DevotionalBuilder._build_id logic)
# ─────────────────────────────────────────────────────────────────────────────

def build_id(cita: str, version: str, date: str) -> str:
    id_part      = re.sub(r"\s+", "", cita).replace(":", "")
    date_compact = date.replace("-", "")
    return id_part + version + date_compact


# ─────────────────────────────────────────────────────────────────────────────
# VERSICULO STRING BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_versiculo(cita: str, texto: str, version: str) -> str:
    return f'{cita} {version}: "{texto}"'


# ─────────────────────────────────────────────────────────────────────────────
# CORE INJECTION
# ─────────────────────────────────────────────────────────────────────────────

def inject(source_path: str, seed_path: str, new_version: str, output_dir: str) -> str:
    """
    Merges source devotional content with seed Bible texts.

    Returns the output file path.
    """

    print("\n" + "=" * 60)
    print("BIBLE VERSION INJECTOR")
    print("=" * 60)
    print(f"  Source devotional : {source_path}")
    print(f"  Seed              : {seed_path}")
    print(f"  New version       : {new_version}")
    print(f"  Output dir        : {output_dir}")
    print("=" * 60 + "\n")

    # ── Load source ──────────────────────────────────────────────────────────
    with open(source_path, encoding="utf-8") as f:
        source_root = json.load(f)

    # Source must have structure: {"data": {"<lang>": {"date": [devotional]}}}
    source_data = source_root.get("data", {})
    if not source_data:
        raise ValueError("Source file has no 'data' key — expected complete devotional format.")

    # Determine language from the one key inside data
    lang_keys = list(source_data.keys())
    if len(lang_keys) != 1:
        raise ValueError(f"Expected exactly one language key in source, found: {lang_keys}")
    lang = lang_keys[0]
    source_by_date = source_data[lang]  # {"date": [devotional, ...]}

    # ── Load seed ────────────────────────────────────────────────────────────
    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    seed_dates = sorted(seed.keys())
    total      = len(seed_dates)
    print(f"INFO: {total} seed entries  |  language: {lang}  |  version → {new_version}\n")
    print("-" * 60)

    # ── Process ──────────────────────────────────────────────────────────────
    results     = {}
    skipped     = []
    ok_count    = 0

    for i, date in enumerate(seed_dates, 1):
        seed_entry = seed[date]

        # Find source devotional for this date
        source_entries = source_by_date.get(date)
        if not source_entries:
            print(f"  [{i}/{total}] {date} — SKIP: date not found in source file")
            skipped.append({"date": date, "reason": "date not found in source"})
            continue

        source_devo = source_entries[0]   # take first entry (always one)

        cita  = seed_entry["versiculo"]["cita"]
        texto = seed_entry["versiculo"]["texto"]

        new_devo = {
            "id":           build_id(cita, new_version, date),
            "date":         date,
            "language":     lang,
            "version":      new_version,
            "versiculo":    build_versiculo(cita, texto, new_version),
            "reflexion":    source_devo["reflexion"],
            "para_meditar": seed_entry["para_meditar"],
            "oracion":      source_devo["oracion"],
            "tags":         seed_entry.get("tags") or source_devo.get("tags", []),
        }

        results[date] = new_devo
        ok_count += 1
        print(f"  [{i}/{total}] {date}  {cita:<28}  OK")

    # ── Save output ──────────────────────────────────────────────────────────
    print("\n" + "-" * 60)

    if not results:
        print("No devotionals produced — nothing to save.")
        return ""

    # Determine year range for filename
    first_year = seed_dates[0][:4]
    last_year  = seed_dates[-1][:4]
    year_label = first_year if first_year == last_year else f"{first_year}-{last_year}"

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Devocional_year_{year_label}_{lang}_{new_version}.json"
    out_path = os.path.join(output_dir, filename)

    nested_output = {
        "data": {
            lang: {
                date: [devo] for date, devo in sorted(results.items())
            }
        }
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(nested_output, f, ensure_ascii=False, indent=2)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\nOutput  → {out_path}")

    if skipped:
        skip_path = os.path.join(output_dir, f"inject_skipped_{lang}_{new_version}_{ts}.json")
        with open(skip_path, "w", encoding="utf-8") as f:
            json.dump(skipped, f, ensure_ascii=False, indent=2)
        print(f"Skipped → {skip_path}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  Seed entries  : {total}")
    print(f"  Injected OK   : {ok_count}")
    print(f"  Skipped       : {len(skipped)}")
    print("=" * 60 + "\n")

    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    root = Tk()
    root.withdraw()

    # 1 ── Source complete devotional file ────────────────────────────────────
    messagebox.showinfo(
        "Version Injector  1/4",
        "Select the SOURCE complete devotional file\n"
        "(e.g. Devocional_year_2025_de_LU17.json)"
    )
    source_path = filedialog.askopenfilename(
        title="Select source devotional JSON",
        filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
    )
    if not source_path:
        sys.exit(0)

    # 2 ── Seed file for the new version ──────────────────────────────────────
    messagebox.showinfo(
        "Version Injector  2/4",
        "Select the SEED file for the new Bible version\n"
        "(e.g. seed_de_SCH2000_for_2025.json)"
    )
    seed_path = filedialog.askopenfilename(
        title="Select seed JSON for new version",
        filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        initialdir=os.path.join(os.path.dirname(source_path)),
    )
    if not seed_path:
        sys.exit(0)

    # 3 ── New version code ────────────────────────────────────────────────────
    new_version = simpledialog.askstring(
        "Version code  3/4",
        "Enter the new Bible version code (e.g. SCH2000, KJV, RV1960):",
        initialvalue="SCH2000",
    )
    if not new_version:
        sys.exit(0)
    new_version = new_version.strip()

    # 4 ── Output directory ────────────────────────────────────────────────────
    messagebox.showinfo(
        "Version Injector  4/4",
        "Select the OUTPUT folder where the new file will be saved."
    )
    output_dir = filedialog.askdirectory(title="Select output folder")
    if not output_dir:
        sys.exit(0)

    root.destroy()

    # ── Run ───────────────────────────────────────────────────────────────────
    try:
        out = inject(source_path, seed_path, new_version, output_dir)
        print(f"\nDone!  File saved to:\n  {out}\n")
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

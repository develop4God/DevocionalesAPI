"""
extract_verse_pool.py
──────────────────────
Pulls the 730 verse citations (365 from 2025 + 365 from 2026) out of the
local English KJV devotional files, dedupes them, and writes a flat list
of unique citation strings — meant as raw input for picking/curating the
2027 verse list.

Usage:
  python extract_verse_pool.py \\
      --2025 /home/develop4god/Projects/devocionales-json/Devocional_year_2025_en_KJV.json \\
      --2026 /home/develop4god/Projects/devocionales-json/Devocional_year_2026_en_KJV.json \\
      --out verse_pool_2025_2026.json
"""

import argparse
import json


def extract_citations(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    lang = list(data["data"].keys())[0]
    dates = data["data"][lang]

    citations = []
    for date_key in sorted(dates):
        versiculo = dates[date_key][0]["versiculo"]
        cita = versiculo.split(" KJV:")[0].strip()
        citations.append(cita)
    return citations


def main():
    parser = argparse.ArgumentParser(description="Extract 730 verse citations from 2025+2026 KJV files")
    parser.add_argument("--2025", dest="path_2025", required=True, help="Path to Devocional_year_2025_en_KJV.json")
    parser.add_argument("--2026", dest="path_2026", required=True, help="Path to Devocional_year_2026_en_KJV.json")
    parser.add_argument("--out", default="verse_pool_2025_2026.json", help="Output file path")
    args = parser.parse_args()

    pool = extract_citations(args.path_2025) + extract_citations(args.path_2026)

    seen = set()
    unique_pool = []
    dropped = 0
    for cita in pool:
        if cita in seen:
            dropped += 1
            continue
        seen.add(cita)
        unique_pool.append(cita)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(unique_pool, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(unique_pool)} unique citations -> {args.out} ({dropped} duplicates dropped)")


if __name__ == "__main__":
    main()

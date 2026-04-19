"""
source.py — GEP Critic v3
Single responsibility: fetch and parse devotional source data.
Supports GitHub remote and local file.
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

from models import DevotionalEntry

GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/develop4God/devocionales-json"
    "/refs/heads/main"
)

KNOWN_FILES = {
    ("pt", "NVI"):       "Devocional_year_{year}_pt_NVI.json",
    ("pt", "ARC"):       "Devocional_year_{year}_pt_ARC.json",
    ("en", "KJV"):       "Devocional_year_{year}_en_KJV.json",
    ("en", "NIV"):       "Devocional_year_{year}_en_NIV.json",
    ("es", "NVI"):       "Devocional_year_{year}_es_NVI.json",
    ("fr", "LSG1910"):   "Devocional_year_{year}_fr_LSG1910.json",
    ("fr", "TOB"):       "Devocional_year_{year}_fr_TOB.json",
    ("de", "LU17"):      "Devocional_year_{year}_de_LU17.json",
    ("de", "SCH2000"):   "Devocional_year_{year}_de_SCH2000.json",
    ("hi", "HERV"):      "Devocional_year_{year}_hi_HERV.json",
    ("hi", "HIOV"):      "Devocional_year_{year}_hi_HIOV.json",
    ("ar", "NAV"):       "Devocional_year_{year}_ar_NAV.json",
    ("ar", "SVDA"):      "Devocional_year_{year}_ar_SVDA.json",
    ("tl", "ADB"):       "Devocional_year_{year}_tl_ADB.json",
    ("tl", "ASND"):      "Devocional_year_{year}_tl_ASND.json",
    ("ja", "リビングバイブル"): "Devocional_year_{year}_ja_リビングバイブル.json",
    ("ja", "新改訳2003"):    "Devocional_year_{year}_ja_新改訳2003.json",
    ("zh", "和合本1919"):    "Devocional_year_{year}_zh_和合本1919.json",
    ("zh", "新译本"):        "Devocional_year_{year}_zh_新译本.json",
}


def build_url(lang: str, version: str, year: int) -> str:
    key = (lang, version)
    filename = KNOWN_FILES.get(key, f"Devocional_year_{{year}}_{lang}_{version}.json")
    return f"{GITHUB_RAW_BASE}/{filename.format(year=year)}"


def fetch_remote(lang: str, version: str, year: int) -> dict:
    url = build_url(lang, version, year)
    print(f"  📡 Fetching: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "gep-critic/3.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  ❌ HTTP {e.code}: {url}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"  ❌ Network error: {e.reason}")
        sys.exit(1)


def load_local(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_entries(data: dict, lang: str) -> list[DevotionalEntry]:
    try:
        date_map = data["data"][lang]
    except KeyError:
        print(f"  ❌ Could not find data.{lang} in source JSON")
        sys.exit(1)

    entries = []
    for date_key, date_value in date_map.items():
        items = date_value if isinstance(date_value, list) else [date_value]
        for item in items:
            if not isinstance(item, dict):
                continue
            entries.append(DevotionalEntry(
                date=date_key,
                id=item.get("id", ""),
                language=item.get("language", lang),
                version=item.get("version", ""),
                versiculo=item.get("versiculo", ""),
                reflexion=item.get("reflexion", ""),
                oracion=item.get("oracion", ""),
                para_meditar=item.get("para_meditar", []),
                tags=item.get("tags", []),
            ))

    entries.sort(key=lambda e: e.date)
    return entries


def list_known_files():
    print("\n  Available lang/version combinations:\n")
    print(f"  {'LANG':<6} {'VERSION':<18} FILENAME PATTERN")
    print(f"  {'─'*6} {'─'*18} {'─'*40}")
    for (lang, version), pattern in sorted(KNOWN_FILES.items()):
        print(f"  {lang:<6} {version:<18} {pattern}")
    print()

def get_sample_entries():
    """Returns 5 stub DevotionalEntry objects for pipeline testing. No network needed."""
    entries = []
    stubs = [
        ("2025-01-01", "es_NVI_2025-01-01", "Porque de tal manera amó Dios al mundo.",
         "Dios nos ama profundamente y desea lo mejor para cada uno de nosotros.",
         "Señor, gracias por tu amor incondicional que nos sostiene cada día."),
        ("2025-01-02", "es_NVI_2025-01-02", "El Señor es mi pastor, nada me faltará.",
         "Cuando confiamos en el Señor él provee todo lo que necesitamos en la vida.",
         "Padre, ayúdame a confiar en tu provisión y no en mis propias fuerzas."),
        ("2025-01-03", "es_NVI_2025-01-03", "Todo lo puedo en Cristo que me fortalece.",
         "La fuerza no viene de nosotros mismos sino del Cristo que vive en nosotros nosotros.",
         "Señor, dame tu fuerza para enfrentar los retos de hoy con fe y esperanza."),
        ("2025-01-04", "es_NVI_2025-01-04", "La fe es la certeza de lo que se espera.",
         "La fe activa nos permite ver más allá de las circunstancias actuales con esperanza.",
         "Dios mío, aumenta mi fe para caminar contigo en los momentos difíciles."),
        ("2025-01-05", "es_NVI_2025-01-05", "Encomienda al Señor tu camino y confía en él.",
         "Dios tiene un plan perfecto para nuestra vida mucho mejor que nuestros propios planes.",
         "Padre, entrego mi camino a tus manos sabiendo que tú diriges mis pasos."),
    ]
    for date, id_, vers, refl, orac in stubs:
        entries.append(DevotionalEntry(
            date=date, id=id_, language="es", version="NVI",
            versiculo=vers, reflexion=refl, oracion=orac,
            para_meditar=[], tags=[],
        ))
    return entries

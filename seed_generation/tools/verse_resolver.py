"""
verse_resolver.py
─────────────────
Agnostic verse resolver. Resolves English Bible references to
target-language citations and verse text from a SQLite Bible DB.

Book lookup uses the bible_books.json SOT (github.com/develop4God/bible_versions)
to confirm the EN book name and get its book_number. The native book name is
then read directly from the DB's own `books` table — no manual per-language
mapping file is required.

Reusable by any pipeline script — no content-type assumptions.

Usage:
    from verse_resolver import VerseResolver

    # SQLite path is the only required argument
    with VerseResolver("path/to/bible.db") as r:
        cita, texto, error = r.resolve("1 Corinthians 13:4-7")
    # On success : ("1 Korinther 13:4-7", "Die Liebe ist...", None)
    # On failure : (None, None, "reason string")

    # Optional: supply a local bible_books.json to avoid a network fetch
    with VerseResolver("path/to/bible.db", books_sot_path="devocionales_scripts/bible_books.json") as r:
        cita, texto, error = r.resolve("John 3:16")
"""

import gzip
import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.request
from typing import Self

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

BOOKS_SOT_URL = (
    "https://raw.githubusercontent.com/develop4god/bible_versions"
    "/refs/heads/main/bible_books.json"
)

# Devanagari digit → ASCII digit (for Hindi references)
_DEVA = str.maketrans("०१२३४५६७८९", "0123456789")

# HIOV_hi.SQLite3's `books.long_name` stores every book in liturgical long
# form (e.g. "लूका रचित सुसमाचार" = "the Gospel composed by Luke",
# "रोमियों के नाम पौलुस प्रेरित की पत्री" = "the letter of Paul the apostle
# to the Romans") instead of the short form real Hindi Bibles use in
# citations. This mapping is applied by long_name value, not by filename —
# see _native_book_name — so it's safe regardless of what the DB file is
# named or how it was copied/symlinked. Covers all 66 books.
_HIOV_LONG_TO_SHORT = {
    "उत्पत्ति": "उत्पत्ति",
    "निर्गमन": "निर्गमन",
    "लैव्यव्यवस्था": "लैव्यव्यवस्था",
    "गिनती": "गिनती",
    "व्यवस्थाविवरण": "व्यवस्थाविवरण",
    "यहोशू": "यहोशू",
    "न्यायियों": "न्यायियों",
    "रूत": "रूत",
    "शमूएल की पहली पुस्तक": "1 शमूएल",
    "शमूएल की दूसरी पुस्तक": "2 शमूएल",
    "राजाओं का वृत्तान्त – पहला भाग": "1 राजाओं",
    "राजाओं का वृत्तान्त – दूसरा भाग": "2 राजाओं",
    "इतिहास नामक पुस्तक – पहला भाग": "1 इतिहास",
    "इतिहास नामक पुस्तक – दूसरा भाग": "2 इतिहास",
    "एज्रा नामक पुस्तक": "एज्रा",
    "नहेम्याह नामक पुस्तक": "नहेम्याह",
    "एस्तेर": "एस्तेर",
    "अय्यूब": "अय्यूब",
    "भजन संहिता": "भजन संहिता",
    "नीतिवचन": "नीतिवचन",
    "सभोपदेशक": "सभोपदेशक",
    "श्रेष्‍ठगीत": "श्रेष्ठगीत",
    "यशायाह भविष्यद्वक्‍ता की पुस्तक": "यशायाह",
    "यिर्मयाह नामक पुस्तक": "यिर्मयाह",
    "विलापगीत": "विलापगीत",
    "यहेजकेल नामक पुस्तक": "यहेजकेल",
    "दानिय्येल नामक पुस्तक": "दानिय्येल",
    "होशे": "होशे",
    "योएल": "योएल",
    "आमोस": "आमोस",
    "ओबद्याह": "ओबद्याह",
    "योना": "योना",
    "मीका": "मीका",
    "नहूम": "नहूम",
    "हबक्‍कूक": "हबक्कूक",
    "सपन्याह": "सपन्याह",
    "हाग्गै": "हाग्गै",
    "जकर्याह": "जकर्याह",
    "मलाकी": "मलाकी",
    "मत्ती रचित सुसमाचार": "मत्ती",
    "मरकुस रचित सुसमाचार": "मरकुस",
    "लूका रचित सुसमाचार": "लूका",
    "यूहन्ना रचित सुसमाचार": "यूहन्ना",
    "प्रेरितों के कामों का वर्णन": "प्रेरितों के काम",
    "रोमियों के नाम पौलुस प्रेरित की पत्री": "रोमियों",
    "कुरिन्थियों के नाम पौलुस प्रेरित की पहली पत्री": "1 कुरिन्थियों",
    "कुरिन्थियों के नाम पौलुस प्रेरित की दूसरी पत्री": "2 कुरिन्थियों",
    "गलातियों के नाम पौलुस प्रेरित की पत्री": "गलातियों",
    "इफिसियों के नाम पौलुस प्रेरित की पत्री": "इफिसियों",
    "फिलिप्पियों के नाम पौलुस प्रेरित की पत्री": "फिलिप्पियों",
    "कुलुस्सियों के नाम पौलुस प्रेरित की पत्री": "कुलुस्सियों",
    "थिस्सलुनीकियों के नाम पौलुस प्रेरित की पहली पत्री": "1 थिस्सलुनीकियों",
    "थिस्सलुनीकियों के नाम पौलुस प्रेरित की दूसरी पत्री": "2 थिस्सलुनीकियों",
    "तीमुथियुस के नाम पौलुस प्रेरित की पहली पत्री": "1 तीमुथियुस",
    "तीमुथियुस के नाम पौलुस प्रेरित की दूसरी पत्री": "2 तीमुथियुस",
    "तीतुस के नाम पौलुस प्रेरित की पत्री": "तीतुस",
    "फिलेमोन के नाम पौलुस प्रेरित की पत्री": "फिलेमोन",
    "इब्रानियों के नाम पत्री": "इब्रानियों",
    "याकूब की पत्री": "याकूब",
    "पतरस की पहली पत्री": "1 पतरस",
    "पतरस की दूसरी पत्री": "2 पतरस",
    "यूहन्ना की पहली पत्री": "1 यूहन्ना",
    "यूहन्ना की दूसरी पत्री": "2 यूहन्ना",
    "यूहन्ना की तीसरी पत्री": "3 यूहन्ना",
    "यहूदा की पत्री": "यहूदा",
    "यूहन्ना का प्रकाशितवाक्य": "प्रकाशितवाक्य",
}

# LU17_de.SQLite3's `books.long_name` stores every book in full liturgical
# form (e.g. "Das Evangelium nach Johannes", "Der Brief des Paulus an die
# Römer") instead of the short form real German citations use ("Johannes",
# "Römer"). Applied by long_name value — see _native_book_name — so it's
# safe regardless of what the DB file is named or how it was copied.
_LU17_LONG_TO_SHORT = {
    "Das erste Buch Mose (Genesis)": "1. Mose",
    "Das zweite Buch Mose (Exodus)": "2. Mose",
    "Das dritte Buch Mose (Levitikus)": "3. Mose",
    "Das vierte Buch Mose (Numeri)": "4. Mose",
    "Das fünfte Buch Mose (Deuteronomium)": "5. Mose",
    "Das Buch Josua": "Josua",
    "Das Buch der Richter": "Richter",
    "Das Buch Rut": "Rut",
    "Das erste Buch Samuel": "1. Samuel",
    "Das zweite Buch Samuel": "2. Samuel",
    "Das erste Buch der Könige": "1. Könige",
    "Das zweite Buch der Könige": "2. Könige",
    "Das erste Buch der Chronik": "1. Chronik",
    "Das zweite Buch der Chronik": "2. Chronik",
    "Das Buch Esra": "Esra",
    "Das Buch Nehemia": "Nehemia",
    "Das Buch Tobias (Tobit)": "Tobias",
    "Das Buch Judit": "Judit",
    "Das Buch Ester": "Ester",
    "Stücke zum Buch Ester": "Stücke zu Ester",
    "Das Buch Hiob (Ijob)": "Hiob",
    "Der Psalter": "Psalm",
    "Die Sprüche Salomos (Proverbia)": "Sprüche",
    "Der Prediger Salomo (Kohelet)": "Prediger",
    "Das Hohelied Salomos": "Hoheslied",
    "Die Weisheit Salomos": "Weisheit",
    "Das Buch Jesus Sirach": "Sirach",
    "Der Prophet Jesaja": "Jesaja",
    "Der Prophet Jeremia": "Jeremia",
    "Die Klagelieder Jeremias": "Klagelieder",
    "Das Buch Baruch": "Baruch",
    "Der Prophet Hesekiel (Ezechiel)": "Hesekiel",
    "Das Buch Daniel": "Daniel",
    "Stücke zum Buch Daniel": "Stücke zu Daniel",
    "Der Prophet Hosea": "Hosea",
    "Der Prophet Joel": "Joel",
    "Der Prophet Amos": "Amos",
    "Der Prophet Obadja": "Obadja",
    "Der Prophet Jona": "Jona",
    "Der Prophet Micha": "Micha",
    "Der Prophet Nahum": "Nahum",
    "Der Prophet Habakuk": "Habakuk",
    "Der Prophet Zefanja": "Zefanja",
    "Der Prophet Haggai": "Haggai",
    "Der Prophet Sacharja": "Sacharja",
    "Der Prophet Maleachi": "Maleachi",
    "Das erste Buch der Makkabäer": "1. Makkabäer",
    "Das zweite Buch der Makkabäer": "2. Makkabäer",
    "Das Evangelium nach Matthäus": "Matthäus",
    "Das Evangelium nach Markus": "Markus",
    "Das Evangelium nach Lukas": "Lukas",
    "Das Evangelium nach Johannes": "Johannes",
    "Die Apostelgeschichte des Lukas": "Apostelgeschichte",
    "Der Brief des Paulus an die Römer": "Römer",
    "Der erste Brief des Paulus an die Korinther": "1. Korinther",
    "Der zweite Brief des Paulus an die Korinther": "2. Korinther",
    "Der Brief des Paulus an die Galater": "Galater",
    "Der Brief des Paulus an die Epheser": "Epheser",
    "Der Brief des Paulus an die Philipper": "Philipper",
    "Der Brief des Paulus an die Kolosser": "Kolosser",
    "Der erste Brief des Paulus an die Thessalonicher": "1. Thessalonicher",
    "Der zweite Brief des Paulus an die Thessalonicher": "2. Thessalonicher",
    "Der erste Brief des Paulus an Timotheus": "1. Timotheus",
    "Der zweite Brief des Paulus an Timotheus": "2. Timotheus",
    "Der Brief des Paulus an Titus": "Titus",
    "Der Brief des Paulus an Philemon": "Philemon",
    "Der Brief an die Hebräer": "Hebräer",
    "Der Brief des Jakobus": "Jakobus",
    "Der erste Brief des Petrus": "1. Petrus",
    "Der zweite Brief des Petrus": "2. Petrus",
    "Der erste Brief des Johannes": "1. Johannes",
    "Der zweite Brief des Johannes": "2. Johannes",
    "Der dritte Brief des Johannes": "3. Johannes",
    "Der Brief des Judas": "Judas",
    "Die Offenbarung des Johannes": "Offenbarung",
    "Das Gebet Manasses": "Gebet Manasses",
}

# Module-level cache — fetched once per Python process
_books_sot_cache: dict | None = None


# ─────────────────────────────────────────────────────────────────────────────
# LOW-LEVEL HELPERS  (module-level, usable without instantiation)
# ─────────────────────────────────────────────────────────────────────────────


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
        name: entry["book_number"] for name, entry in data["books"].items()
    }
    return _books_sot_cache


def parse_en_ref(cita: str) -> tuple[str, int, int, int] | None:
    """
    Parse an English Bible reference string.

    Accepts:
      - "John 3:16"
      - "1 Corinthians 13:4-7"
      - References with trailing version codes ("John 3:16 KJV")
      - Devanagari digits

    Returns:
      (book_name, chapter, verse_start, verse_end)  on success
      None                                           on failure
    """
    cita = cita.strip().translate(_DEVA)
    cita = re.sub(r"\s+[A-Z0-9]{2,6}$", "", cita).strip()  # strip version code
    m = re.match(
        r"^((?:\d\s+)?[A-Za-z]+(?:\s+[A-Za-z]+)*)\s+(\d+):(\d+)(?:-(\d+))?$",
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
    """
    Fetch and clean verse text from a SQLite Bible DB.

    Expected schema:
      verses(book_number INTEGER, chapter INTEGER, verse INTEGER, text TEXT)

    Returns cleaned combined text, or None if not found.
    """
    cursor.execute(
        "SELECT text FROM verses "
        "WHERE book_number=? AND chapter=? AND verse>=? AND verse<=? "
        "ORDER BY verse",
        (book_number, chapter, v_start, v_end),
    )
    rows = cursor.fetchall()
    if not rows or any(r[0] is None for r in rows):
        return None  # missing row, or a NULL text cell (real gap in some DBs) — same as "not found"
    combined = " ".join(r[0] for r in rows)
    # <f>...</f> (footnote-marker number, e.g. "<f>[3]</f>") and <n>...</n>
    # (translator's note, e.g. "<n>〔注：或作...〕</n>") wrap editorial
    # apparatus, not verse text — must drop the whole element including its
    # content (some CUV1919/zh rows contain 1000+ of these). Every other
    # tag (<pb/>, <J>...</J> direct-speech, <i>...</i> supplied words) wraps
    # real verse text, so only its markup is stripped by the generic pass
    # below, not its content.
    combined = re.sub(r"<f>.*?</f>", "", combined)
    combined = re.sub(r"<n>.*?</n>", "", combined)
    combined = re.sub(r"<S>.*?</S>", "", combined)  # Strong's number, e.g. "<S>25</S>"
    combined = re.sub(r"<[^>]+>", "", combined)  # strip remaining XML tags
    combined = re.sub(r"[①-⓿]", "", combined)  # strip Unicode ref markers
    combined = re.sub(r"\s+", " ", combined).strip()
    return combined


# ─────────────────────────────────────────────────────────────────────────────
# VERSE RESOLVER CLASS
# ─────────────────────────────────────────────────────────────────────────────


class VerseResolver:
    """
    Stateful verse resolver. Holds an open SQLite connection and the
    bible_books.json SOT so callers do not manage them directly.

    Book numbers come from the bible_books.json SOT (EN name → book_number).
    Native book names are read directly from the DB's `books` table, so no
    per-language mapping file is needed.

    Parameters
    ----------
    sqlite_path    : path to SQLite Bible database
    books_sot_path : optional path to a local bible_books.json for offline use;
                     if absent, the SOT is fetched from BOOKS_SOT_URL once and
                     cached for the lifetime of the process.

    Example
    -------
    with VerseResolver("bible.db") as r:
        cita, texto, error = r.resolve("1 Corinthians 13:4-7")
        # cita  → native-language citation, e.g. "1. Korinther 13:4-7"
        # texto → verse text from the DB
    """

    def __init__(
        self,
        sqlite_path: str,
        books_sot_path: str | None = None,
    ) -> None:
        self.books_sot = load_books_sot(books_sot_path)
        self._temp_path = None

        if sqlite_path.endswith(".gz"):
            fd, self._temp_path = tempfile.mkstemp(suffix=".SQLite3")
            os.close(fd)
            with (
                gzip.open(sqlite_path, "rb") as src,
                open(self._temp_path, "wb") as dst,
            ):
                shutil.copyfileobj(src, dst)
            sqlite_path = self._temp_path

        self.conn = sqlite3.connect(sqlite_path)
        self.cursor = self.conn.cursor()

    # ── context manager support ───────────────────────────────────────────────

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        """Close the SQLite connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
            self.cursor = None
        if self._temp_path:
            os.remove(self._temp_path)
            self._temp_path = None

    # ── internal helpers ──────────────────────────────────────────────────────

    def _native_book_name(self, book_number: int, fallback: str) -> str:
        """
        Query the DB's `books` table for the long_name of this book_number.
        Returns fallback (the EN book name) if the table is absent or the
        row is missing — ensuring citation building never fails silently.
        """
        try:
            self.cursor.execute(
                "SELECT long_name FROM books WHERE book_number = ?",
                (book_number,),
            )
            row = self.cursor.fetchone()
            if row and row[0]:
                long_name = row[0]
                if long_name in _HIOV_LONG_TO_SHORT:
                    return _HIOV_LONG_TO_SHORT[long_name]
                if long_name in _LU17_LONG_TO_SHORT:
                    return _LU17_LONG_TO_SHORT[long_name]
                return long_name
        except sqlite3.OperationalError:
            pass  # `books` table absent in some minimal DB builds
        return fallback

    # ── public API ────────────────────────────────────────────────────────────

    def resolve(
        self,
        cita_en: str,
    ) -> tuple[str | None, str | None, str | None]:
        """
        Resolve an English Bible reference to native-language citation + text.

        Parameters
        ----------
        cita_en : English reference, e.g. "John 3:16" or "1 Corinthians 13:4-7"

        Returns
        -------
        (local_cita, texto, None)      on success
        (None,       None,  reason)    on failure

        Failure reasons:
          - "could not parse reference: '...'"
          - "unknown book: '...' — not in bible_books.json SOT"
          - "verse not found: '...' (chapter has N verses)"
        """
        parsed = parse_en_ref(cita_en)
        if parsed is None:
            return None, None, f"could not parse reference: '{cita_en}'"

        book_en, chapter, v_start, v_end = parsed

        # Confirm EN book name against SOT and get book_number
        book_number = self.books_sot.get(book_en)
        if book_number is None:
            return (
                None,
                None,
                f"unknown book: '{book_en}' — not in bible_books.json SOT",
            )

        # Get native book name directly from the DB (no manual mapping needed)
        local_name = self._native_book_name(book_number, fallback=book_en)

        texto = fetch_text(self.cursor, book_number, chapter, v_start, v_end)
        if texto is None:
            self.cursor.execute(
                "SELECT MAX(verse) FROM verses WHERE book_number=? AND chapter=?",
                (book_number, chapter),
            )
            row = self.cursor.fetchone()
            max_verse = row[0] if row and row[0] else "unknown"
            range_str = f"{v_start}-{v_end}" if v_start != v_end else str(v_start)
            return (
                None,
                None,
                (
                    f"verse not found: '{cita_en}' → {local_name} {chapter}:{range_str} "
                    f"(chapter has {max_verse} verses)"
                ),
            )

        range_suffix = f"{v_start}-{v_end}" if v_start != v_end else str(v_start)
        local_cita = f"{local_name} {chapter}:{range_suffix}"
        return local_cita, texto, None

    def resolve_many(
        self,
        refs: list[str],
    ) -> list[dict]:
        """
        Resolve a list of English references in one call.

        Returns list of dicts:
          {"ref": original, "cita": local_cita, "texto": texto, "error": None}
          {"ref": original, "cita": None,       "texto": None,  "error": reason}
        """
        results = []
        for ref in refs:
            cita, texto, error = self.resolve(ref)
            results.append(
                {
                    "ref": ref,
                    "cita": cita,
                    "texto": texto,
                    "error": error,
                }
            )
        return results

    def verse_count(self) -> int:
        """Return total number of verses in the connected DB."""
        self.cursor.execute("SELECT COUNT(*) FROM verses")
        return self.cursor.fetchone()[0]

"""
test_book_name_sanitizer.py — Unit tests for the data-driven book-title sanitizer.

Guards the defect that shipped in seed_de_SCH2000_for_2027.json: the extractor
returned each DB's raw `books.long_name`, so citations carried liturgical long
forms ("Das Evangelium nach Johannes 10:14") instead of the readable titles
German Bibles actually use ("Johannes 10:14").

No DB exposes a citation-ready title column — `short_name` is only an
abbreviation ("Joh", "Apg") and `books_all.long_name` repeats the long form —
so canonical titles are curated per language in
`seed_generation/tools/book_name_sanitizers/<lang>.json`, keyed by the
canonical MyBible `book_number`. These tests protect that contract, the
per-language config data, and the "only the citation title is rewritten"
guarantee of the repair pass.
"""

import json
import re
import unittest
from pathlib import Path

from seed_generation.tools.book_name_normalizer import (
    load_title_aliases,
    sanitize_book_name,
)
from seed_generation.tools.sanitize_seed_citations import (
    build_title_map,
    sanitize_citation,
    sanitize_seed,
)

_TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
_SANITIZER_DIR = _TOOLS_DIR / "book_name_sanitizers"
LOCAL_LU17_DB = _TOOLS_DIR / "Bibles" / "DE" / "LU17_de.SQLite3"
LOCAL_NAV_DB = _TOOLS_DIR / "Bibles" / "NAV_ar.SQLite3"

# The 66 canonical Protestant book numbers used by the MyBible format.
CANONICAL_66 = [
    *range(10, 170, 10),  # 1.-5. Mose … Nehemia (16)
    190,  # Ester
    *range(220, 270, 10),  # Hiob … Hoheslied
    290, 300, 310, 330, 340,  # Jesaja … Daniel (ohne Baruch)
    *range(350, 470, 10),  # Hosea … Maleachi
    *range(470, 740, 10),  # Matthäus … Offenbarung
]

# Liturgical leading phrases that must never survive sanitization.
_LONG_FORM_PREFIXES = (
    "Das Buch ",
    "Das erste Buch ",
    "Das zweite Buch ",
    "Das dritte Buch ",
    "Das vierte Buch ",
    "Das fünfte Buch ",
    "Das Evangelium ",
    "Der Brief ",
    "Der erste Brief ",
    "Der zweite Brief ",
    "Der dritte Brief ",
    "Der Prophet ",
    "Die Apostelgeschichte",
    "Die Psalmen",
)


class TestSanitizeBookName(unittest.TestCase):
    def test_maps_liturgical_long_form_to_canonical(self):
        self.assertEqual(
            sanitize_book_name("Das Evangelium nach Johannes", 500, "de"), "Johannes"
        )

    def test_maps_schlachter_style_pauline_title_to_canonical(self):
        self.assertEqual(
            sanitize_book_name("Der Brief des Apostels Paulus an die Römer", 520, "de"),
            "Römer",
        )

    def test_maps_schlachter_style_acts_title_to_canonical(self):
        self.assertEqual(sanitize_book_name("Die Apostelgeschichte", 510, "de"), "Apostelgeschichte")

    def test_maps_luther_style_psalter_to_canonical(self):
        self.assertEqual(sanitize_book_name("Der Psalter", 230, "de"), "Psalm")

    def test_language_is_case_insensitive(self):
        self.assertEqual(sanitize_book_name("Die Psalmen", 230, "DE"), "Psalm")

    def test_returns_raw_name_when_language_is_none(self):
        """Backwards compatibility: callers that pass no language keep old behaviour."""
        self.assertEqual(
            sanitize_book_name("Das Evangelium nach Johannes", 500, None),
            "Das Evangelium nach Johannes",
        )

    def test_returns_raw_name_for_language_without_config(self):
        self.assertEqual(sanitize_book_name("Genesis", 10, "zz"), "Genesis")

    def test_returns_raw_name_for_unconfigured_book_number(self):
        self.assertEqual(sanitize_book_name("Some Unknown Book", 9999, "de"), "Some Unknown Book")


class TestLanguageConfigsAreWellFormed(unittest.TestCase):
    def _configs(self) -> dict[str, dict[str, str]]:
        files = sorted(_SANITIZER_DIR.glob("*.json"))
        self.assertTrue(files, f"no sanitizer configs found in {_SANITIZER_DIR}")
        return {
            path.stem: json.loads(path.read_text(encoding="utf-8"))["book_names"]
            for path in files
        }

    def test_every_config_uses_numeric_book_weight_keys(self):
        for language, config in self._configs().items():
            with self.subTest(language=language):
                non_numeric = [k for k in config if not str(k).isdigit()]
                self.assertEqual(non_numeric, [])

    def test_every_config_title_is_unique_and_non_empty(self):
        for language, config in self._configs().items():
            with self.subTest(language=language):
                titles = list(config.values())
                self.assertEqual([t for t in titles if not t.strip()], [])
                self.assertEqual(len(titles), len(set(titles)))

    def test_no_title_can_be_mistaken_for_a_chapter_verse_suffix(self):
        """A canonical title must never itself contain `<space>N:N`, or citations
        would no longer round-trip through the `<title> <chapter>:<verse>` parse."""
        for language, config in self._configs().items():
            with self.subTest(language=language):
                offenders = [t for t in config.values() if re.search(r"\s\d+:\d+", t)]
                self.assertEqual(offenders, [])

    def test_german_config_covers_all_66_canonical_books(self):
        config = json.loads((_SANITIZER_DIR / "de.json").read_text(encoding="utf-8"))["book_names"]
        missing = [n for n in CANONICAL_66 if str(n) not in config]
        self.assertEqual(missing, [], f"de.json is missing book numbers: {missing}")

    def test_no_config_title_is_a_leftover_long_form(self):
        for language, config in self._configs().items():
            with self.subTest(language=language):
                offenders = [
                    t for t in config.values() if t.startswith(_LONG_FORM_PREFIXES)
                ]
                self.assertEqual(offenders, [])


# Synthetic title map mirroring a real `books` table: raw long_name -> canonical.
TITLE_MAP = {
    "Das Evangelium nach Johannes": "Johannes",
    "Die Psalmen": "Psalm",
    "Das erste Buch Mose (Genesis)": "1. Mose",
    "Johannes": "Johannes",
    "Psalm": "Psalm",
}


class TestSanitizeCitation(unittest.TestCase):
    def test_rewrites_liturgical_title_to_canonical(self):
        cita, verdict = sanitize_citation("Das Evangelium nach Johannes 10:14", TITLE_MAP)
        self.assertEqual((cita, verdict), ("Johannes 10:14", "changed"))

    def test_leaves_already_canonical_citation_untouched(self):
        cita, verdict = sanitize_citation("Johannes 3:16", TITLE_MAP)
        self.assertEqual((cita, verdict), ("Johannes 3:16", "canonical"))

    def test_preserves_multi_word_titles_without_truncating(self):
        cita, _ = sanitize_citation("Das erste Buch Mose (Genesis) 1:1", TITLE_MAP)
        self.assertEqual(cita, "1. Mose 1:1")

    def test_preserves_verse_range_suffix(self):
        cita, _ = sanitize_citation("Die Psalmen 23:1-6", TITLE_MAP)
        self.assertEqual(cita, "Psalm 23:1-6")

    def test_strips_surrounding_whitespace(self):
        cita, _ = sanitize_citation("  Die Psalmen 27:1  ", TITLE_MAP)
        self.assertEqual(cita, "Psalm 27:1")

    def test_reports_unknown_title_as_unconfigured_and_leaves_it(self):
        cita, verdict = sanitize_citation("Der Brief des Judas 1:24", TITLE_MAP)
        self.assertEqual((cita, verdict), ("Der Brief des Judas 1:24", "unconfigured"))

    def test_reports_non_reference_as_unparsed(self):
        cita, verdict = sanitize_citation("kein Verweis", TITLE_MAP)
        self.assertEqual((cita, verdict), ("kein Verweis", "unparsed"))


class TestSanitizeSeed(unittest.TestCase):
    def _seed(self) -> dict:
        return {
            "2027-08-01": {
                "versiculo": {
                    "cita": "Das Evangelium nach Johannes 10:14",
                    "texto": "TEXT-MAIN",
                },
                "para_meditar": [
                    {"cita": "Die Psalmen 23:1", "texto": "TEXT-PM-1"},
                    {"cita": "Johannes 1:1", "texto": "TEXT-PM-2"},
                ],
                "tags": ["Hoffnung", "Heiliger Geist"],
            }
        }

    def test_rewrites_only_the_citation_key(self):
        seed = self._seed()
        original = json.loads(json.dumps(seed))

        out, changes, problems = sanitize_seed(seed, TITLE_MAP)

        self.assertEqual(problems, [])
        self.assertEqual(
            [c["after"] for c in changes],
            ["Johannes 10:14", "Psalm 23:1"],
        )
        self.assertEqual(list(out), list(original))  # dates untouched
        self.assertEqual(out["2027-08-01"]["tags"], original["2027-08-01"]["tags"])
        self.assertEqual(out["2027-08-01"]["versiculo"]["texto"], "TEXT-MAIN")
        self.assertEqual(
            [p["texto"] for p in out["2027-08-01"]["para_meditar"]],
            ["TEXT-PM-1", "TEXT-PM-2"],
        )
        self.assertEqual(
            len(out["2027-08-01"]["para_meditar"]),
            len(original["2027-08-01"]["para_meditar"]),
        )

    def test_is_idempotent(self):
        seed, _, _ = sanitize_seed(self._seed(), TITLE_MAP)
        _, second_changes, second_problems = sanitize_seed(seed, TITLE_MAP)
        self.assertEqual(second_changes, [])
        self.assertEqual(second_problems, [])

    def test_reports_unconfigured_citation_without_dropping_it(self):
        seed = self._seed()
        seed["2027-08-01"]["para_meditar"].append(
            {"cita": "Der Brief des Judas 1:24", "texto": "TEXT-PM-3"}
        )
        out, _, problems = sanitize_seed(seed, TITLE_MAP)
        self.assertEqual([p["verdict"] for p in problems], ["unconfigured"])
        self.assertEqual(len(out["2027-08-01"]["para_meditar"]), 3)


class TestBuildTitleMap(unittest.TestCase):
    @unittest.skipUnless(LOCAL_LU17_DB.exists(), "LU17_de.SQLite3 not present")
    def test_real_german_db_produces_no_long_form_titles(self):
        title_map = build_title_map(str(LOCAL_LU17_DB), "de")
        self.assertTrue(title_map)
        offenders = [
            canonical
            for canonical in title_map.values()
            if canonical.startswith(_LONG_FORM_PREFIXES)
        ]
        self.assertEqual(offenders, [], "raw long names leaked out of the sanitizer")

    @unittest.skipUnless(LOCAL_LU17_DB.exists(), "LU17_de.SQLite3 not present")
    def test_real_german_db_maps_known_books(self):
        title_map = build_title_map(str(LOCAL_LU17_DB), "de")
        self.assertEqual(title_map.get("Das Evangelium nach Johannes"), "Johannes")
        self.assertEqual(title_map.get("Der Psalter"), "Psalm")

    @unittest.skipUnless(LOCAL_LU17_DB.exists(), "LU17_de.SQLite3 not present")
    def test_already_canonical_citation_resolves_to_itself(self):
        """A sanitized citation must be recognised, not flagged as unmatched —
        this is what makes re-running the pass on a seed idempotent."""
        title_map = build_title_map(str(LOCAL_LU17_DB), "de")
        cita, verdict = sanitize_citation("Johannes 3:16", title_map)
        self.assertEqual((cita, verdict), ("Johannes 3:16", "canonical"))

    @unittest.skipUnless(LOCAL_LU17_DB.exists(), "LU17_de.SQLite3 not present")
    def test_real_german_db_pass_over_canonical_seed_reports_no_problems(self):
        title_map = build_title_map(str(LOCAL_LU17_DB), "de")
        seed = {
            "2027-08-01": {
                "versiculo": {"cita": "Hebräer 13:6", "texto": "T"},
                "para_meditar": [{"cita": "Psalm 27:1", "texto": "T"}],
                "tags": ["Hoffnung"],
            }
        }
        _, changes, problems = sanitize_seed(seed, title_map)
        self.assertEqual(changes, [])
        self.assertEqual(problems, [])


class TestTitleAliases(unittest.TestCase):
    """`book_names` is keyed by book_number, so it can only correct titles a DB
    itself produces. `aliases` declares title-level variants that no DB column
    carries — without it the repair pass has no way to extend the config for
    them, and would have to leave them unmatched forever."""

    def test_aliases_default_to_empty(self):
        self.assertEqual(load_title_aliases(None), {})
        self.assertEqual(load_title_aliases("zz"), {})

    def test_every_alias_target_is_a_configured_canonical_title(self):
        for path in sorted(_SANITIZER_DIR.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            canonical = set(data["book_names"].values())
            unknown = set(data.get("aliases", {}).values()) - canonical
            with self.subTest(language=path.stem):
                self.assertEqual(unknown, set())

    @unittest.skipUnless(LOCAL_NAV_DB.exists(), "NAV_ar.SQLite3 not present")
    def test_variant_title_is_normalized_not_left_unmatched(self):
        title_map = build_title_map(str(LOCAL_NAV_DB), "ar")
        self.assertEqual(
            sanitize_citation("رؤيا يوحنا اللاهوتي 22:17", title_map),
            ("الرؤيا 22:17", "changed"),
        )

    @unittest.skipUnless(LOCAL_NAV_DB.exists(), "NAV_ar.SQLite3 not present")
    def test_canonical_title_stays_canonical_after_aliases(self):
        """Aliases must not break idempotency for the canonical form."""
        title_map = build_title_map(str(LOCAL_NAV_DB), "ar")
        self.assertEqual(
            sanitize_citation("الرؤيا 22:17", title_map),
            ("الرؤيا 22:17", "canonical"),
        )



"""
test_consecutive_dup_skip.py — Unit tests for the shared consecutive-duplicate-word check.

Guards the Arabic skip-list: the 2027 AR NAV devotionals legitimately contain
scriptural / adverbial repetition ("الحق الحق أقول لك" — Jesus's "truly, truly",
"خطوة خطوة" — step by step, "واحداً واحداً" — one by one) that the validator
used to flag as a generation artifact. A genuine doubled word must still fail.
"""

import unittest

from seed_generation.shared.consecutive_dup_skip import (
    find_consecutive_duplicate,
    get_consecutive_dup_skip,
)


class ArabicIdiomSkipTests(unittest.TestCase):
    def test_arabic_idioms_are_not_flagged(self):
        for text in (
            "يبدأ يسوع بعبارة مؤكدة هي الحق الحق أقول لك، ثم يعلن",
            "فنسير في العام الجديد خطوة خطوة. لا نحتاج",
            "نسمي همومنا واحداً واحداً، ونسلمها له",
            "بل يعرفنا واحدًا واحدًا. وبعدها يأتي",
        ):
            with self.subTest(text=text):
                self.assertIsNone(find_consecutive_duplicate(text, "ar"))

    def test_genuine_arabic_doubled_word_is_still_flagged(self):
        self.assertIsNotNone(
            find_consecutive_duplicate("كل ما نعمله نعمله في المحبة", "ar")
        )

    def test_trisagion_with_conjunction_prefix_still_skipped(self):
        self.assertIsNone(
            find_consecutive_duplicate("أنت قدوس وقدوس وقدوس، نشكرك", "ar")
        )

    def test_arabic_skip_words_do_not_leak_to_other_languages(self):
        self.assertNotIn("الحق", get_consecutive_dup_skip("en"))
        self.assertIsNotNone(find_consecutive_duplicate("the quick quick fox", "en"))


if __name__ == "__main__":
    unittest.main()

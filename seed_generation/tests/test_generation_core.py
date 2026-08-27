"""
test_generation_core.py — Unit tests for generation_core.py's checkpoint/resume logic.

Covers three defects found in a real multi-hour local run (2026-08-27):
1. Checkpoint filenames built from an unsanitized external value (--model tag)
   silently break resume when the tag contains filesystem-unsafe characters.
2. Resume trusted completed_count as a loop index instead of checking which
   specific dates were actually completed.
3. Checkpoint saves were not atomic — a crash mid-write could corrupt the
   entire checkpoint file, not just the latest entry.
"""

import json
import os
import tempfile
import unittest

from seed_generation.shared.generation_core import (
    CheckpointStore,
    checkpoint_path_for,
    checkpoint_status,
    pending_dates,
    slugify_identifier,
)


class TestCheckpointPathFor(unittest.TestCase):
    def test_uses_model_when_given(self):
        path = checkpoint_path_for("out", "ollama", "gemma4:26b")
        self.assertIn(slugify_identifier("gemma4:26b"), path)

    def test_falls_back_to_provider_when_no_model(self):
        path = checkpoint_path_for("out", "gemini", None)
        self.assertIn("gemini", path)

    def test_joined_under_output_dir(self):
        path = checkpoint_path_for("some/out/dir", "ollama", "gemma4:26b")
        self.assertTrue(path.startswith("some/out/dir"))


class TestSlugifyIdentifier(unittest.TestCase):
    def test_colon_is_sanitized(self):
        self.assertNotIn(":", slugify_identifier("gemma4:26b"))

    def test_slash_is_sanitized(self):
        self.assertNotIn("/", slugify_identifier("some/model"))

    def test_safe_value_is_unchanged_shape(self):
        # Already-safe values shouldn't be mangled beyond recognition.
        result = slugify_identifier("gemma4-26b")
        self.assertEqual(result, "gemma4-26b")

    def test_distinct_unsafe_values_do_not_collide(self):
        # Two different unsafe tags must not sanitize to the same identifier —
        # a collision here would silently merge two different runs' checkpoints.
        a = slugify_identifier("gemma4:26b")
        b = slugify_identifier("gemma4/26b")
        self.assertNotEqual(a, b)


class TestPendingDates(unittest.TestCase):
    def test_skips_only_dates_actually_completed(self):
        all_dates = ["2027-01-01", "2027-01-02", "2027-01-03"]
        completed = {"2027-01-01": {}}
        self.assertEqual(
            pending_dates(all_dates, completed), ["2027-01-02", "2027-01-03"]
        )

    def test_non_prefix_completion_set_is_still_handled_correctly(self):
        # The bug: real runs don't always complete an exact chronological prefix.
        # If completed dates are NOT the first N of all_dates, index-based resume
        # (start_index = len(completed)) would silently redo or skip real work.
        # Content-based resume must handle this correctly regardless of order.
        all_dates = ["2027-01-01", "2027-01-02", "2027-01-03", "2027-01-04"]
        completed = {"2027-01-01": {}, "2027-01-03": {}}
        self.assertEqual(
            pending_dates(all_dates, completed), ["2027-01-02", "2027-01-04"]
        )

    def test_empty_completed_returns_all_dates(self):
        all_dates = ["2027-01-01", "2027-01-02"]
        self.assertEqual(pending_dates(all_dates, {}), all_dates)

    def test_fully_completed_returns_empty(self):
        all_dates = ["2027-01-01", "2027-01-02"]
        completed = {"2027-01-01": {}, "2027-01-02": {}}
        self.assertEqual(pending_dates(all_dates, completed), [])


class TestCheckpointStatus(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.checkpoint_file = os.path.join(self.tmpdir.name, "checkpoint.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_no_checkpoint_file_returns_none(self):
        self.assertIsNone(
            checkpoint_status(self.checkpoint_file, total=10, seed_path="seed.json")
        )

    def test_reports_done_and_pending_counts(self):
        CheckpointStore(self.checkpoint_file).save(
            {"2027-01-01": {}, "2027-01-02": {}},
            2,
            "seed.json",
            "es",
            "RVR1960",
            "out/",
        )
        status = checkpoint_status(self.checkpoint_file, total=5, seed_path="seed.json")
        self.assertEqual(status["done"], 2)
        self.assertEqual(status["pending"], 3)
        self.assertEqual(status["total"], 5)
        self.assertTrue(status["seed_matches"])

    def test_flags_seed_mismatch(self):
        CheckpointStore(self.checkpoint_file).save(
            {"2027-01-01": {}}, 1, "old_seed.json", "es", "RVR1960", "out/"
        )
        status = checkpoint_status(
            self.checkpoint_file, total=5, seed_path="new_seed.json"
        )
        self.assertFalse(status["seed_matches"])

    def test_readable_without_knowing_total_or_seed_path_in_advance(self):
        # A discovery tool (e.g. a dashboard scanning many checkpoints) needs
        # to read a checkpoint's own metadata (provider, model, seed_path,
        # done count) BEFORE it knows total/seed_path — those two args must
        # be optional, with seed_matches/pending simply omitted when unknown.
        CheckpointStore(self.checkpoint_file).save(
            {"2027-01-01": {}},
            1,
            "seed.json",
            "es",
            "RVR1960",
            "out/",
            provider="ollama",
            model="gemma4:26b",
        )
        status = checkpoint_status(self.checkpoint_file)
        self.assertEqual(status["done"], 1)
        self.assertEqual(status["provider"], "ollama")
        self.assertEqual(status["checkpoint_seed_path"], "seed.json")
        self.assertIsNone(status["pending"])
        self.assertIsNone(status["seed_matches"])


class TestCheckpointStoreAtomicSave(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.checkpoint_file = os.path.join(self.tmpdir.name, "checkpoint.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_save_produces_valid_json(self):
        store = CheckpointStore(self.checkpoint_file)
        store.save({"2027-01-01": {"id": "x"}}, 1, "seed.json", "es", "RVR1960", "out/")
        with open(self.checkpoint_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["completed_count"], 1)

    def test_save_stores_provider_and_model_for_discovery(self):
        # A dashboard/status tool must be able to resume a checkpoint without
        # the user re-typing --provider/--model — the checkpoint needs to
        # remember them itself, not rely solely on its (lossy, slugified)
        # filename as the only source of truth.
        store = CheckpointStore(self.checkpoint_file)
        store.save(
            {"2027-01-01": {"id": "x"}},
            1,
            "seed.json",
            "es",
            "RVR1960",
            "out/",
            provider="ollama",
            model="gemma4:26b",
        )
        with open(self.checkpoint_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["provider"], "ollama")
        self.assertEqual(data["model"], "gemma4:26b")

    def test_save_without_provider_model_still_works(self):
        # Backward compatibility: existing call sites that don't pass these
        # new optional kwargs must keep working unchanged.
        store = CheckpointStore(self.checkpoint_file)
        store.save({"2027-01-01": {"id": "x"}}, 1, "seed.json", "es", "RVR1960", "out/")
        with open(self.checkpoint_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsNone(data.get("provider"))
        self.assertIsNone(data.get("model"))

    def test_no_leftover_temp_file_after_save(self):
        store = CheckpointStore(self.checkpoint_file)
        store.save({"2027-01-01": {"id": "x"}}, 1, "seed.json", "es", "RVR1960", "out/")
        leftovers = [f for f in os.listdir(self.tmpdir.name) if f != "checkpoint.json"]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()

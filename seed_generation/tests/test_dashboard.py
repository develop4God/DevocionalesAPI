"""
test_dashboard.py — Unit/integration tests for the checkpoint dashboard.

Covers the actual friction the dashboard exists to remove: resuming a
checkpointed run without the user re-typing --seed/--lang/--version/
--provider/--model, by auto-discovering checkpoints and reading their
own stored metadata.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from seed_generation.dashboard import _resume, discover_checkpoints
from seed_generation.shared.generation_core import CheckpointStore, checkpoint_path_for


def _write_seed(path: str, dates: list[str]) -> None:
    seed = {
        d: {
            "versiculo": {"cita": "Juan 3:16", "texto": "texto"},
            "para_meditar": "m",
            "tags": [],
        }
        for d in dates
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seed, f)


class _FakeGenerator:
    def generate(self, prompt: str) -> str:
        return json.dumps(
            {
                "reflexion": "x" * 950,
                "oracion": "y " * 160 + "in the name of Jesus, amen",
            }
        )


class TestDiscoverCheckpoints(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.output_root = self.tmpdir.name
        self.lang_dir = os.path.join(self.output_root, "es")
        os.makedirs(self.lang_dir)
        self.seed_path = os.path.join(self.output_root, "seed.json")
        _write_seed(self.seed_path, ["2027-01-01", "2027-01-02", "2027-01-03"])

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_no_checkpoints_returns_empty_list(self):
        self.assertEqual(discover_checkpoints(self.output_root), [])

    def test_finds_checkpoint_with_full_metadata(self):
        checkpoint_file = os.path.join(
            self.lang_dir, "generate_from_seed_checkpoint_gemma4-3a-26b.json"
        )
        CheckpointStore(checkpoint_file).save(
            {"2027-01-01": {}},
            1,
            self.seed_path,
            "es",
            "RVR1960",
            self.lang_dir,
            provider="ollama",
            model="gemma4:26b",
        )
        entries = discover_checkpoints(self.output_root)
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["provider"], "ollama")
        self.assertEqual(e["model"], "gemma4:26b")
        self.assertEqual(e["done"], 1)
        self.assertEqual(e["total"], 3)
        self.assertEqual(e["pending"], 2)
        self.assertTrue(e["seed_exists"])

    def test_missing_seed_file_flagged_not_crashed(self):
        checkpoint_file = os.path.join(
            self.lang_dir, "generate_from_seed_checkpoint_x.json"
        )
        CheckpointStore(checkpoint_file).save(
            {"2027-01-01": {}}, 1, "/no/such/seed.json", "es", "RVR1960", self.lang_dir
        )
        entries = discover_checkpoints(self.output_root)
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]["seed_exists"])

    def test_sorted_by_pending_descending(self):
        # A checkpoint further from completion should be listed first.
        cp_far = os.path.join(self.lang_dir, "generate_from_seed_checkpoint_a.json")
        CheckpointStore(cp_far).save(
            {"2027-01-01": {}}, 1, self.seed_path, "es", "RVR1960", self.lang_dir
        )
        cp_near = os.path.join(self.lang_dir, "generate_from_seed_checkpoint_b.json")
        CheckpointStore(cp_near).save(
            {"2027-01-01": {}, "2027-01-02": {}},
            2,
            self.seed_path,
            "es",
            "RVR1960",
            self.lang_dir,
        )
        entries = discover_checkpoints(self.output_root)
        self.assertEqual(entries[0]["checkpoint_file"], cp_far)
        self.assertEqual(entries[1]["checkpoint_file"], cp_near)


class TestResume(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.output_root = self.tmpdir.name
        self.lang_dir = os.path.join(self.output_root, "es")
        os.makedirs(self.lang_dir)
        self.seed_path = os.path.join(self.output_root, "seed.json")
        _write_seed(self.seed_path, ["2027-01-01", "2027-01-02"])

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_resume_with_full_metadata_generates_remaining_entries(self):
        # Real checkpoints are always named via checkpoint_path_for() — using
        # that here (not an arbitrary name) is what makes this test catch a
        # real bug: generate_from_seed() re-derives its checkpoint path from
        # (output_dir, provider, model) rather than accepting the discovered
        # path directly, so this must match what it will actually compute.
        checkpoint_file = checkpoint_path_for(self.lang_dir, "ollama", "gemma4:26b")
        CheckpointStore(checkpoint_file).save(
            {"2027-01-01": {"id": "kept"}},
            1,
            self.seed_path,
            "es",
            "RVR1960",
            self.lang_dir,
            provider="ollama",
            model="gemma4:26b",
        )
        entries = discover_checkpoints(self.output_root)
        entry = entries[0]
        with (
            patch(
                "seed_generation.generate_from_seed.build_generator",
                return_value=_FakeGenerator(),
            ),
            patch("builtins.input", return_value=""),
        ):
            _resume(entry)
        outputs = [f for f in os.listdir(self.lang_dir) if f.startswith("raw_")]
        self.assertEqual(len(outputs), 1)
        with open(os.path.join(self.lang_dir, outputs[0]), encoding="utf-8") as f:
            data = json.load(f)
        # Pre-existing entry preserved (not regenerated) AND the missing one filled in.
        self.assertEqual(data["data"]["es"]["2027-01-01"][0]["id"], "kept")
        self.assertIn("2027-01-02", data["data"]["es"])

    def test_resume_without_provider_refuses_without_crashing(self):
        checkpoint_file = os.path.join(
            self.lang_dir, "generate_from_seed_checkpoint_old.json"
        )
        CheckpointStore(checkpoint_file).save(
            {"2027-01-01": {}}, 1, self.seed_path, "es", "RVR1960", self.lang_dir
        )
        entry = discover_checkpoints(self.output_root)[0]
        with patch("builtins.input", return_value=""):
            _resume(entry)  # must not raise
        outputs = [f for f in os.listdir(self.lang_dir) if f.startswith("raw_")]
        self.assertEqual(outputs, [])  # nothing generated — refused safely


if __name__ == "__main__":
    unittest.main()

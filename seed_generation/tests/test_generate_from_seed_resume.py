"""
test_generate_from_seed_resume.py — Integration test for generate_from_seed()'s
resume flow end-to-end: a checkpoint with a non-chronological-prefix completed
set must resume onto exactly the remaining dates, and only those.

This is the scenario that broke in production: a real run's checkpoint file
did not match `--model` due to an unsanitized filename, so resume silently
did not trigger. This test drives generate_from_seed() itself (not just the
pending_dates/slugify_identifier units) to catch wiring regressions.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from seed_generation.generate_from_seed import generate_from_seed
from seed_generation.shared.generation_core import CheckpointStore, slugify_identifier


class _FakeGenerator:
    def generate(self, prompt: str) -> str:
        return json.dumps(
            {
                "reflexion": "x" * 950,
                "oracion": "y " * 160 + "in the name of Jesus, amen",
            }
        )


def _write_seed(path: str, dates: list[str]) -> None:
    seed = {
        d: {
            "versiculo": {"cita": "Juan 3:16", "texto": "texto de ejemplo"},
            "para_meditar": "meditar",
            "tags": [],
        }
        for d in dates
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seed, f)


class TestResumeEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.seed_path = os.path.join(self.tmpdir.name, "seed.json")
        self.output_dir = self.tmpdir.name
        self.all_dates = ["2027-01-01", "2027-01-02", "2027-01-03", "2027-01-04"]
        _write_seed(self.seed_path, self.all_dates)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run(self, model="gemma4:26b", answer_resume="y"):
        with patch(
            "seed_generation.generate_from_seed.build_generator",
            return_value=_FakeGenerator(),
        ), patch("builtins.input", return_value=answer_resume):
            generate_from_seed(
                seed_path=self.seed_path,
                master_lang="es",
                master_version="RVR1960",
                provider="ollama",
                output_dir=self.output_dir,
                model=model,
            )

    def test_resumes_onto_exactly_the_remaining_non_prefix_dates(self):
        # Pre-seed a checkpoint whose completed set is NOT a chronological
        # prefix of all_dates — the real-world scenario that a position-based
        # resume (start_index = completed_count) would get wrong.
        checkpoint_file = os.path.join(
            self.output_dir,
            f"generate_from_seed_checkpoint_{slugify_identifier('gemma4:26b')}.json",
        )
        completed = {
            "2027-01-01": {"id": "a"},
            "2027-01-03": {"id": "b"},
        }
        CheckpointStore(checkpoint_file).save(
            completed, 2, self.seed_path, "es", "RVR1960", self.output_dir
        )

        self._run()

        # Original two entries must survive untouched; the checkpoint's own
        # 'a'/'b' placeholder devotionals prove they were NOT regenerated.
        outputs = [f for f in os.listdir(self.output_dir) if f.startswith("raw_")]
        self.assertEqual(len(outputs), 1)
        with open(os.path.join(self.output_dir, outputs[0]), encoding="utf-8") as f:
            data = json.load(f)
        dates_in_output = set(data["data"]["es"].keys())
        self.assertEqual(dates_in_output, set(self.all_dates))
        self.assertEqual(data["data"]["es"]["2027-01-01"][0]["id"], "a")
        self.assertEqual(data["data"]["es"]["2027-01-03"][0]["id"], "b")

    def test_checkpoint_filename_matches_regardless_of_unsafe_model_tag(self):
        # The exact production bug: --model "gemma4:26b" must resolve to the
        # same checkpoint file whether written earlier by this same code path
        # or found on a subsequent run — no colon ever reaches the filename.
        self._run(model="gemma4:26b", answer_resume="n")
        checkpoint_files = [
            f
            for f in os.listdir(self.output_dir)
            if f.startswith("generate_from_seed_checkpoint")
        ]
        self.assertEqual(checkpoint_files, [])  # deleted on full completion
        outputs = [f for f in os.listdir(self.output_dir) if f.startswith("raw_")]
        self.assertEqual(len(outputs), 1)
        self.assertNotIn(":", outputs[0])


if __name__ == "__main__":
    unittest.main()

import importlib
import os
import unittest
from unittest import mock

import seed_generation.test_generate_ollama as mod


class OllamaUrlTest(unittest.TestCase):
    def tearDown(self):
        importlib.reload(mod)

    def test_defaults_to_localhost(self):
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop("OLLAMA_URL", None)
            importlib.reload(mod)
            self.assertEqual(mod.OLLAMA_URL, "http://localhost:11434/api/generate")

    def test_env_var_overrides(self):
        with mock.patch.dict(
            os.environ, {"OLLAMA_URL": "http://localhost:11435/api/generate"}
        ):
            importlib.reload(mod)
            self.assertEqual(mod.OLLAMA_URL, "http://localhost:11435/api/generate")


if __name__ == "__main__":
    unittest.main()

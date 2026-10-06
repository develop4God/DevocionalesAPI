import unittest

from seed_generation.shared.providers import (
    PROVIDERS,
    OllamaGenerator,
    OracleGenerator,
    build_generator,
    resolve_model,
)


class OracleProviderTest(unittest.TestCase):
    def test_registered_as_provider(self):
        self.assertIs(PROVIDERS["oracle"], OracleGenerator)

    def test_default_model_resolves_to_gemma4_12b(self):
        self.assertEqual(resolve_model("oracle", None)["model_id"], "gemma4:12b")

    def test_build_generator_uses_oracle_config_block(self):
        gen = build_generator("oracle")
        self.assertIsInstance(gen, OracleGenerator)
        self.assertEqual(gen._model, "gemma4:12b")
        self.assertEqual(gen._url, "http://localhost:11435/api/generate")
        self.assertEqual(gen._timeout, 900)
        self.assertFalse(gen._thinking_enabled)

    def test_ollama_provider_unchanged(self):
        gen = build_generator("ollama")
        self.assertNotIsInstance(gen, OracleGenerator)
        self.assertIsInstance(gen, OllamaGenerator)
        self.assertEqual(gen._url, "http://localhost:11434/api/generate")
        self.assertEqual(gen._timeout, 300)

    def test_explicit_url_overrides_config(self):
        gen = OracleGenerator(model="gemma4:12b", url="http://x:1/api/generate")
        self.assertEqual(gen._url, "http://x:1/api/generate")


if __name__ == "__main__":
    unittest.main()

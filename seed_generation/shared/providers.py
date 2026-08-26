"""
providers.py
────────────
Thin Generator implementations (see generation_core.Generator) for the
sync, in-process seed generation path — as opposed to BATCH/provider_adapter.py,
which handles genuine async/batch jobs (Anthropic/Gemini native batch,
Fireworks). Each class here does exactly one thing: turn a prompt into
raw model text. All prompt-building, JSON repair, validation, checkpoint,
and output logic lives elsewhere (generation_core.py) and is shared
across every provider.

Model names/defaults live in providers.yml (same directory) — to add a
model alias or change a default, edit the YAML, not this file.

Each provider is imported lazily by its own __init__ so that using one
provider never requires the SDK or API key of another (e.g. --provider
ollama should never require ANTHROPIC_API_KEY to be set).
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import yaml

_PROVIDERS_YML = Path(__file__).parent / "providers.yml"


def _load_config() -> dict:
    with open(_PROVIDERS_YML, encoding="utf-8") as f:
        return yaml.safe_load(f)["providers"]


def resolve_model(provider: str, model_alias: str | None) -> dict:
    """Resolve a model alias (or the provider's default) to its full config block.

    Returns a dict with at least `model_id`. If the alias isn't a known entry
    in providers.yml, it's treated as a raw model_id/tag with no extra config.
    """
    cfg = _load_config()[provider]
    alias = model_alias or cfg["defaults"]["model"]
    if alias in cfg["models"]:
        return cfg["models"][alias]
    return {"model_id": alias}  # not a known alias — treat as a raw model_id/tag


class OllamaGenerator:
    """Local Ollama model — no API key, no network beyond localhost."""

    def __init__(
        self,
        model: str,
        url: str | None = None,
        timeout: int | None = None,
        thinking_enabled: bool = False,
    ):
        cfg = _load_config()["ollama"]
        self._model = model
        self._url = url or cfg["url"]
        self._timeout = timeout or cfg["timeout_seconds"]
        self._thinking_enabled = thinking_enabled

    def generate(self, prompt: str) -> str:
        payload = json.dumps(
            {
                "model": self._model,
                "prompt": prompt,
                "think": self._thinking_enabled,
                "stream": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self._url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result.get("response", "")


class GeminiGenerator:
    """Google Gemini via google-genai SDK — sync call, no server needed."""

    def __init__(self, model: str, api_key: str | None = None, max_output_tokens: int | None = None):
        from google import genai
        from google.genai import types

        cfg = _load_config()["gemini"]
        key = api_key or os.environ.get(cfg["api_key_env"])
        if not key:
            raise ValueError(f"{cfg['api_key_env']} not set.")
        self._client = genai.Client(api_key=key)
        self._types = types
        self._model = model
        self._max_output_tokens = max_output_tokens or 8192

    def generate(self, prompt: str) -> str:
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=self._types.GenerateContentConfig(max_output_tokens=self._max_output_tokens),
        )
        return response.text or ""


class AnthropicGenerator:
    """Anthropic Claude via anthropic SDK — sync call, no server needed."""

    def __init__(self, model: str, api_key: str | None = None, max_tokens: int | None = None):
        import anthropic

        cfg = _load_config()["anthropic"]
        key = api_key or os.environ.get(cfg["api_key_env"])
        if not key:
            raise ValueError(f"{cfg['api_key_env']} not set.")
        self._client = anthropic.Anthropic(api_key=key)
        self._model = model
        self._max_tokens = max_tokens or 4096

    def generate(self, prompt: str) -> str:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text


PROVIDERS = {
    "ollama": OllamaGenerator,
    "gemini": GeminiGenerator,
    "anthropic": AnthropicGenerator,
}


def build_generator(provider: str, model_alias: str | None = None):
    """Construct a Generator for `provider`, resolving `model_alias` via providers.yml.

    Adding a new model tag/version for an existing provider requires only a
    providers.yml edit — no Python change. Adding a brand-new provider still
    needs one Generator class (the actual API-calling code is unavoidable).
    """
    cls = PROVIDERS.get(provider)
    if cls is None:
        raise ValueError(f"Unknown provider {provider!r}. Available: {list(PROVIDERS)}")
    model_cfg = resolve_model(provider, model_alias)
    kwargs = {"model": model_cfg["model_id"]}
    if model_cfg.get("supports_thinking"):
        kwargs["thinking_enabled"] = model_cfg.get("thinking_enabled", False)
    return cls(**kwargs)

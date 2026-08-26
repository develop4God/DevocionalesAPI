"""
providers.py
────────────
Thin Generator implementations (see generation_core.Generator) for the
sync, in-process seed generation path — as opposed to provider_adapter.py,
which handles genuine async/batch jobs (Anthropic/Gemini native batch,
Fireworks). Each class here does exactly one thing: turn a prompt into
raw model text. All prompt-building, JSON repair, validation, checkpoint,
and output logic lives elsewhere (pipeline_shared.py, generation_core.py)
and is shared across every provider.

Each provider is imported lazily by its own __init__ so that using one
provider never requires the SDK or API key of another (e.g. --provider
ollama should never require ANTHROPIC_API_KEY to be set).

Not wired into a CLI yet — this is the foundation other code will call.
"""

from __future__ import annotations

import json
import os
import urllib.request


class OllamaGenerator:
    """Local Ollama model — no API key, no network beyond localhost."""

    def __init__(self, model: str, url: str = "http://localhost:11434/api/generate", timeout: int = 180):
        self._model = model
        self._url = url
        self._timeout = timeout

    def generate(self, prompt: str) -> str:
        payload = json.dumps(
            {"model": self._model, "prompt": prompt, "think": False, "stream": False}
        ).encode("utf-8")
        req = urllib.request.Request(
            self._url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result.get("response", "")


class GeminiGenerator:
    """Google Gemini via google-genai SDK — sync call, no server needed."""

    def __init__(self, model: str = "gemini-2.0-flash", api_key: str | None = None):
        from google import genai
        from google.genai import types

        key = api_key or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise ValueError("GOOGLE_API_KEY not set.")
        self._client = genai.Client(api_key=key)
        self._types = types
        self._model = model

    def generate(self, prompt: str) -> str:
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=self._types.GenerateContentConfig(max_output_tokens=8192),
        )
        return response.text or ""


class AnthropicGenerator:
    """Anthropic Claude via anthropic SDK — sync call, no server needed."""

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None, max_tokens: int = 4096):
        import anthropic

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY not set.")
        self._client = anthropic.Anthropic(api_key=key)
        self._model = model
        self._max_tokens = max_tokens

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

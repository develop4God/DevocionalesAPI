"""
ollama_client.py — GEP Critic v3
Single responsibility: call Ollama, parse JSON, retry on failure.
"""

import json
import re
import time
import urllib.request
import urllib.error

from models import PauseCategory, ReaderReaction, Verdict
from models_helper import get_model_for_key

OLLAMA_URL = "http://localhost:11434/api/generate"

# Model keys accepted by --model flag:
#   auto   → best model that fits available RAM (recommended)
#   fast   → smallest installed model
#   best   → highest quality installed model
#   <tag>  → any direct Ollama tag, e.g. qwen2.5:7b
MODEL_KEYS = ["auto", "fast", "best"]

MAX_RETRIES    = 2
RETRY_DELAY_S  = 3


def call_ollama(
    model: str,
    system: str,
    user: str,
    -> tuple[ReaderReaction | None, str | None]:
    """
    Returns (ReaderReaction, raw_response) on success.
    Returns (None, raw_response_or_error) on failure.
    If return_raw=True, always returns the full raw model response (with <think> blocks) as the second tuple value.
    """
    import inspect
    # Backward compatible: support return_raw kwarg
    frame = inspect.currentframe().f_back
    return_raw = frame.f_locals.get('return_raw', False)

    payload = json.dumps({
        "model": model,
        "prompt": user,
        "system": system,
        "stream": False,
        # NOTE: Do NOT use "format": "json" with Qwen3 thinking models.
        # Ollama's grammar-constrained JSON conflicts with thinking-token generation
        # and produces an empty response. The prompts already instruct JSON output.
        "options": {
            "temperature": 0.1,
            "num_predict": 8192,  # thinking models need room for reasoning + JSON
        },
    }).encode("utf-8")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Request rebuilt each attempt — payload is consumed on first read
            req = urllib.request.Request(
                OLLAMA_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                result  = json.loads(resp.read().decode("utf-8"))
                raw     = result.get("response", "").strip()
                parsed  = _parse_reaction(raw)
                if parsed is None:
                    # Parse failure — retry if attempts remain, otherwise surface raw
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY_S)
                        continue
                    return None, raw if return_raw else f"ParseError: could not extract JSON from model response.\nRaw ({len(raw)} chars): {raw[:600]}"
                return parsed, raw if return_raw else None

        except urllib.error.URLError as e:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S)
                continue
            return None, None if return_raw else f"URLError: {e.reason}"

        except json.JSONDecodeError as e:
            return None, None if return_raw else f"JSONDecodeError on Ollama response: {e}"

    return None, None if return_raw else "Max retries exceeded"


def _parse_reaction(raw: str) -> ReaderReaction | None:
    text = raw
    # Strip <think>...</think> blocks from reasoning models (Qwen3, deepseek-r1, etc.)
    # Ollama normally hides these for supported models, but not always.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    verdict_raw = data.get("verdict", "OK").strip().upper()
    verdict     = Verdict.PAUSE if verdict_raw == "PAUSE" else Verdict.OK

    category = None
    if verdict == Verdict.PAUSE:
        cat_raw = (data.get("category") or "").strip().lower()
        try:
            category = PauseCategory(cat_raw)
        except ValueError:
            category = PauseCategory.OTHER

    return ReaderReaction(
        verdict=verdict,
        reaction=data.get("reaction", "").strip(),
        quoted_pause=data.get("quoted_pause") or None,
        category=category,
        confidence=float(data.get("confidence", 1.0)),
    )

"""
ollama_client.py — GEP Critic v3
Single responsibility: call Ollama, parse JSON, retry on failure.
"""

import json
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
) -> tuple[ReaderReaction | None, str | None]:
    """
    Returns (ReaderReaction, None) on success.
    Returns (None, raw_response_or_error) on failure.
    """
    payload = json.dumps({
        "model": model,
        "prompt": user,
        "system": system,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": 500,   # reader reaction is short — no need for 1500
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result  = json.loads(resp.read().decode("utf-8"))
                raw     = result.get("response", "").strip()
                return _parse_reaction(raw), None

        except urllib.error.URLError as e:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S)
                continue
            return None, f"URLError: {e.reason}"

        except json.JSONDecodeError as e:
            return None, f"JSONDecodeError on Ollama response: {e}"

    return None, "Max retries exceeded"


def _parse_reaction(raw: str) -> ReaderReaction | None:
    # Strip markdown fences if present
    text = raw
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

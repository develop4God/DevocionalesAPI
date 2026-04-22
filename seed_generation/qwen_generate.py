"""
qwen_generate.py
────────────────
Synchronous Qwen (Alibaba Cloud Model Studio) adapter for the devocional_nuevo
devotional content pipeline.

Uses the EXACT same build_prompt() and repair_json() from pipeline_shared.py.
Output schema mirrors what batch_collect produces: the seed fields are preserved
and the model fills in {reflexion, oracion}.

Rate limits (qwen3.6-flash, free tier):
    15 000 RPM  /  5 000 000 TPM

Free quota does NOT cover Batch API → synchronous inference only.
API is OpenAI-compatible via DashScope international endpoint.

Required env var:
    DASHSCOPE_API_KEY   (Alibaba Cloud Model Studio console → API Keys)

Quick smoke test (one real devotional):
    python qwen_generate.py
    python qwen_generate.py --lang en --verse "John 3:16"
    python qwen_generate.py --lang ar --verse "يوحنا 3:16" --topic "محبة الله"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from typing import Any

from openai import OpenAI, APIStatusError, APIConnectionError, APITimeoutError

# ── Shared pipeline utilities (same source of truth as batch_submit.py) ───────
from pipeline_shared import build_prompt, repair_json
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

QWEN_MODEL    = "qwen-flash"
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

MAX_RETRIES    = 3
BACKOFF_BASE   = 2.0    # seconds
BACKOFF_MAX    = 60.0   # seconds cap
BACKOFF_JITTER = 0.5    # ± fraction

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# ─────────────────────────────────────────────────────────────────────────────


class QwenGenerationError(Exception):
    """Raised when Qwen generation fails after all retries."""


# ── Client ────────────────────────────────────────────────────────────────────

def _make_client() -> OpenAI:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "DASHSCOPE_API_KEY is not set. "
            "Get your key from Alibaba Cloud Model Studio → API Keys."
        )
    return OpenAI(api_key=api_key, base_url=QWEN_BASE_URL)


# ── Retry backoff ─────────────────────────────────────────────────────────────

def _backoff_delay(attempt: int) -> None:
    delay = min(BACKOFF_BASE ** (attempt + 1), BACKOFF_MAX)
    jitter = delay * BACKOFF_JITTER * (random.random() * 2 - 1)
    sleep_for = max(0.5, delay + jitter)
    logger.info("[qwen] Retry %d — waiting %.1fs", attempt + 1, sleep_for)
    time.sleep(sleep_for)


# ── API call ──────────────────────────────────────────────────────────────────

def _call_qwen(
    client: OpenAI,
    prompt: str,
    *,
    enable_thinking: bool = False,
) -> str:
    """
    Single synchronous call to qwen3.6-flash.

    The prompt produced by build_prompt() is passed as the user message.
    A lean system message enforces JSON-only output.

    enable_thinking: activates Qwen3 chain-of-thought (higher quality,
    more tokens). Off by default to keep cost/latency low.
    """
    system_msg = (
        "You are a devoted biblical devotional writer. "
        "Return ONLY a valid JSON object with no markdown fences, "
        "no preamble, and no trailing text."
    )

    extra: dict[str, Any] = {}
    if enable_thinking:
        extra["extra_body"] = {"enable_thinking": True}

    response = client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.6,
        max_tokens=4096,   # reflexion ≥900 chars + oracion ≥150 words needs room
        **extra,
    )
    return response.choices[0].message.content or ""


# ── Response parsing ──────────────────────────────────────────────────────────

def _parse_response(raw: str) -> dict[str, Any]:
    """
    Parse model output → dict with at least {reflexion, oracion}.
    Falls back to pipeline_shared.repair_json() on decode failure.
    """
    data = repair_json(raw)   # handles markdown fences + 6-strategy recovery
    if data is None:
        raise QwenGenerationError(
            f"repair_json() could not recover output. "
            f"Raw (first 300 chars): {raw[:300]!r}"
        )
    if "reflexion" not in data or "oracion" not in data:
        raise QwenGenerationError(
            f"Model response missing required keys. Got: {list(data.keys())}"
        )
    return data


# ── Public API ────────────────────────────────────────────────────────────────

def generate_devotional_qwen(
    verse_cita: str,
    lang: str,
    *,
    topic: str | None = None,
    enable_thinking: bool = False,
    client: OpenAI | None = None,
) -> dict[str, Any]:
    """
    Generate one devotional entry using Qwen (synchronous).

    Signature mirrors the pipeline's build_prompt() inputs so callers
    can swap this in alongside the Anthropic/Gemini adapters.

    Args:
        verse_cita:      Verse citation string, e.g. "Juan 3:16".
                         Same value used as the key in the seed JSON.
        lang:            Language code, e.g. "es", "en", "ar", "de".
        topic:           Optional thematic hint (same as seed["topic"]).
        enable_thinking: Enable Qwen3 chain-of-thought mode.
        client:          Pre-built OpenAI client (pass one to reuse across calls).

    Returns:
        dict with keys: reflexion, oracion  (+ _source, _model metadata)

    Raises:
        QwenGenerationError: unrecoverable failure after MAX_RETRIES.
        EnvironmentError:    DASHSCOPE_API_KEY not set.
    """
    if client is None:
        client = _make_client()

    # Identical prompt to what batch_submit.py sends to Anthropic/Gemini
    prompt = build_prompt(verse_cita, lang, topic)

    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            raw    = _call_qwen(client, prompt, enable_thinking=enable_thinking)
            result = _parse_response(raw)
            result["_source"] = "qwen"
            result["_model"]  = QWEN_MODEL
            logger.info("[qwen] ✅ %s / %s (attempt %d)", lang, verse_cita, attempt + 1)
            return result

        except APIStatusError as exc:
            if exc.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                logger.warning("[qwen] HTTP %d — will retry", exc.status_code)
                _backoff_delay(attempt)
                last_error = exc
                continue
            raise QwenGenerationError(
                f"Qwen API error HTTP {exc.status_code} for {verse_cita}: {exc}"
            ) from exc

        except (APIConnectionError, APITimeoutError) as exc:
            if attempt < MAX_RETRIES:
                logger.warning("[qwen] Network error — will retry: %s", exc)
                _backoff_delay(attempt)
                last_error = exc
                continue
            raise QwenGenerationError(
                f"Qwen network error after {MAX_RETRIES} retries for {verse_cita}: {exc}"
            ) from exc

        except QwenGenerationError:
            raise

        except Exception as exc:
            raise QwenGenerationError(
                f"Unexpected error for {verse_cita}: {exc}"
            ) from exc

    raise QwenGenerationError(
        f"Exhausted retries for {verse_cita}"
    ) from last_error


def generate_batch_qwen(
    entries: list[dict[str, Any]],
    lang: str,
    *,
    enable_thinking: bool = False,
    delay_between: float = 0.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Generate multiple devotionals sequentially.

    Args:
        entries:       List of seed entry dicts, each with at least:
                         {"versiculo": {"cita": "..."}, "topic": "..."}
                       Same shape as values in your seed JSON files.
        lang:          Language code.
        enable_thinking: Enable thinking mode for all entries.
        delay_between: Sleep between calls in seconds. Default 0 — at 15k RPM
                       the free quota is generous enough for 365-entry runs.

    Returns:
        (successes, failures)
        Each failure: {"cita": ..., "error": "..."}
    """
    client = _make_client()
    successes: list[dict[str, Any]] = []
    failures:  list[dict[str, Any]] = []

    for i, entry in enumerate(entries):
        cita  = entry["versiculo"]["cita"]
        topic = entry.get("topic")
        try:
            result = generate_devotional_qwen(
                cita, lang,
                topic=topic,
                enable_thinking=enable_thinking,
                client=client,
            )
            result["_cita"]  = cita
            result["_topic"] = topic
            successes.append(result)
        except QwenGenerationError as exc:
            logger.error("[qwen] ❌ Failed %s: %s", cita, exc)
            failures.append({"cita": cita, "error": str(exc)})

        if delay_between > 0 and i < len(entries) - 1:
            time.sleep(delay_between)

    logger.info(
        "[qwen] Batch done: %d ok / %d failed / %d total",
        len(successes), len(failures), len(entries),
    )
    return successes, failures


# ── CLI smoke test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Test Qwen devotional generation — one real API call."
    )
    parser.add_argument("--lang",     default="es",        help="Language code (default: es)")
    parser.add_argument("--verse",    default="Juan 3:16", help="Verse citation")
    parser.add_argument("--topic",    default=None,        help="Optional theme hint")
    parser.add_argument("--thinking", action="store_true", help="Enable chain-of-thought")
    args = parser.parse_args()

    SEP = "─" * 52
    print(f"\n{SEP}")
    print(f"  Qwen smoke test")
    print(f"  Model   : {QWEN_MODEL}")
    print(f"  Lang    : {args.lang}")
    print(f"  Verse   : {args.verse}")
    if args.topic:
        print(f"  Topic   : {args.topic}")
    print(f"  Thinking: {args.thinking}")
    print(f"{SEP}\n")

    try:
        result = generate_devotional_qwen(
            args.verse, args.lang,
            topic=args.topic,
            enable_thinking=args.thinking,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n{SEP}")
        print(f"  reflexion : {len(result.get('reflexion',''))} chars")
        print(f"  oracion   : {len(result.get('oracion',''))} chars")
        print(f"{SEP}\n")
        sys.exit(0)
    except (QwenGenerationError, EnvironmentError) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)

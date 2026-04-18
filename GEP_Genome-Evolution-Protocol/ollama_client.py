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

MAX_RETRIES   = 2
RETRY_DELAY_S = 3


def call_ollama(
    model: str,
    system: str,
    user: str,
    verbose: bool = True,
    think: bool = True,
) -> tuple[ReaderReaction | None, str | None]:
    """
    Streams response from Ollama.
    Returns (ReaderReaction, raw_full) on success — raw_full includes <think> blocks.
    Returns (None, error_str) on failure.
    When verbose=True, prints live thinking progress and verdict to stdout.
    """
    # Validate model parameter (catches common typos/missing models early)
    if not model or model.isspace():
        return None, f"Error: invalid model parameter '{model}'"
    
    payload = json.dumps({
        "model": model,
        "prompt": user,
        "system": system,
        "stream": True,  # streaming avoids read-timeout on thinking models
        # NOTE: Do NOT use "format": "json" with Qwen3 thinking models.
        # Ollama's grammar-constrained JSON conflicts with thinking-token generation
        # and produces an empty response. The prompts already instruct JSON output.
        "think": think,  # expose thinking tokens in separate field (Ollama 0.6+)
        "options": {
            "temperature": 0.1,
            "num_predict": 8192,  # thinking models need room for reasoning + JSON
        },
    }).encode("utf-8")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                OLLAMA_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            raw = _collect_stream(req, verbose=verbose)
            parsed = _parse_reaction(raw)
            if parsed is None:
                if attempt < MAX_RETRIES:
                    if verbose:
                        print(f"  ⚠️  parse failed, retrying ({attempt}/{MAX_RETRIES})...")
                    time.sleep(RETRY_DELAY_S)
                    continue
                return None, f"ParseError: could not extract JSON.\nRaw ({len(raw)} chars): {raw[:600]}"
            return parsed, raw

        except urllib.error.URLError as e:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S)
                continue
            # Capture HTTP response body if available (400 Bad Request usually has details)
            error_msg = f"URLError: {e.reason}"
            if hasattr(e, 'read'):
                try:
                    details = e.read().decode('utf-8')[:400]
                    error_msg += f"\n[Response: {details}]"
                except Exception:
                    pass
            return None, error_msg

        except (json.JSONDecodeError, TimeoutError, OSError) as e:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S)
                continue
            return None, f"{type(e).__name__}: {e}"

    return None, "Max retries exceeded"


def _collect_stream(req: urllib.request.Request, verbose: bool) -> str:
    """
    Reads Ollama NDJSON stream. Returns the complete accumulated response string
    (including any <think> blocks). When verbose=True, prints live progress:
      - Dots for thinking tokens (one dot per 200 chars)
      - Final think size summary when </think> is found
    """
    tokens: list[str] = []
    think_tokens: list[str] = []
    think_chars = 0
    dots_printed = 0

    # timeout=60 is per-read idle timeout, not total.
    # Thinking models send tokens continuously so this won't fire mid-stream.
    with urllib.request.urlopen(req, timeout=60) as resp:
        if verbose:
            print("  💭 ", end="", flush=True)

        for raw_line in resp:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                chunk = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            think_token = chunk.get("thinking", "")
            resp_token  = chunk.get("response", "")
            if think_token:
                think_tokens.append(think_token)
            tokens.append(resp_token)

            if verbose:
                if think_token:
                    think_chars += len(think_token)
                    print(think_token, end="", flush=True)
                if resp_token and think_chars > 0 and len(tokens) == 1:
                    print(f"\n  ({think_chars} chars)", flush=True)
                    print("  📄 response...", flush=True)

            if chunk.get("done"):
                break

    if verbose:
        print()  # finish the output line

    thinking_text = "".join(think_tokens)
    response_text = "".join(tokens)
    if thinking_text:
        return f"<think>{thinking_text}</think>\n{response_text}"
    return response_text


def _parse_reaction(raw: str) -> ReaderReaction | None:
    text = raw
    # Strip <think>...</think> blocks from reasoning models (Qwen3, deepseek-r1, etc.)
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

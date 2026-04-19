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
                # Return the full raw (incl. <think> blocks) so the caller can store
                # thinking tokens and the prose response for debugging/audit.
                return None, raw
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


def _unwrap_text(text: str) -> str:
    """
    Normalize model output before JSON parsing.
    Handles all known non-standard response wrappers — never raises, never returns None.

    In order:
      1. Strip <think>...</think> reasoning blocks (Qwen3, DeepSeek-R1)
      2. Extract content after "Final answer:" when present
         (Qwen3 and similar models often conclude with "Final answer: <answer>")
      3. Unwrap \\boxed{ \\{ ... \\} }  LaTeX format
      4. Strip markdown code fences  ```json ... ```

    Returns the cleanest possible string for json.loads().
    """
    # 1. Strip reasoning blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # 2. Extract content after "Final answer:" / "Final Answer:" / "final answer:"
    #    Requires the colon to be present (excludes "final answer is:").
    #    Handles multi-line gap between label and actual answer.
    fa_m = re.search(r'(?i)final\s+answer\s*:\s*\n*([\s\S]+)', text)
    if fa_m:
        text = fa_m.group(1).strip()

    # 3. Unwrap \boxed{ \{ ... \} } — LaTeX format from some model configs
    boxed_m = re.search(r'\\boxed\s*\{([\s\S]+)\}', text)
    if boxed_m:
        text = boxed_m.group(1).replace(r'\{', '{').replace(r'\}', '}').strip()

    # 4. Strip markdown fences
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.lower().startswith("json"):
            text = text[4:]

    return text.strip()


def _parse_reaction(raw: str) -> ReaderReaction | None:
    text = _unwrap_text(raw)

    data = None

    # 1. Strict JSON parse on the full cleaned text
    try:
        parsed = json.loads(text)
        # Model returned a plain JSON string (e.g. "No repeated phrase found") → OK
        if isinstance(parsed, str):
            tu = parsed.upper()
            if re.search(r'\bPAUSE\b|\bFLAG\b', tu):
                return ReaderReaction(verdict=Verdict.PAUSE, reaction=parsed,
                                      category=PauseCategory.OTHER, confidence=0.7)
            return ReaderReaction(verdict=Verdict.OK, reaction=parsed, confidence=1.0)
        data = parsed
    except json.JSONDecodeError:
        pass

    # 2. Find any JSON object in the text (model may wrap JSON in prose)
    #    — broadened: no longer requires "verdict" key to handle Phase-1 format responses
    if data is None:
        for m in re.finditer(r'\{[^{}]+\}', text, re.DOTALL):
            try:
                candidate = json.loads(m.group())
                # Accept if it has at least one known key from either phase
                known = {"verdict", "reaction", "quoted_pause", "category", "confidence",
                         "quoted_problem", "type", "issue", "quoted", "problem", "error"}
                if known & candidate.keys():
                    data = candidate
                    break
            except json.JSONDecodeError:
                continue

    # 3. Normalise alternate key names produced by different model configs
    if data is not None:
        # Infer verdict from problem-indicating keys when "verdict" is absent
        if "verdict" not in data:
            problem_keys = {"quoted_problem", "problem", "error", "issue", "type"}
            data["verdict"] = "PAUSE" if problem_keys & data.keys() else "OK"
        # Map Phase-1-style keys to Phase-2 canonical names
        if "quoted_problem" in data and "quoted_pause" not in data:
            data["quoted_pause"] = data.pop("quoted_problem")
        if "type" in data and "category" not in data:
            data["category"] = data.pop("type")
        if "issue" in data and "reaction" not in data:
            data["reaction"] = data.pop("issue")
        if "problem" in data and "reaction" not in data:
            data["reaction"] = data.pop("problem")

    # 4. Prose keyword fallback (model answered entirely in natural language)
    if data is None:
        tu = text.upper()
        if re.search(r'\bPAUSE\b', tu) or re.search(r'\bFLAG\b', tu):
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            reaction_text = lines[0] if lines else "Reader paused (prose response)"
            quoted_m = re.search(r'"([^"]{3,120})"', text)
            return ReaderReaction(
                verdict=Verdict.PAUSE,
                reaction=reaction_text,
                quoted_pause=quoted_m.group(1) if quoted_m else None,
                category=PauseCategory.OTHER,
                confidence=0.7,
            )
        # OK / CLEAN / "no issue" / "not found" / "nothing wrong" → no problem detected
        if (re.search(r'\bOK\b', tu) or re.search(r'\bCLEAN\b', tu)
                or re.search(r'no\s+(repeated|issue|problem|error|phrase|flag)\b', tu)
                or re.search(r'nothing\s+(wrong|found|flagged)\b', tu)
                or re.search(r'not\s+found\b', tu)):
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            reaction_text = lines[0] if lines else "Entry looks good (prose response)"
            return ReaderReaction(verdict=Verdict.OK, reaction=reaction_text, confidence=1.0)
        # Last resort — store whatever the model said, mark as OK with low confidence
        # Better to pass through than drop the entry permanently as an unhandled error.
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        reaction_text = (lines[0] if lines else text[:200]) + " [fallback: unrecognised format]"
        return ReaderReaction(verdict=Verdict.OK, reaction=reaction_text, confidence=0.5)

    verdict_raw = (data.get("verdict") or "OK").strip().upper()
    # CLEAN (Phase-1 vocab) and OK (Phase-2 vocab) both mean no issue
    verdict = Verdict.PAUSE if verdict_raw == "PAUSE" else Verdict.OK

    category = None
    if verdict == Verdict.PAUSE:
        cat_raw = (data.get("category") or "").strip().lower()
        try:
            category = PauseCategory(cat_raw)
        except ValueError:
            category = PauseCategory.OTHER

    return ReaderReaction(
        verdict=verdict,
        reaction=(data.get("reaction") or "").strip(),
        quoted_pause=data.get("quoted_pause") or None,
        category=category,
        confidence=float(data.get("confidence", 1.0)),
    )

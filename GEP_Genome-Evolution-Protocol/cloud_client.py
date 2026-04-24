# Shared utility: unwrap model output for JSON parsing
import re
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

    # 3. Unwrap \\boxed{ \\{ ... \\} } — LaTeX format from some model configs
    boxed_m = re.search(r'\\boxed\s*\{([\s\S]+)\}', text)
    if boxed_m:
        text = boxed_m.group(1).replace(r'\\{', '{').replace(r'\\}', '}').strip()


    # 4. Strip markdown fences
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.lower().startswith("json"):
            text = text[4:]

    return text.strip()
"""
cloud_client.py — GEP Critic v3
Single responsibility: call any cloud LLM provider defined in providers.yml.

One client. Zero hardcoded providers. Config-driven routing and fallback.
Drop-in replacement for ollama_client.py — same public interface.

Public API (mirrors ollama_client.py):
  call_ollama(model, system, user, verbose, phase) → (ReaderReaction | None, str | None)
  get_model_for_key(key) → str
  MODEL_KEYS

CLI:
  python cloud_client.py --list
  python cloud_client.py --test --phase 1
  python cloud_client.py --test --phase 2
  python cloud_client.py --usage
  python cloud_client.py --reset-usage
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # handled at load time

from models import PauseCategory, ReaderReaction, Verdict
import ollama_client as _ollama  # local routing

# --- Load .env automatically ---
try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    pass

# ── Config loading ────────────────────────────────────────────────────────────

from paths import PROVIDERS_YML as _PROVIDERS_YML
_config: dict | None = None


def _load_config() -> dict:
    global _config
    if _config is not None:
        return _config
    if yaml is None:
        raise RuntimeError(
            "PyYAML not installed. Run: pip install pyyaml --break-system-packages"
        )
    with open(_PROVIDERS_YML, encoding="utf-8") as f:
        _config = yaml.safe_load(f)
    return _config



def providers_for_phase(phase: int) -> list[dict]:
    """Return providers for the given phase filtered by default_backend, sorted by priority."""
    cfg = _load_config()
    backend = cfg["settings"].get("default_backend", "api")  # "api" or "local"
    phase_key = f"phase{phase}"
    result = [
        p for p in cfg["providers"]
        if p.get("phase") in (phase_key, "both", phase)
        and p.get("client_type", "api") == backend
    ]
    return sorted(result, key=lambda p: p.get("priority", 99))


def settings() -> dict:
    return _load_config().get("settings", {})


# ── Daily usage tracking ──────────────────────────────────────────────────────

def _usage_path() -> Path:
    return Path(__file__).parent / settings().get("daily_counter_path", ".gep_daily_tokens.json")


def _load_usage() -> dict:
    path = _usage_path()
    today = str(date.today())
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return data
        except (json.JSONDecodeError, KeyError):
            pass
    return {"date": today, "providers": {}}


def _save_usage(usage: dict):
    if settings().get("daily_token_tracking", True):
        _usage_path().write_text(json.dumps(usage, indent=2), encoding="utf-8")


def _record_tokens(provider_id: str, tokens: int):
    if not settings().get("daily_token_tracking", True):
        return
    usage = _load_usage()
    bucket = usage["providers"].setdefault(provider_id, {"tokens": 0, "requests": 0})
    bucket["tokens"] += tokens
    bucket["requests"] += 1
    _save_usage(usage)


def _provider_exhausted(provider: dict) -> bool:
    """True if provider is at or near its daily limit."""
    pid = provider["id"]
    usage = _load_usage()
    bucket = usage["providers"].get(pid, {"tokens": 0, "requests": 0})

    skip_pct = settings().get("skip_at_percent", 95) / 100

    tpd = provider["limits"].get("tpd") or 0
    if tpd and bucket["tokens"] >= tpd * skip_pct:
        return True

    rpd = provider["limits"].get("rpd") or 0
    if rpd and bucket["requests"] >= rpd * skip_pct:
        return True

    return False


def _provider_warned(provider: dict) -> bool:
    """True if provider is near (but not at) its daily limit."""
    pid = provider["id"]
    usage = _load_usage()
    bucket = usage["providers"].get(pid, {"tokens": 0, "requests": 0})

    warn_pct = settings().get("warn_at_percent", 80) / 100

    tpd = provider["limits"].get("tpd") or 0
    if tpd and bucket["tokens"] >= tpd * warn_pct:
        return True

    rpd = provider["limits"].get("rpd") or 0
    if rpd and bucket["requests"] >= rpd * warn_pct:
        return True

    return False


# ── Request building ──────────────────────────────────────────────────────────

def _build_request(provider: dict, system: str, user: str, phase: int) -> tuple[dict, str]:
    """
    Build the API payload and resolve the API key.
    Returns (payload_dict, api_key).
    """
    model = provider["model"]
    thinking_cfg = provider.get("thinking_mode", {})
    style = thinking_cfg.get("style", "none")

    # Inject /think tag for SambaNova-style providers
    if style == "think_tag" and phase == 2:
        tag = thinking_cfg.get("inject_tag", "/think")
        user = f"{tag}\n{user}"

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "max_tokens": 4096,
        "temperature": 0.1,
    }

    # reasoning_effort style (Groq, OpenRouter)
    if style == "reasoning_effort" and thinking_cfg.get("supported"):
        param = thinking_cfg.get("param", "reasoning_effort")
        value = thinking_cfg.get("value_on") if phase == 2 else thinking_cfg.get("value_off", "none")
        if value:
            payload[param] = value

    api_key_env = provider.get("env_var", "")
    api_key = os.environ.get(api_key_env, "")
    if not api_key and provider.get("client_type", "api") != "local":
        raise RuntimeError(
            f"API key not set for provider '{provider['id']}'.\n"
            f"Run: export {api_key_env}=your_key_here"
        )
    return payload, api_key or "local"


def _build_http_request(provider: dict, payload: dict, api_key: str) -> urllib.request.Request:
    base_url = provider["base_url"].rstrip("/")
    url = f"{base_url}/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    }
    # Extra headers from config (e.g. OpenRouter referer)
    for k, v in provider.get("headers", {}).items():
        headers[k] = v

    return urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )


# ── Response parsing ──────────────────────────────────────────────────────────

def _parse_response(data: dict) -> tuple[str, int]:
    """Returns (content, total_tokens)."""
    if "error" in data:
        msg = data["error"].get("message", "Unknown API error")
        raise RuntimeError(f"API error: {msg}")
    content = data["choices"][0]["message"]["content"]
    tokens = data.get("usage", {}).get("total_tokens", 0)
    return content, tokens


def _parse_reaction(raw: str) -> Optional[ReaderReaction]:
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{[^{}]*"verdict"[^{}]*\}', text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return None

    verdict_raw = data.get("verdict", "OK").strip().upper()
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
        reaction=data.get("reaction", "").strip(),
        quoted_pause=data.get("quoted_pause") or None,
        category=category,
        confidence=float(data.get("confidence", 1.0)),
    )


def _extract_thinking(content: str) -> str:
    match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
    return match.group(1).strip() if match else ""


# ── Core call ─────────────────────────────────────────────────────────────────

def _call_provider(
    provider: dict,
    system: str,
    user: str,
    phase: int,
    verbose: bool,
) -> tuple[Optional[ReaderReaction], Optional[str], Optional[int]]:
    """
    Single provider call. Returns (reaction, raw_full, tokens_used).
    Returns (None, error_str, None) on failure.
    """
    # ── Local (Ollama) routing ────────────────────────────────────────────
    if provider.get("client_type") == "local":
        model = provider.get("model")
        thinking_cfg = provider.get("thinking_mode", {})
        think = thinking_cfg.get("supported", False) and phase == 2
        reaction, raw = _ollama.call_ollama(model, system, user, verbose=verbose, think=think)
        return reaction, raw, None
    # ── API routing (below) ───────────────────────────────────────────────
    cfg = settings()
    max_retries = cfg.get("max_retries", 2)
    retry_delay = cfg.get("retry_delay_s", 5)

    try:
        payload, api_key = _build_request(provider, system, user, phase)
    except RuntimeError as e:
        return None, str(e), None

    req = _build_http_request(provider, payload, api_key)

    for attempt in range(1, max_retries + 1):
        try:
            if verbose:
                style = "thinking" if phase == 2 else "fast"
                print(f"  [{provider['name']} / {style}] ", end="", flush=True)

            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw_bytes = resp.read()
            elapsed = time.monotonic() - t0

            data = json.loads(raw_bytes)
            content, tokens = _parse_response(data)

            tps = round(tokens / max(elapsed, 0.1))
            if verbose:
                print(f"{tps} tok/s  ({tokens} tokens, {elapsed:.1f}s)")

            thinking = _extract_thinking(content)
            raw_full = f"<think>{thinking}</think>\n{content}" if thinking else content

            reaction = _parse_reaction(content)
            if reaction is None:
                if attempt < max_retries:
                    if verbose:
                        print(f"  parse failed, retrying ({attempt}/{max_retries})...")
                    time.sleep(retry_delay)
                    continue
                return None, f"ParseError: {content[:400]}", None

            return reaction, raw_full, tokens

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                if verbose:
                    print(f" 429 rate limit")
                return None, "429", None          # signal to swap provider
            if attempt < max_retries:
                time.sleep(retry_delay)
                continue
            return None, f"HTTP {e.code}: {body[:200]}", None

        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            if attempt < max_retries:
                time.sleep(retry_delay)
                continue
            return None, f"{type(e).__name__}: {e}", None

    return None, "Max retries exceeded", None


# ── Public API ────────────────────────────────────────────────────────────────

# Mirror ollama_client MODEL_KEYS
MODEL_KEYS = ["auto", "fast", "best"]



def get_model_for_key(key: str) -> str:
    """Returns a display string for the active provider/model for a given key."""
    try:
        phase = 1 if key == "fast" else 2
        providers = providers_for_phase(phase)
        if providers:
            p = providers[0]
            return f"{p['name']}/{p['model']}"
    except Exception:
        pass
    return "cloud/auto"


def call_ollama(
    model: str,
    system: str,
    user: str,
    verbose: bool = True,
    phase: int = 2,
    think: bool = True,  # Ignored, for compatibility
) -> tuple[Optional[ReaderReaction], Optional[str]]:
    """
    Drop-in replacement for ollama_client.call_ollama.
    Reads providers.yml, routes by phase, swaps on 429.
    Returns (ReaderReaction, raw_full) or (None, error_str).
    """
    candidates = providers_for_phase(phase)
    if not candidates:
        return None, f"No providers configured for phase {phase} in providers.yml"

    swap_on_429 = settings().get("swap_on_429", True)
    failures: list[str] = []

    for provider in candidates:
        pid = provider["id"]

        if _provider_exhausted(provider):
            msg = f"[{provider['name']}] daily limit reached — skipped"
            if verbose:
                print(f"  {msg}")
            failures.append(msg)
            continue

        if _provider_warned(provider) and verbose:
            print(f"  [{provider['name']}] approaching daily limit — continuing")

        reaction, raw, tokens = _call_provider(provider, system, user, phase, verbose)

        if raw == "429":
            if swap_on_429:
                msg = f"[{provider['name']}] 429 rate-limit → swapped"
                if verbose:
                    print(f"  swapping to next provider...")
                failures.append(msg)
                continue
            return None, f"429 rate limit on {provider['name']}"

        if reaction is not None:
            if tokens:
                _record_tokens(pid, tokens)
            return reaction, raw

        # Non-429 failure — record detail and try next
        fail_detail = f"[{provider['name']}] {raw}"
        if verbose:
            print(f"  [{provider['name']}] failed: {raw}")
        if tokens:
            _record_tokens(pid, tokens)
        failures.append(fail_detail)

    failures_str = " | ".join(failures) if failures else "no providers available"
    return None, f"All providers exhausted for phase {phase}: {failures_str}"


# ── CLI ───────────────────────────────────────────────────────────────────────

_TEST_SYSTEM = (
    "You are reviewing a Christian devotional. Return ONLY valid JSON:\n"
    '{"verdict": "OK" or "PAUSE", "reaction": "one sentence", '
    '"quoted_pause": null, "category": null, "confidence": 1.0}'
)
_TEST_USER = (
    "Verse: John 15:5 — I am the vine, you are the branches.\n"
    "Reflection: God is the source of all our strength and fruit."
)


def _cmd_list():
    cfg = _load_config()
    print(f"\n  Providers in {_PROVIDERS_YML.name}:\n")
    for p in cfg["providers"]:
        pid = p["id"]
        phase = p.get("phase", "?")
        model = p.get("model", "?")
        limits = p.get("limits", {})
        tpd = limits.get("tpd") or "unknown"
        rpd = limits.get("rpd") or "—"
        rpm = limits.get("rpm", "?")
        thinking = p.get("thinking_mode", {}).get("supported", False)
        key_set = "✅" if os.environ.get(p.get("env_var", "")) else "❌ key missing"
        exhausted = "⚠️  near limit" if _provider_warned(p) else ""
        print(
            f"  {key_set} [{phase}] pri={p.get('priority','?')}  "
            f"{p['name']:<20} {model:<30} "
            f"rpm={rpm}  tpd={tpd}  rpd={rpd}  thinking={thinking}  {exhausted}"
        )
    print()


def _cmd_usage():
    usage = _load_usage()
    print(f"\n  Daily usage — {usage['date']}\n")
    if not usage["providers"]:
        print("  No usage recorded today.\n")
        return
    cfg = _load_config()
    id_to_provider = {p["id"]: p for p in cfg["providers"]}
    for pid, bucket in usage["providers"].items():
        p = id_to_provider.get(pid, {})
        tpd = (p.get("limits") or {}).get("tpd") or 0
        rpd = (p.get("limits") or {}).get("rpd") or 0
        tok_pct = f"({bucket['tokens']/tpd*100:.0f}%)" if tpd else ""
        req_pct = f"({bucket['requests']/rpd*100:.0f}%)" if rpd else ""
        name = p.get("name", pid)
        print(
            f"  {name:<22} tokens: {bucket['tokens']:>7} {tok_pct:<8} "
            f"requests: {bucket['requests']:>5} {req_pct}"
        )
    print()


def _cmd_test(phase: int):
    print(f"\n  Testing phase {phase} with providers.yml routing...\n")
    reaction, raw = call_ollama(
        model="auto",
        system=_TEST_SYSTEM,
        user=_TEST_USER,
        verbose=True,
        phase=phase,
    )
    if reaction is None:
        print(f"\n  FAIL: {raw}")
    else:
        print(f"\n  verdict   : {reaction.verdict.value}")
        print(f"  reaction  : {reaction.reaction}")
        print(f"  confidence: {reaction.confidence}")
    print()


def _cli():
    parser = argparse.ArgumentParser(
        description="GEP cloud_client — provider manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python cloud_client.py --list\n"
            "  python cloud_client.py --usage\n"
            "  python cloud_client.py --reset-usage\n"
            "  python cloud_client.py --test --phase 1\n"
            "  python cloud_client.py --test --phase 2\n"
        ),
    )
    parser.add_argument("--list",        action="store_true", help="List all configured providers")
    parser.add_argument("--usage",       action="store_true", help="Show today's token usage")
    parser.add_argument("--reset-usage", action="store_true", help="Reset daily usage counters")
    parser.add_argument("--test",        action="store_true", help="Run a test call")
    parser.add_argument("--phase",       type=int, default=2, help="Phase for --test (1 or 2)")
    args = parser.parse_args()

    if args.list:
        _cmd_list()
    elif args.usage:
        _cmd_usage()
    elif args.reset_usage:
        _usage_path().unlink(missing_ok=True)
        print("  Usage counters reset.\n")
    elif args.test:
        _cmd_test(args.phase)
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()

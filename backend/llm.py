"""
llm.py — Unified LLM backend wrapper (§10).

Consolidated from the repeated three-way branch in the original scripts
(file_reasoning.py, file_ranking.py, element_reasoning.py, element_ranking.py, FL.py).

Supports: anthropic | openai | gemini
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from pathlib import Path

from dotenv import load_dotenv

from config import (
    LLM_MAX_RETRIES,
    LLM_RETRY_BASE_SECONDS,
    LLM_RETRY_MAX_SECONDS,
)

logger = logging.getLogger(__name__)

# Load credentials from backend/.env when present.  Real secret values remain
# local because .env is ignored by Git; process environment variables still
# take precedence over this file.
load_dotenv(Path(__file__).with_name(".env"))


# Substrings that mark a failure as worth retrying: a quota or rate limit the
# caller can wait out, or a transient server-side error. Anything else (a bad
# model name, a malformed request, a missing key) fails immediately — retrying
# those just multiplies the same error.
_RETRYABLE_MARKERS = (
    "429",
    "resource_exhausted",
    "rate limit",
    "ratelimit",
    "quota",
    "too many requests",
    "500",
    "502",
    "503",
    "504",
    "overloaded",
    "unavailable",
    "deadline exceeded",
    "timeout",
)


def _is_retryable(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    if "invalid" in message and "api key" in message:
        return False
    return any(marker in message for marker in _RETRYABLE_MARKERS)


def _retry_delay_from(exc: Exception, attempt: int) -> float:
    """Prefer the provider's own retry hint, else exponential backoff with jitter."""
    hint = re.search(r"retrydelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", str(exc), re.IGNORECASE)
    if hint:
        return min(float(hint.group(1)) + 1.0, LLM_RETRY_MAX_SECONDS)
    backoff = LLM_RETRY_BASE_SECONDS * (2 ** attempt)
    # Jitter keeps a pool of parallel workers from retrying in lockstep and
    # tripping the same per-minute quota all over again.
    return min(backoff, LLM_RETRY_MAX_SECONDS) * random.uniform(0.8, 1.2)


def call_llm(
    prompt: str,
    model: str,
    backend: str,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> str:
    """
    Call the specified LLM backend with a single user prompt.

    Args:
        prompt:      The full prompt string to send.
        model:       Model identifier (e.g. "claude-sonnet-4-5", "gpt-4o", "gemini-2.5-pro").
        backend:     One of "anthropic", "openai", "gemini".
        max_tokens:  Maximum tokens in the response.
        temperature: Sampling temperature (0.0 = deterministic).

    Returns:
        The model's response text as a plain string.

    Raises:
        ValueError:  For an unknown backend string.
        Any exception from the underlying SDK is propagated — callers must wrap in try/except.
    """
    last_error: Exception | None = None

    for attempt in range(LLM_MAX_RETRIES):
        try:
            return _call_once(prompt, model, backend, max_tokens, temperature)
        except ValueError:
            raise  # Unknown backend — a caller bug, not a transient failure.
        except Exception as exc:
            last_error = exc
            if attempt == LLM_MAX_RETRIES - 1 or not _is_retryable(exc):
                raise
            delay = _retry_delay_from(exc, attempt)
            logger.warning(
                "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                LLM_MAX_RETRIES,
                delay,
                exc,
            )
            time.sleep(delay)

    raise last_error  # Unreachable; the loop always returns or raises.


def _call_once(
    prompt: str,
    model: str,
    backend: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """One attempt against one provider. Wrapped by call_llm's retry loop."""
    # Anthropic Claude: uses the messages API with a single user message.
    if backend == "anthropic":
        import anthropic  # type: ignore[import]

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    # OpenAI GPT: uses chat completions API with a single user message.
    elif backend == "openai":
        import openai  # type: ignore[import]

        client = openai.OpenAI()
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content

    # Google Gemini: supports both API key and Vertex AI authentication.
    elif backend == "gemini":
        from google import genai  # type: ignore[import]

        # Try API key first, fall back to Vertex AI if not set.
        google_api_key = os.environ.get("GOOGLE_API_KEY")
        if google_api_key:
            client = genai.Client(api_key=google_api_key)
        else:
            project = os.environ.get("VERTEXAI_PROJECT", "")
            location = os.environ.get("VERTEXAI_LOCATION", "us-central1")
            client = genai.Client(vertexai=True, project=project, location=location)
        response = client.models.generate_content(model=model, contents=prompt)
        return response.text

    else:
        raise ValueError(
            f"Unknown backend: {backend!r}. Must be one of 'anthropic', 'openai', 'gemini'."
        )

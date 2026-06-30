"""
Reasoning LLM — provider-swappable text completion for the insight layer.

One `complete(system, prompt)` call, dispatched by settings.llm_provider:
  - anthropic  → Messages API (Claude)
  - deepseek   → OpenAI-compatible Chat Completions (DeepSeek)

Both go over httpx (no SDK coupling), return plain text, and the caller parses
JSON. `active_model()` reports which model produced an insight, for provenance.
"""

from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings


def active_model() -> str:
    """The model id of the currently-selected provider (stored on insights)."""
    if settings.llm_provider == "deepseek":
        return settings.deepseek_model
    return settings.anthropic_model


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def _anthropic(system: str, prompt: str, max_tokens: int) -> str:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.anthropic_base_url}/messages",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.anthropic_model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def _deepseek(system: str, prompt: str, max_tokens: int) -> str:
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not configured")
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.deepseek_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            json={
                "model": settings.deepseek_model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def complete(system: str, prompt: str, max_tokens: int = 2048) -> str:
    """Run a single completion against the configured provider; return raw text."""
    provider = settings.llm_provider
    if provider == "anthropic":
        return await _anthropic(system, prompt, max_tokens)
    if provider == "deepseek":
        return await _deepseek(system, prompt, max_tokens)
    raise RuntimeError(f"Unsupported llm_provider: {provider!r}")

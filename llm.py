"""LLM providers: OpenAI-compatible, Anthropic, or local Ollama.

All calls are user-triggered and explicit (Ask / Draft buttons). The system
prompt pins the assistant to *grounded* answers with citations like [1], [2],
based only on the provided, redacted thread excerpts.
"""
from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_SYSTEM_PROMPT = """You are an email assistant. Answer ONLY from the provided email excerpts.
Rules:
- Cite the excerpts you use as [1], [2], ... matching the numbering shown to the user.
- If the excerpts do not contain the answer, say so explicitly — never invent facts.
- Prefer concrete details: dates, amounts, names, and who agreed to what.
- Be concise. Bullet points when listing."""


class LLMError(Exception):
    pass


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class LLM:
    def __init__(self, provider: str, model: str, api_key: str = "",
                 base_url: str = "") -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def chat(self, system: str, user: str, max_tokens: int = 900) -> str:
        if self.provider == "openai":
            return self._openai(system, user, max_tokens)
        if self.provider == "anthropic":
            return self._anthropic(system, user, max_tokens)
        if self.provider == "ollama":
            return self._ollama(system, user, max_tokens)
        raise LLMError(f"Unknown provider: {self.provider}")

    # -- providers -----------------------------------------------------------
    def _openai(self, system: str, user: str, max_tokens: int) -> str:
        base = self.base_url or "https://api.openai.com/v1"
        data = _post_json(
            f"{base}/chat/completions",
            {"Authorization": f"Bearer {self.api_key}"},
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
        )
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"OpenAI response malformed: {e}") from e

    def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            {
                "model": self.model,
                "system": system,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": user}],
                "temperature": 0.2,
            },
        )
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Anthropic response malformed: {e}") from e

    def _ollama(self, system: str, user: str, max_tokens: int) -> str:
        base = self.base_url or "http://localhost:11434"
        data = _post_json(
            f"{base}/api/chat",
            {},
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": 0.2},
            },
            timeout=120,
        )
        try:
            return data["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Ollama response malformed: {e}") from e


def build_user_prompt(question: str, excerpts: list[tuple[str, str]]) -> str:
    """excerpts: list of (label, text). Label is the [n] the user will see."""
    parts = ["Email excerpts (numbered for citation):", ""]
    for label, text in excerpts:
        parts.append(f"--- {label} ---")
        parts.append(text.strip())
        parts.append("")
    parts.append(f"Question: {question}")
    return "\n".join(parts)

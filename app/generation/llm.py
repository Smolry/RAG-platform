from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import yaml

log = logging.getLogger(__name__)


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class LLMClient(Protocol):
    def complete(self, messages: list[dict], model: str, temperature: float = 0.0) -> LLMResult: ...


class LiteLLMClient:
    """Provider-agnostic via LiteLLM: set llm_model to any LiteLLM model string."""

    def complete(self, messages: list[dict], model: str, temperature: float = 0.0) -> LLMResult:
        import litellm  # lazy

        r = litellm.completion(model=model, messages=messages, temperature=temperature)
        u = r.usage
        return LLMResult(r.choices[0].message.content or "", int(u.prompt_tokens), int(u.completion_tokens), model)


class RecordingLLM:
    """Offline deterministic stand-in for tests: records every prompt, answers citing [1]. NOT for results."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict], model: str, temperature: float = 0.0) -> LLMResult:
        self.calls.append(messages)
        n_in = sum(len(m["content"].split()) for m in messages)
        return LLMResult("Answer based on the provided context [1].", n_in, 8, model)

    def all_prompt_text(self) -> str:
        return "\n".join(m["content"] for call in self.calls for m in call)

    def all_context_text(self) -> str:
        """Only the retrieved-context part of each prompt (excludes the user's own question text)."""
        parts = []
        for call in self.calls:
            for m in call:
                if m["role"] == "user" and "Context:\n" in m["content"]:
                    parts.append(m["content"].split("\n\nQuestion:", 1)[0])
        return "\n".join(parts)


class PriceTable:
    """Config-driven price table (configs/models.yaml). Never hardcode prices in app code."""

    def __init__(self, prices: dict[str, dict[str, float]]):
        self.prices = prices

    @classmethod
    def from_yaml(cls, path: str) -> "PriceTable":
        try:
            with open(path) as f:
                return cls((yaml.safe_load(f) or {}).get("prices", {}))
        except FileNotFoundError:
            log.warning("price table %s not found; costs will be 0.0", path)
            return cls({})

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        p = self.prices.get(model)
        if p is None:
            log.warning("no price for model %s; cost recorded as 0.0", model)
            return 0.0
        return (input_tokens * p["input_per_1m"] + output_tokens * p["output_per_1m"]) / 1_000_000

# LLM provider 工厂：CLI/API 只选 provider，不碰各家 wire 细节。
from __future__ import annotations

import os
from typing import Literal, Optional

from app.agents.llm import LLMClient
from app.agents.llm_http import DEFAULT_MAX_OUTPUT_TOKENS
from app.agents.llm_openai import DEFAULT_MODEL as OPENAI_DEFAULT_MODEL
from app.agents.llm_openai import OpenAILLM
from app.agents.llm_openai_chat_compat import OpenAIChatCompatLLM

LLMProvider = Literal["openai", "openai_chat_compat"]


def normalize_provider(provider: Optional[str]) -> LLMProvider:
    raw = provider or os.environ.get("TRACE_LLM_PROVIDER") or "openai"
    normalized = raw.strip().lower().replace("-", "_")
    aliases = {
        "openai_responses": "openai",
        "chat_compat": "openai_chat_compat",
        "openai_compatible": "openai_chat_compat",
        "openai_compatible_chat": "openai_chat_compat",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"openai", "openai_chat_compat"}:
        raise ValueError("未知 LLM provider；当前支持: openai, openai_chat_compat")
    return normalized  # type: ignore[return-value]


def resolve_model(provider: Optional[str], model: Optional[str]) -> Optional[str]:
    normalized = normalize_provider(provider)
    if normalized == "openai":
        return model or os.environ.get("TRACE_LLM_MODEL") or os.environ.get("OPENAI_MODEL") or OPENAI_DEFAULT_MODEL
    return (
        model
        or os.environ.get("TRACE_LLM_MODEL")
        or os.environ.get("OPENAI_CHAT_COMPAT_MODEL")
        or os.environ.get("OPENAI_MODEL")
    )


def create_llm(
    provider: Optional[str],
    model: Optional[str] = None,
    *,
    api_key: Optional[str] = None,
    temperature: Optional[float] = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    base_url: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    client: Optional[object] = None,
) -> LLMClient:
    normalized = normalize_provider(provider)
    if normalized == "openai":
        return OpenAILLM(
            model,
            api_key=api_key,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            base_url=base_url,
            reasoning_effort=reasoning_effort,
            client=client,
        )
    if reasoning_effort:
        raise ValueError("openai_chat_compat 不支持 reasoning_effort；不同兼容端点字段差异太大")
    return OpenAIChatCompatLLM(
        model,
        api_key=api_key,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        base_url=base_url,
        client=client,
    )

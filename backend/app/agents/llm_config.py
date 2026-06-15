from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agents.llm_http import DEFAULT_MAX_OUTPUT_TOKENS
from app.agents.llm_factory import normalize_provider, resolve_model


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_config_path() -> Path:
    return _repo_root() / "backend" / "llm.config.json"


def _config_path() -> Path:
    override = os.environ.get("TRACE_LLM_CONFIG_FILE")
    if override:
        return Path(override).expanduser().resolve()
    return _default_config_path()


@dataclass(frozen=True)
class LLMRuntimeConfig:
    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float | None = None
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    reasoning_effort: str | None = None


def load_llm_config() -> LLMRuntimeConfig | None:
    path = _config_path()
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"LLM 配置文件必须是 JSON object: {path}")

    provider_raw = (_as_str(raw.get("provider")) or "openai_chat_compat").lower().replace("-", "_")
    provider = "mock" if provider_raw == "mock" else normalize_provider(provider_raw)
    model = _as_str(raw.get("model")) or ("mock-1" if provider == "mock" else resolve_model(provider, None))
    if not model:
        raise ValueError(f"LLM 配置缺少 model: {path}")

    return LLMRuntimeConfig(
        provider=provider,
        model=model,
        api_key=_as_str(raw.get("api_key")),
        base_url=_as_str(raw.get("base_url")),
        temperature=_as_float(raw.get("temperature")),
        max_output_tokens=_as_int(raw.get("max_output_tokens"), DEFAULT_MAX_OUTPUT_TOKENS),
        reasoning_effort=_as_str(raw.get("reasoning_effort")),
    )


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)

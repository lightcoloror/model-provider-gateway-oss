"""Reviewed provider and model presets.

The catalog is deliberately static at runtime. Updating it is a reviewed source
change; provider discovery never mutates an active profile or consent.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

CATALOG_SCHEMA = "model_provider_gateway.catalog.v1"
CAPABILITY_NAMES = ("text", "vision", "asr", "ocr", "tts", "embedding")


def _model(
    preset_id: str,
    model: str,
    capabilities: tuple[str, ...],
    *,
    protocol: str = "chat_completions",
    provider_options: dict[str, Any] | None = None,
    evidence: str,
) -> dict[str, Any]:
    return {
        "preset_id": preset_id,
        "model": model,
        "capabilities": list(capabilities),
        "protocol": protocol,
        "provider_options": dict(provider_options or {}),
        "review_status": "reviewed",
        "evidence": evidence,
    }


_PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "label": "OpenAI",
        "litellm_provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "destination": "api.openai.com",
        "auth_env": "OPENAI_API_KEY",
        "protocols": ["responses", "chat_completions"],
        "models": [
            _model(
                "openai-gpt-5-6-luna",
                "gpt-5.6-luna",
                ("text", "vision"),
                protocol="responses",
                evidence="OpenAI model catalog reviewed 2026-08-05",
            )
        ],
    },
    "anthropic": {
        "label": "Anthropic",
        "litellm_provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "destination": "api.anthropic.com",
        "auth_env": "ANTHROPIC_API_KEY",
        "protocols": ["messages"],
        "models": [
            _model(
                "anthropic-claude-sonnet-5",
                "claude-sonnet-5",
                ("text", "vision"),
                protocol="messages",
                evidence="Anthropic model catalog reviewed 2026-08-05",
            )
        ],
    },
    "gemini": {
        "label": "Google Gemini",
        "litellm_provider": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "destination": "generativelanguage.googleapis.com",
        "auth_env": "GEMINI_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "gemini-3-6-flash",
                "gemini-3.6-flash",
                ("text", "vision"),
                evidence="VKP verified provider prefill contract 2026-07-22",
            ),
            _model(
                "gemini-3-5-flash-lite",
                "gemini-3.5-flash-lite",
                ("text", "vision"),
                evidence="VKP verified provider prefill contract 2026-07-22",
            ),
        ],
    },
    "openrouter": {
        "label": "OpenRouter",
        "litellm_provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "destination": "openrouter.ai",
        "auth_env": "OPENROUTER_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "openrouter-openai-gpt-5-6-luna",
                "openai/gpt-5.6-luna",
                ("text", "vision"),
                evidence="OpenRouter explicit-model API documentation reviewed 2026-08-05",
            )
        ],
        "note": "The gateway never uses OpenRouter auto-router or :free aliases as a fallback.",
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "litellm_provider": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "destination": "api.siliconflow.cn",
        "auth_env": "SILICONFLOW_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "siliconflow-qwen3-5-4b",
                "Qwen/Qwen3.5-4B",
                ("text",),
                evidence="VKP verified provider prefill contract 2026-07-18",
            ),
            _model(
                "siliconflow-glm-4-5v",
                "zai-org/GLM-4.5V",
                ("vision",),
                evidence="VKP verified account catalog and execution evidence",
            ),
        ],
    },
    "modelscope": {
        "label": "ModelScope API-Inference",
        "litellm_provider": "openai",
        "base_url": "https://api-inference.modelscope.cn/v1",
        "destination": "api-inference.modelscope.cn",
        "auth_env": "MODELSCOPE_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "modelscope-glm-5-2",
                "ZhipuAI/GLM-5.2",
                ("text",),
                evidence="VKP verified provider prefill contract 2026-07-18",
            ),
            _model(
                "modelscope-deepseek-v4-pro",
                "deepseek-ai/DeepSeek-V4-Pro",
                ("text",),
                evidence="VKP verified provider prefill contract 2026-07-18",
            ),
        ],
    },
    "ark": {
        "label": "Volcengine Ark standard model API",
        "litellm_provider": "volcengine",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "destination": "ark.cn-beijing.volces.com",
        "auth_env": "ARK_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "ark-deepseek-v4-pro",
                "deepseek-v4-pro-260425",
                ("text",),
                evidence="VKP verified Ark standard API contract 2026-07-18",
            ),
            _model(
                "ark-deepseek-v4-flash",
                "deepseek-v4-flash-260425",
                ("text",),
                evidence="VKP verified Ark standard API contract 2026-07-18",
            ),
            _model(
                "ark-glm-5-2",
                "glm-5-2-260617",
                ("text",),
                evidence="VKP verified Ark standard API contract 2026-07-18",
            ),
        ],
    },
    "groq": {
        "label": "Groq",
        "litellm_provider": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "destination": "api.groq.com",
        "auth_env": "GROQ_API_KEY",
        "protocols": ["chat_completions", "audio_transcriptions"],
        "models": [
            _model(
                "groq-qwen3-6-27b",
                "qwen/qwen3.6-27b",
                ("text", "vision"),
                provider_options={"reasoning_effort": "none", "reasoning_format": "hidden"},
                evidence="VKP verified provider prefill contract 2026-07-18",
            ),
            _model(
                "groq-whisper-large-v3-turbo",
                "whisper-large-v3-turbo",
                ("asr",),
                protocol="audio_transcriptions",
                evidence="VKP verified provider prefill contract 2026-07-18",
            ),
        ],
    },
    "mistral": {
        "label": "Mistral",
        "litellm_provider": "mistral",
        "base_url": "https://api.mistral.ai/v1",
        "destination": "api.mistral.ai",
        "auth_env": "MISTRAL_API_KEY",
        "protocols": ["chat_completions", "audio_transcriptions", "ocr"],
        "models": [
            _model(
                "mistral-voxtral-mini-2602",
                "voxtral-mini-2602",
                ("asr",),
                protocol="audio_transcriptions",
                evidence="VKP verified provider prefill contract 2026-07-18",
            ),
            _model(
                "mistral-ocr-4-0",
                "mistral-ocr-4-0",
                ("ocr",),
                protocol="ocr",
                evidence="VKP verified provider prefill contract 2026-07-18",
            ),
        ],
    },
    "deepseek": {
        "label": "DeepSeek 官方 API",
        "litellm_provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "destination": "api.deepseek.com",
        "auth_env": "DEEPSEEK_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "deepseek-chat",
                "deepseek-chat",
                ("text",),
                evidence="LiteLLM 1.81.7 deepseek adapter and Cherry Studio fixed provider registry",
            ),
        ],
    },
    "zhipu": {
        "label": "智谱 BigModel",
        "litellm_provider": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "destination": "open.bigmodel.cn",
        "auth_env": "ZHIPU_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "zhipu-glm-5-2",
                "glm-5.2",
                ("text",),
                evidence="Cherry Studio provider-registry de7da764 provider/model endpoint matrix",
            ),
        ],
    },
    "moonshot": {
        "label": "Moonshot / Kimi 官方 API",
        "litellm_provider": "moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "destination": "api.moonshot.cn",
        "auth_env": "MOONSHOT_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "moonshot-kimi-k3",
                "kimi-k3",
                ("text",),
                evidence="Cherry Studio provider-registry de7da764 moonshot provider and Kimi K3 mapping",
            ),
        ],
    },
    "dashscope": {
        "label": "阿里云百炼 DashScope",
        "litellm_provider": "dashscope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "destination": "dashscope.aliyuncs.com",
        "auth_env": "DASHSCOPE_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "dashscope-qwen3-6-flash",
                "qwen3.6-flash",
                ("text", "vision"),
                evidence="LiteLLM 1.81.7 DashScope adapter and Cherry Studio endpoint matrix",
            ),
        ],
    },
    "together": {
        "label": "Together AI",
        "litellm_provider": "together_ai",
        "base_url": "https://api.together.xyz/v1",
        "destination": "api.together.xyz",
        "auth_env": "TOGETHERAI_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "together-llama-3-3-70b-turbo",
                "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                ("text",),
                evidence="LiteLLM 1.81.7 together_ai model metadata and Cherry Studio registry",
            ),
        ],
    },
    "fireworks": {
        "label": "Fireworks AI",
        "litellm_provider": "fireworks_ai",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "destination": "api.fireworks.ai",
        "auth_env": "FIREWORKS_AI_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "fireworks-llama-v3p3-70b-instruct",
                "accounts/fireworks/models/llama-v3p3-70b-instruct",
                ("text",),
                evidence="LiteLLM 1.81.7 fireworks_ai model metadata and Cherry Studio registry",
            ),
        ],
    },
    "perplexity": {
        "label": "Perplexity API",
        "litellm_provider": "perplexity",
        "base_url": "https://api.perplexity.ai",
        "destination": "api.perplexity.ai",
        "auth_env": "PERPLEXITYAI_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "perplexity-sonar",
                "sonar",
                ("text",),
                evidence="LiteLLM 1.81.7 perplexity model metadata and Cherry Studio registry",
            ),
        ],
    },
    "cerebras": {
        "label": "Cerebras Inference",
        "litellm_provider": "cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "destination": "api.cerebras.ai",
        "auth_env": "CEREBRAS_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "cerebras-llama3-1-8b",
                "llama3.1-8b",
                ("text",),
                evidence="LiteLLM 1.81.7 cerebras model metadata and Cherry Studio registry",
            ),
        ],
    },
    "xai": {
        "label": "xAI",
        "litellm_provider": "xai",
        "base_url": "https://api.x.ai/v1",
        "destination": "api.x.ai",
        "auth_env": "XAI_API_KEY",
        "protocols": ["chat_completions"],
        "models": [
            _model(
                "xai-grok-4-latest",
                "grok-4-latest",
                ("text",),
                evidence="LiteLLM 1.81.7 xAI model metadata and Cherry Studio registry",
            ),
        ],
    },
    "azure-openai": {
        "label": "Azure OpenAI",
        "litellm_provider": "azure",
        "base_url": "https://{resource}.openai.azure.com",
        "destination": "{resource}.openai.azure.com",
        "auth_env": "AZURE_API_KEY",
        "protocols": ["chat_completions", "responses"],
        "models": [],
    },
    "vertex-ai": {
        "label": "Google Vertex AI",
        "litellm_provider": "vertex_ai",
        "base_url": "https://{location}-aiplatform.googleapis.com",
        "destination": "{location}-aiplatform.googleapis.com",
        "auth_env": "GOOGLE_APPLICATION_CREDENTIALS",
        "protocols": ["chat_completions"],
        "models": [],
    },
    "aws-bedrock": {
        "label": "AWS Bedrock",
        "litellm_provider": "bedrock",
        "base_url": "https://bedrock-runtime.{region}.amazonaws.com",
        "destination": "bedrock-runtime.{region}.amazonaws.com",
        "auth_env": "AWS_ACCESS_KEY_ID",
        "protocols": ["messages"],
        "models": [],
    },
}


_GROUPS = {
    "international_official": "国际官方",
    "china_official": "国内官方",
    "aggregator": "聚合平台",
    "cloud_platform": "云平台（需额外字段）",
}

_PROVIDER_METADATA: dict[str, dict[str, Any]] = {
    "openai": {"group_id": "international_official", "search_terms": ["GPT", "OpenAI"]},
    "anthropic": {"group_id": "international_official", "search_terms": ["Claude", "Anthropic"]},
    "gemini": {"group_id": "international_official", "search_terms": ["Google", "Gemini"]},
    "groq": {"group_id": "international_official", "search_terms": ["Groq", "Whisper", "ASR"]},
    "mistral": {"group_id": "international_official", "search_terms": ["Mistral", "Voxtral", "OCR"]},
    "deepseek": {"group_id": "china_official", "search_terms": ["DeepSeek", "深度求索"]},
    "zhipu": {"group_id": "china_official", "search_terms": ["智谱", "BigModel", "GLM"]},
    "moonshot": {"group_id": "china_official", "search_terms": ["Moonshot", "Kimi", "月之暗面"]},
    "dashscope": {
        "group_id": "china_official",
        "search_terms": ["阿里云", "百炼", "DashScope", "Qwen", "通义千问"],
        "optional_fields": [
            {
                "field_id": "workspace_host",
                "label": "Workspace Host（可选）",
                "input_type": "hostname",
                "placeholder": "ws-xxxx.cn-beijing.maas.aliyuncs.com",
                "help": "仅企业 Workspace 使用；留空即使用百炼公共地址。只允许受审核的北京 MaaS Host。",
            }
        ],
        "local_overlay": {
            "field_id": "workspace_host",
            "allowed_pattern": r"^[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com$",
            "base_url_suffix": "/compatible-mode/v1",
        },
    },
    "ark": {"group_id": "china_official", "search_terms": ["火山引擎", "方舟", "Ark", "Volcengine"]},
    "siliconflow": {"group_id": "aggregator", "search_terms": ["硅基流动", "SiliconFlow"]},
    "modelscope": {"group_id": "aggregator", "search_terms": ["魔搭", "ModelScope", "API-Inference"]},
    "openrouter": {"group_id": "aggregator", "search_terms": ["OpenRouter", "聚合"]},
    "together": {"group_id": "aggregator", "search_terms": ["Together AI", "聚合"]},
    "fireworks": {"group_id": "aggregator", "search_terms": ["Fireworks AI", "聚合"]},
    "perplexity": {"group_id": "international_official", "search_terms": ["Perplexity", "Sonar", "搜索"]},
    "cerebras": {"group_id": "international_official", "search_terms": ["Cerebras", "Inference"]},
    "xai": {"group_id": "international_official", "search_terms": ["xAI", "Grok"]},
    "azure-openai": {
        "group_id": "cloud_platform",
        "search_terms": ["Azure", "Microsoft", "Deployment"],
        "setup_mode": "minimal_fields",
        "onboarding_status": "blocked_template_not_executable",
        "required_fields": [
            {"field_id": "resource_endpoint", "label": "Resource Endpoint", "input_type": "url"},
            {"field_id": "deployment_name", "label": "Deployment Name", "input_type": "text"},
            {"field_id": "api_version", "label": "API Version", "input_type": "text"},
        ],
        "note": "资源 Host 与 Deployment 属于租户配置；在形成受审核本机 overlay 前不生成可执行 Profile。",
    },
    "vertex-ai": {
        "group_id": "cloud_platform",
        "search_terms": ["Vertex", "Google Cloud", "GCP"],
        "setup_mode": "minimal_fields",
        "onboarding_status": "blocked_template_not_executable",
        "required_fields": [
            {"field_id": "project_id", "label": "GCP Project ID", "input_type": "text"},
            {"field_id": "location", "label": "Location", "input_type": "text"},
            {"field_id": "credential_reference", "label": "ADC / Service Account 引用", "input_type": "reference"},
        ],
        "note": "Vertex 使用项目、区域和 ADC/Service Account，不是单一 API Key。",
    },
    "aws-bedrock": {
        "group_id": "cloud_platform",
        "search_terms": ["AWS", "Amazon", "Bedrock"],
        "setup_mode": "minimal_fields",
        "onboarding_status": "blocked_template_not_executable",
        "required_fields": [
            {"field_id": "region", "label": "AWS Region", "input_type": "text"},
            {"field_id": "credential_profile", "label": "AWS Credential Profile", "input_type": "reference"},
            {"field_id": "model_id", "label": "Bedrock Model ID", "input_type": "text"},
        ],
        "note": "Bedrock 使用区域与 AWS 凭据链，不是单一 API Key。",
    },
}


def _capability_status(provider: dict[str, Any]) -> dict[str, str]:
    reviewed = {capability for model in provider["models"] for capability in model["capabilities"]}
    return {capability: "reviewed" if capability in reviewed else "blocked" for capability in CAPABILITY_NAMES}


for _provider_id, _provider in _PROVIDERS.items():
    _metadata = _PROVIDER_METADATA.get(_provider_id, {})
    _provider.update(deepcopy(_metadata))
    _provider.setdefault("group_id", "international_official")
    _provider["group_label"] = _GROUPS[_provider["group_id"]]
    _provider.setdefault("search_terms", [_provider["label"], _provider_id])
    _provider.setdefault("setup_mode", "api_key_only")
    _provider.setdefault("onboarding_status", "ready")
    _provider.setdefault("required_fields", [])
    _provider.setdefault("optional_fields", [])
    _provider.setdefault("advanced_fields_locked", True)
    _provider["capability_status"] = _capability_status(_provider)
    _provider["evidence_sources"] = [
        {
            "project": "LiteLLM",
            "version": "1.81.7",
            "role": "provider_protocol_adapter",
        },
        {
            "project": "Cherry Studio provider-registry",
            "commit": "de7da76426bd2a1322410d8c4051387a989fd3a6",
            "role": "provider_endpoint_and_model_registry_reference",
        },
    ]


def resolve_provider_fields(provider_id: str, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve a reviewed local overlay without permitting an arbitrary URL."""

    provider = catalog_show(provider_id)
    if fields is not None and not isinstance(fields, dict):
        raise ValueError("provider_fields must be an object")
    supplied = dict(fields or {})
    permitted = {row["field_id"] for row in provider["required_fields"] + provider["optional_fields"]}
    unknown = sorted(set(supplied) - permitted)
    if unknown:
        raise ValueError(f"provider fields are not reviewed: {', '.join(unknown)}")
    if provider["onboarding_status"] != "ready":
        raise ValueError(f"provider onboarding is not executable: {provider['onboarding_status']}")
    missing = [
        row["field_id"] for row in provider["required_fields"] if not str(supplied.get(row["field_id"]) or "").strip()
    ]
    if missing:
        raise ValueError(f"provider fields are required: {', '.join(missing)}")
    normalized = {key: str(value).strip().lower() for key, value in supplied.items() if str(value).strip()}
    base_url = provider["base_url"]
    destination = provider["destination"]
    overlay = provider.get("local_overlay")
    if overlay and overlay["field_id"] in normalized:
        host = normalized[overlay["field_id"]]
        if not re.fullmatch(overlay["allowed_pattern"], host):
            raise ValueError("workspace_host is outside the reviewed Alibaba Cloud MaaS hostname scope")
        base_url = f"https://{host}{overlay['base_url_suffix']}"
        destination = host
    return {"provider_fields": normalized, "base_url": base_url, "destination": destination}


def catalog_list() -> list[dict[str, Any]]:
    return [
        {
            "provider_id": provider_id,
            "label": row["label"],
            "destination": row["destination"],
            "preset_count": len(row["models"]),
            "status": "reviewed" if row["onboarding_status"] == "ready" else "blocked",
            "group_id": row["group_id"],
            "group_label": row["group_label"],
            "setup_mode": row["setup_mode"],
            "onboarding_status": row["onboarding_status"],
        }
        for provider_id, row in sorted(_PROVIDERS.items())
    ]


def catalog_show(provider_id: str) -> dict[str, Any]:
    try:
        row = deepcopy(_PROVIDERS[provider_id])
    except KeyError as exc:
        raise ValueError(f"unknown provider_id: {provider_id}") from exc
    return {"schema": CATALOG_SCHEMA, "provider_id": provider_id, **row}


def find_model_preset(preset_id: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    for provider_id, provider in _PROVIDERS.items():
        for model in provider["models"]:
            if model["preset_id"] == preset_id:
                return provider_id, deepcopy(provider), deepcopy(model)
    raise ValueError(f"unknown or unreviewed model preset: {preset_id}")


def provider_ids() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDERS))

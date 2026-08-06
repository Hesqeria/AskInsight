"""Multi-LLM adapter layer: supports 12+ LLM service providers

Usage:
  from app.agent.llm_provider import build_llm
  llm = build_llm()  # auto-selected from app_config
"""
from langchain_openai import ChatOpenAI

from app.conf.app_config import app_config


# Supported LLM list
SUPPORTED_PROVIDERS = {
    "deepseek": {"base_url_env": "LLM_BASE_URL", "model_prefix": "deepseek"},
    "openai": {"base_url": "https://api.openai.com/v1", "model_prefix": "gpt"},
    "kimi": {"base_url": "https://api.moonshot.cn/v1", "model_prefix": "moonshot"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model_prefix": "qwen"},
    "glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model_prefix": "glm"},
    "kaiqian": {"base_url_env": "LLM_BASE_URL", "model_prefix": "deepseek"},
    "minimax": {"base_url": "https://api.minimax.chat/v1", "model_prefix": "abab"},
    "baidu": {"base_url": "https://qianfan.baidubce.com/v2", "model_prefix": "ernie"},
    "huoshan": {"base_url": "https://ark.cn-beijing.volces.com/api/v3", "model_prefix": "doubao"},
    "hunyuan": {"base_url": "https://api.hunyuan.cloud.tencent.com/v1", "model_prefix": "hunyuan"},
    "spark": {"base_url": "https://spark-api-open.xf-yun.com/v1", "model_prefix": "general"},
    "custom": {"base_url_env": "LLM_BASE_URL", "model_prefix": ""},
}


def build_llm(provider: str = None, model: str = None, api_key: str = None,
              base_url: str = None, temperature: float = 0, max_tokens: int = 4096):
    """Build LLM client

    Args:
        provider: provider identifier (deepseek/openai/kimi/qwen/glm/...); if None, read from config
        model: model name; if None, read from config
        api_key: API Key; if None, read from config
        base_url: service URL; if None, inferred from provider
        temperature: temperature (default 0)
        max_tokens: max tokens (default 4096)

    Returns:
        ChatOpenAI instance (all LLMs go through the OpenAI-compatible protocol)
    """
    # Read defaults from config
    cfg = app_config.llm
    provider = provider or "custom"
    model = model or cfg.model_name
    api_key = api_key or cfg.api_key
    base_url = base_url or cfg.base_url

    # Override base_url based on provider
    p_config = SUPPORTED_PROVIDERS.get(provider, {})
    if "base_url" in p_config:
        base_url = p_config["base_url"]

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def list_supported_providers() -> dict:
    """Return the supported LLM list"""
    return SUPPORTED_PROVIDERS


# Global LLM instance (loaded from app_config)
llm = build_llm()

if __name__ == "__main__":
    for p, config in SUPPORTED_PROVIDERS.items():
        print(f"  {p:12} | {config}")

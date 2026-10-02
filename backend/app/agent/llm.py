import os

from langchain_openai import ChatOpenAI

from app.conf.app_config import app_config


def build_llm():
    """Build the LLM client, reusing the OpenAI-compatible proxy.
    Note: models such as deepseek-v4-pro / GLM-5.2 run in reasoning mode,
    where reasoning_tokens are consumed first; max_tokens must be large enough (default 4096) to output content.
    """
    return ChatOpenAI(
        model=app_config.llm.model_name,
        request_timeout=int(__import__('os').getenv('LLM_REQUEST_TIMEOUT', '90')),
        api_key=app_config.llm.api_key,
        base_url=app_config.llm.base_url,
        temperature=0,
        max_tokens=4096,
    )


def build_fast_llm():
    """Lightweight model for hot-path auxiliary calls (keyword expansion,
    table filtering, intent fallback). Reasoning-capable strong models
    (GLM-5.3) spend 10-16s per such call; a flash-tier model answers in
    ~1-2s with equivalent JSON quality. Falls back to the main model
    when LLM_FAST_MODEL_NAME is unset."""
    model = os.getenv("LLM_FAST_MODEL_NAME", "")
    if not model:
        return build_llm()
    return ChatOpenAI(
        model=model,
        request_timeout=int(__import__('os').getenv('LLM_REQUEST_TIMEOUT', '90')),
        api_key=app_config.llm.api_key,
        base_url=app_config.llm.base_url,
        temperature=0,
        max_tokens=int(os.getenv("LLM_FAST_MAX_TOKENS", "2048")),
    )


llm = build_llm()
fast_llm = build_fast_llm()

if __name__ == "__main__":
    for chunk in llm.stream("Hello, who are you?"):
        print(chunk.content, end="")

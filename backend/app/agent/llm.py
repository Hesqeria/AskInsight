from langchain_openai import ChatOpenAI

from app.conf.app_config import app_config


def build_llm():
    """Build the LLM client, reusing the OpenAI-compatible proxy.
    Note: models such as deepseek-v4-pro / GLM-5.2 run in reasoning mode,
    where reasoning_tokens are consumed first; max_tokens must be large enough (default 4096) to output content.
    """
    return ChatOpenAI(
        model=app_config.llm.model_name,
        api_key=app_config.llm.api_key,
        base_url=app_config.llm.base_url,
        temperature=0,
        max_tokens=4096,
    )


llm = build_llm()

if __name__ == "__main__":
    for chunk in llm.stream("Hello, who are you?"):
        print(chunk.content, end="")

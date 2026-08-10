"""P3-01: LLM Multi-model router with retry, fallback, and cost tracking."""
import time
import json
import httpx
from app.conf.app_config import app_config

TASK_MODEL_PRIORITY = {
    "intent":       ["deepseek-v4-flash", "deepseek-v4-pro"],
    "sql_simple":   ["deepseek-v4-flash", "deepseek-v4-pro"],
    "sql_complex":  ["deepseek-v4-pro", "kimi-k2.7-code"],
    "etl_gen":      ["deepseek-v4-pro", "kimi-k2.7-code"],
    "root_cause":   ["deepseek-v4-pro", "GLM-5.2"],
    "free_form":    ["deepseek-v4-pro", "kimi-k2.7-code"],
}


class LLMRouter:
    def __init__(self):
        self.base_url = app_config.llm.base_url.rstrip("/")
        self.api_key = app_config.llm.api_key
        self._unavailable = {}

    def complete(self, messages, task_type="free_form", model_hint=None,
                 temperature=0.3, max_tokens=2048, timeout=30):
        candidates = self._select(task_type, model_hint)
        last_err = None
        for model_name in candidates:
            if self._is_unavailable(model_name):
                continue
            try:
                return self._call(model_name, messages, temperature, max_tokens, timeout)
            except Exception as e:
                last_err = e
                self._mark_unavailable(model_name)
        raise RuntimeError(f"All LLM models failed: {last_err}")

    def _call(self, model, messages, temperature, max_tokens, timeout):
        body = {"model": model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        t0 = time.time()
        resp = httpx.post(f"{self.base_url}/chat/completions",
                          content=json.dumps(body), headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]["message"]
        usage = data.get("usage", {})
        return {"content": choice["content"], "model_used": data.get("model", model),
                "latency_ms": int((time.time() - t0) * 1000),
                "tokens_in": usage.get("prompt_tokens", 0),
                "tokens_out": usage.get("completion_tokens", 0)}

    def _select(self, task_type, hint):
        defaults = TASK_MODEL_PRIORITY.get(task_type, ["deepseek-v4-pro"])
        if hint:
            return [hint] + [m for m in defaults if m != hint]
        return defaults

    def _is_unavailable(self, name):
        ts = self._unavailable.get(name, 0)
        if ts and time.time() < ts:
            return True
        if ts:
            del self._unavailable[name]
        return False

    def _mark_unavailable(self, name):
        self._unavailable[name] = time.time() + 600


_router = None

def get_llm() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router

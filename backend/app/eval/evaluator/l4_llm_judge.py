"""L4 LLM-as-Judge：语义等价判断

L3 EX 失败时，调 LLM 判断两个 SQL 是否语义等价。
使用 DeepSeek API（OpenAI 兼容）。

输出：
{
    "equivalent": bool,
    "confidence": 0.0-1.0,
    "reason": str
}
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


JUDGE_PROMPT = """你是 SQL 语义等价判断专家。

任务：判断【候选 SQL】与【金标准 SQL】是否在业务语义上等价（即回答相同的业务问题）。

【金标准 SQL】
{gold_sql}

【候选 SQL】
{pred_sql}

【金标准执行结果】（前 5 行）
{gold_preview}

【候选执行结果】（前 5 行）
{pred_preview}

【候选执行报错（如有）】
{pred_error}

判断规则（视为等价的情形）：
1. 字段顺序不同但结果集业务含义相同
2. 子查询 vs JOIN（同义改写）
3. WHERE 条件等价改写
4. 聚合函数等价（SUM/COUNT/AVG 替换）
5. JOIN 顺序不同
6. 表别名不同

视为不等价的情形：
1. 候选 SQL 执行报错且非包装问题
2. 结果集行数差异 > 50%
3. 缺少关键 WHERE 过滤条件
4. 缺少关键 GROUP BY 分组

严格按以下 JSON 格式输出（不要有任何额外文字）：
{{"equivalent": true/false, "confidence": 0.0, "reason": "简短中文解释"}}
"""


class LLMJudge:
    """LLM-as-Judge 语义等价判断"""

    def __init__(
        self,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
        threshold: float = 0.7,
        timeout: int = 30,
    ):
        # 默认走 DeepSeek（与 backend 一致）
        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
        self.model = model or os.getenv("LLM_MODEL_NAME", "deepseek-chat")
        self.threshold = threshold
        self.timeout = timeout
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    def judge(
        self,
        gold_sql: str,
        pred_sql: str,
        gold_result: list = None,
        pred_result: list = None,
        pred_error: str = "",
    ) -> dict:
        """判断 SQL 是否语义等价

        Returns:
            {
                "equivalent": bool,
                "confidence": float,
                "reason": str,
                "raw": str (LLM 原始输出)
            }
        """
        if not pred_sql or not pred_sql.strip():
            return {"equivalent": False, "confidence": 1.0, "reason": "候选 SQL 为空", "raw": ""}

        # 默认失败场景：执行报错直接判不等价
        if pred_error and "nonexistent" in pred_error.lower():
            return {"equivalent": False, "confidence": 1.0,
                    "reason": f"候选 SQL 执行报错: {pred_error[:80]}", "raw": ""}

        # 构造 prompt
        gold_preview = self._format_preview(gold_result or [], 5)
        pred_preview = self._format_preview(pred_result or [], 5)
        prompt = JUDGE_PROMPT.format(
            gold_sql=gold_sql[:1500],
            pred_sql=pred_sql[:1500],
            gold_preview=gold_preview,
            pred_preview=pred_preview,
            pred_error=pred_error[:200] or "（无）",
        )

        try:
            client = self._get_client()
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是严谨的 SQL 评审专家，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content
            parsed = self._parse_json(raw)
            # 应用置信度阈值
            if parsed["equivalent"] and parsed["confidence"] < self.threshold:
                parsed["reason"] += f" (置信度 {parsed['confidence']} < 阈值 {self.threshold}，不通过)"
                parsed["equivalent"] = False
            parsed["raw"] = raw
            return parsed
        except Exception as e:
            logger.warning(f"LLM Judge 调用失败: {e}")
            return {
                "equivalent": False,
                "confidence": 0.0,
                "reason": f"LLM 调用异常: {str(e)[:150]}",
                "raw": "",
            }

    @staticmethod
    def _format_preview(rows: list, n: int) -> str:
        if not rows:
            return "（空结果）"
        preview = rows[:n]
        try:
            lines = [" | ".join(str(c) for c in r) for r in preview]
            return "\n".join(lines)
        except Exception:
            return str(preview)

    @staticmethod
    def _parse_json(text: str) -> dict:
        """解析 LLM 输出，容错处理"""
        # 去掉 markdown 包装
        s = text.strip()
        if s.startswith("```"):
            s = s.split("\n", 1)[-1]
            if s.endswith("```"):
                s = s[:-3]
            s = s.strip()

        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            # 兜底：用正则提取
            import re
            m = re.search(r'\{[^{}]*"equivalent"[^{}]*\}', text, re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except Exception:
                    return {"equivalent": False, "confidence": 0.0, "reason": "JSON 解析失败"}
            else:
                return {"equivalent": False, "confidence": 0.0, "reason": "无法解析 LLM 输出"}

        return {
            "equivalent": bool(obj.get("equivalent", False)),
            "confidence": float(obj.get("confidence", 0.0)),
            "reason": str(obj.get("reason", ""))[:200],
        }

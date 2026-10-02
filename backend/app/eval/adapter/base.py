"""Agent Adapter 抽象基类

不同实现：
- DirectLangGraphAdapter：白盒直调 LangGraph（最准）
- HTTPAdapter：调 /api/query（黑盒，端到端）
- MockSQLAdapter：直接返回 gold SQL（用于框架测试）
"""
import abc
from dataclasses import dataclass


@dataclass
class AgentResponse:
    """Agent 调用返回"""
    pred_sql: str
    intent: str = ""           # query / chat / other
    latency_ms: int = 0
    error: str = ""
    raw_output: str = ""       # 原始输出（调试用）


class BaseAgentAdapter(abc.ABC):
    """Agent 适配器抽象"""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abc.abstractmethod
    async def ask(self, question: str) -> AgentResponse:
        """向 Agent 提问，返回 SQL"""
        ...

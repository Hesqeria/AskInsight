"""评测模块配置"""
import os
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class EvalConfig:
    # Doris 连接（与 backend 共用）
    doris_host: str = os.getenv("DORIS_HOST", "192.168.137.52")
    doris_port: int = int(os.getenv("DORIS_PORT", "9030"))
    doris_user: str = os.getenv("DORIS_USER", "root")
    doris_password: str = os.getenv("DORIS_PASSWORD", "")
    doris_meta_db: str = "data_agent"  # eval_questions/eval_runs 所在库
    doris_data_db: str = "dw"          # 被测数仓

    # 评测参数
    question_limit: int | None = None   # None=全部，N=前 N 题（烟雾测试）
    concurrency: int = 5                # 并发调用 Agent
    timeout_sec: int = 60               # 单题超时
    run_id: str = ""                    # 运行 ID（不填自动生成）

    # 模型/Prompt 版本（用于追踪）
    model_name: str = "qwen3-vl-27b"
    prompt_version: str = "v3"

    # 结果容差（FLOAT/DECIMAL 比较）
    float_rtol: float = 1e-6

    # 评测报告输出
    report_dir: Path = field(default_factory=lambda: Path(__file__).parent / "runs")

    # Mock 当前日期（让 add_extra_context 节点看到固定日期，与 dw 数据冻结日对齐）
    mock_date: str | None = None  # 'YYYY-MM-DD' 或 None

    # L4 LLM-as-Judge（可选，后续实现）
    enable_llm_judge: bool = False
    llm_judge_threshold: float = 0.7


def get_default_config() -> EvalConfig:
    return EvalConfig()

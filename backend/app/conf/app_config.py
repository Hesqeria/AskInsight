from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv  # noqa: E402
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).parents[3]  # AskInsight/
_BACKEND_ROOT = _Path(__file__).parents[2]  # backend/
# Root .env is the canonical source (docker-compose); backend/.env is a local-dev fallback.
# Existing environment variables always win (no override).
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv(_BACKEND_ROOT / ".env")
from omegaconf import OmegaConf  # noqa: E402


@dataclass
class File:
    enable: bool
    level: str
    path: str
    rotation: str
    retention: str


@dataclass
class Console:
    enable: bool
    level: str


@dataclass
class LoggingConfig:
    file: File
    console: Console


@dataclass
class DorisConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class MilvusConfig:
    host: str
    port: int
    user: str
    password: str
    embedding_size: int
    column_collection: str
    metric_collection: str


@dataclass
class EmbeddingConfig:
    api_base: str
    api_key: str
    model: str


@dataclass
class RerankConfig:
    api_key: str
    model: str = "gte-rerank-v2"
    enabled: bool = True
    top_n: int = 50
    score_threshold: float = 0.1
    cache_ttl: int = 3600


@dataclass
class LLMRetryConfig:
    # M5: 重试策略声明化 - per-provider 声明,执行在 llm_adapter
    retries: int = 2
    timeout: int = 90
    backoff_base: float = 2.0
    backoff_jitter: bool = True


@dataclass
class LLMConfig:
    model_name: str
    api_key: str
    base_url: str
    retry: LLMRetryConfig = field(default_factory=LLMRetryConfig)




@dataclass
class PGConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class MySQLConfig:
    host: str
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = ""


@dataclass
class RedisConfig:
    host: str
    port: int = 6379
    password: str = ""


@dataclass
class SupersetConfig:
    url: str
    username: str = "admin"
    password: str = ""

@dataclass
class ApprovalRoleRule:
    # 免审:该角色触发的 PII 门直接放行
    auto_approve: bool = False
    # 免审上限:PII 违规列数 <= 该值时放行(-1 = 不限)
    max_pii_without_approval: int = -1


@dataclass
class ApprovalConfig:
    # ask = 建工单等人工;never = 严格模式,PII 门直接拒绝(fail-closed)
    mode: str = "ask"
    # pending 工单超过 N 小时自动 expired(0 = 关闭过期)
    expire_hours: int = 72
    # 角色规则;未列出的角色默认全审(ask)
    roles: dict = field(default_factory=dict)

    def rule_for(self, role: str) -> ApprovalRoleRule:
        raw = self.roles.get(role) or {}
        return ApprovalRoleRule(
            auto_approve=bool(raw.get("auto_approve", False)),
            max_pii_without_approval=int(
                raw.get("max_pii_without_approval", -1)),
        )


@dataclass
class AppConfig:
    logging: LoggingConfig
    doris: DorisConfig
    milvus: MilvusConfig
    embedding: EmbeddingConfig
    rerank: RerankConfig
    llm: LLMConfig
    pg: PGConfig
    mysql: MySQLConfig
    redis: RedisConfig
    superset: SupersetConfig
    approval: ApprovalConfig = field(default_factory=ApprovalConfig)


_config_file = Path(__file__).parents[2] / "conf" / "app_config.yaml"
_context = OmegaConf.load(_config_file)
# Parse ${oc.env:XXX}
_resolved = OmegaConf.create(OmegaConf.to_yaml(_context, resolve=True))
_schema = OmegaConf.structured(AppConfig)
app_config: AppConfig = OmegaConf.to_object(OmegaConf.merge(_schema, _resolved))

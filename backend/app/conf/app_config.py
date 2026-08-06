from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv  # noqa: E402
from pathlib import Path as _Path
load_dotenv(_Path(__file__).parents[2] / ".env")  # noqa: E402
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
class LLMConfig:
    model_name: str
    api_key: str
    base_url: str




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
class AppConfig:
    logging: LoggingConfig
    doris: DorisConfig
    milvus: MilvusConfig
    embedding: EmbeddingConfig
    llm: LLMConfig
    pg: PGConfig
    mysql: MySQLConfig


_config_file = Path(__file__).parents[2] / "conf" / "app_config.yaml"
_context = OmegaConf.load(_config_file)
# Parse ${oc.env:XXX}
_resolved = OmegaConf.create(OmegaConf.to_yaml(_context, resolve=True))
_schema = OmegaConf.structured(AppConfig)
app_config: AppConfig = OmegaConf.to_object(OmegaConf.merge(_schema, _resolved))

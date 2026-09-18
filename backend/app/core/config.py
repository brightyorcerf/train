from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Pinned into every case (§12) and printed on the report. Bump this whenever a provider adapter
# changes what it returns or how it identifies an edge — a pin that never moves proves nothing.
# 2026-09-18: tokentx row identity now includes the contract + per-page ordinal (was "day6").
ADAPTER_VERSION = "2026-09-18-esplora+etherscan-v2"

# Repo-root .env for host-side scripts; in containers compose injects env and the file is absent.
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    etherscan_api_key: str = ""
    mempool_base_url: str = "https://mempool.space/api"
    esplora_base_url: str = "https://blockstream.info/api"

    postgres_db: str = "vasp"
    postgres_user: str = "vasp"
    postgres_password: str = "vasp"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password123"

    redis_url: str = "redis://redis:6379/0"


settings = Settings()

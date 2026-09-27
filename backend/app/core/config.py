import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Pinned into every case (§12) and printed on the report. Bump this whenever a provider adapter
# changes what it returns or how it identifies an edge — a pin that never moves proves nothing.
# 2026-09-18: tokentx row identity now includes the contract + per-page ordinal (was "day6").
# 2026-09-27: + TronGrid USDT (TRC-20) adapter; EVM/BTC responses unchanged.
ADAPTER_VERSION = "2026-09-27-esplora+etherscan-v2+trongrid"

_CHECKOUT = Path(__file__).resolve().parents[3]
# Containers mount labels/ and vendor/ under REPO_ROOT (compose); host scripts use the checkout.
REPO = Path(os.environ.get("REPO_ROOT") or _CHECKOUT)
# Repo-root .env for host-side scripts; in containers compose injects env and the file is absent.
_ENV_FILE = _CHECKOUT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    etherscan_api_key: str = ""
    trongrid_api_key: str = ""   # optional; keyless works at the paced rate (providers/tron.py)
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

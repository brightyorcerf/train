from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-root .env for host-side scripts; in containers compose injects env and the file is absent.
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    etherscan_api_key: str = ""
    blockchair_api_key: str = ""
    trongrid_api_key: str = ""
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

    @property
    def postgres_dsn(self) -> str:
        return (f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}")


settings = Settings()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    postgres_db: str = "civic_graph"
    postgres_user: str = "civic"
    postgres_password: str = "civic"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    opendata_dortmund_base_url: str = "https://open-data.dortmund.de/api/explore/v2.1"
    # OParl disabled by Dortmund in production (Oct 2024). None = skip OParl flows.
    oparl_endpoint_url: str | None = None
    overpass_api_url: str = "https://overpass-api.de/api/interpreter"
    brightsky_api_url: str = "https://api.brightsky.dev"

    anthropic_api_key: str = ""
    bot_user_agent: str = "civic-graph/0.1 (research; contact@example.com)"

    prefect_api_url: str = "http://localhost:4200/api"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} "
            f"dbname={self.postgres_db} user={self.postgres_user} "
            f"password={self.postgres_password}"
        )


settings = Settings()

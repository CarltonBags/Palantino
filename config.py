from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    postgres_db: str = "civic_graph"
    postgres_user: str = "civic"
    postgres_password: str = "civic"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Cloud Postgres (Supabase / Neon / RDS …): if set, an asyncpg DSN URL like
    # postgresql://user:pass@host:5432/db — used instead of the fields above.
    database_url: str | None = None
    # Set 0 when connecting through a transaction pooler (Supabase :6543 / Neon
    # pooled / PgBouncer) — asyncpg prepared statements break otherwise.
    db_statement_cache_size: int = 100

    opendata_dortmund_base_url: str = "https://open-data.dortmund.de/api/explore/v2.1"
    # OParl disabled by Dortmund in production (Oct 2024). None = skip OParl flows.
    oparl_endpoint_url: str | None = None
    overpass_api_url: str = "https://overpass-api.de/api/interpreter"
    brightsky_api_url: str = "https://api.brightsky.dev"

    # Reasoning LLM — swappable provider (anthropic | deepseek). DeepSeek uses an
    # OpenAI-compatible API, so switching is a config flag + a key.
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_fast_model: str = "claude-haiku-4-5-20251001"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"  # main reasoning / answers
    deepseek_fast_model: str = "deepseek-v4-flash"  # cheap pre-pass (query intent)

    # Embeddings (semantic layer). Provider is swappable; OpenAI is the default.
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-large"
    # text-embedding-3-large is natively 3072-dim, but pgvector's HNSW index caps
    # at 2000 dims, so we request OpenAI's shortened 1536-dim output (still
    # indexable, keeps 3-large quality). Must match the migration's vector(N).
    embedding_dimensions: int = 1536

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

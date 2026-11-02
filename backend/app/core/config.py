from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Everything except the self-hosted endpoints is optional."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- hosted providers (all have free tiers) ---
    GROQ_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None
    HUGGINGFACE_API_KEY: str | None = None
    MISTRAL_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None
    TOGETHER_API_KEY: str | None = None
    CEREBRAS_API_KEY: str | None = None
    # Optional: lifts Pollinations' anonymous 1-req/45s limit. Works without it.
    POLLINATIONS_API_KEY: str | None = None

    # --- self-hosted, keyless ---
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    STABLE_DIFFUSION_URL: str = "http://localhost:7860"
    COMFYUI_BASE_URL: str = "http://localhost:8188"

    # --- app ---
    APP_NAME: str = "Universal AI SaaS"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    API_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    REQUEST_TIMEOUT_SECONDS: float = 120.0
    GENERATION_TIMEOUT_SECONDS: float = 600.0
    MAX_PROMPT_CHARS: int = 32000
    FRONTEND_DIR: str = "../frontend"
    SKILLS_INDEX_URL: str = (
        "https://raw.githubusercontent.com/sickn33/agentic-awesome-skills/main/index.json"
    )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

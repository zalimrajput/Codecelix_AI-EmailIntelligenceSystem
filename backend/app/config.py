from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    supabase_url: str = ""
    supabase_anon_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_ANON_KEY", "ANON_KEY"),
    )
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    google_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_CLIENT_ID", "GMAIL_CLIENT_ID"),
    )
    google_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_CLIENT_SECRET", "GMAIL_CLIENT_SECRET"),
    )
    google_redirect_uri: str = Field(
        default="http://localhost:8000/integrations/gmail/callback",
        validation_alias=AliasChoices("GOOGLE_REDIRECT_URI", "GMAIL_REDIRECT_URI"),
    )
    gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GEMINI_API_KEY", "GEMENI_API_KEY", "GEMENAI_API_KEY"
        ),
    )
    gemini_chat_model: str = "gemini-flash-latest"
    gemini_embedding_model: str = "gemini-embedding-001"
    app_secret_key: str = ""
    frontend_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"
    gmail_token_fernet_key: str = ""
    max_import_bytes: int = 10_000_000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def ai_provider(self) -> str:
        if self.gemini_api_key:
            return "gemini"
        if self.openai_api_key:
            return "openai"
        return ""

    @property
    def chat_model(self) -> str:
        return self.gemini_chat_model if self.ai_provider == "gemini" else self.openai_chat_model

    @property
    def embedding_model(self) -> str:
        return (
            self.gemini_embedding_model
            if self.ai_provider == "gemini"
            else self.openai_embedding_model
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Configuration evaluated only at capability boundaries."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Optional integrations remain unset until their capability is invoked."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, validation_alias="OPENAI_BASE_URL")
    llm_model: str | None = Field(default=None, validation_alias="LLM_MODEL")
    object_storage_endpoint: str | None = Field(
        default=None, validation_alias="BA_OBJECT_STORAGE_ENDPOINT"
    )
    object_storage_access_key: str | None = Field(
        default=None, validation_alias="BA_OBJECT_STORAGE_ACCESS_KEY"
    )
    object_storage_secret_key: str | None = Field(
        default=None, validation_alias="BA_OBJECT_STORAGE_SECRET_KEY"
    )

    object_storage_bucket: str | None = Field(
        default=None, validation_alias="BA_OBJECT_STORAGE_BUCKET"
    )
    object_storage_region: str | None = Field(
        default=None, validation_alias="BA_OBJECT_STORAGE_REGION"
    )
    object_storage_force_path_style: bool = Field(
        default=False, validation_alias="BA_OBJECT_STORAGE_FORCE_PATH_STYLE"
    )
    @field_validator(
        "database_url",
        "openai_api_key",
        "openai_base_url",
        "llm_model",
        "object_storage_endpoint",
        "object_storage_access_key",
        "object_storage_secret_key",
        "object_storage_bucket",
        "object_storage_region",
        mode="before",
    )
    @classmethod
    def blank_values_are_unset(cls, value: str | None) -> str | None:
        return value.strip() or None if isinstance(value, str) else value

    def require_database(self) -> str:
        if not self.database_url:
            raise ValueError("DATABASE_URL must be configured for this capability")
        return self.database_url

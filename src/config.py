"""Application configuration loaded from config/config.toml."""

import functools
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.toml"


class DatabaseSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    name: str = "certificate_validation"
    user: str = "postgres"
    password: str = ""

    @property
    def url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.user}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class PaperlessSettings(BaseSettings):
    base_url: str = "http://localhost:8000"
    token: str = ""
    tag_name: str = "Führungszeugnis"
    poll_interval_seconds: int = 300


class HitobitoSettings(BaseSettings):
    base_url: str = "http://localhost:3000"
    token: str = ""
    group_id: int = 1
    efz_qualification_kind_label: str = "Erweitertes Führungszeugnis"
    stamm_name: str = ""
    dioezese_name: str = ""


class EmailSettings(BaseSettings):
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    sender: str = "noreply@example.com"
    alert_recipients: str = ""

    @property
    def alert_recipients_list(self) -> list[str]:
        return [r.strip() for r in self.alert_recipients.split(",") if r.strip()]


class Settings(BaseSettings):
    # env_nested_delimiter enables flat env vars like DATABASE__HOST to override
    # nested settings fields, matching the keys emitted by the Helm ConfigMap.
    model_config = SettingsConfigDict(
        toml_file=str(CONFIG_PATH),
        env_nested_delimiter="__",
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    paperless: PaperlessSettings = Field(default_factory=PaperlessSettings)
    hitobito: HitobitoSettings = Field(default_factory=HitobitoSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    log_level: str = "INFO"
    certificate_max_age_years: int = 5
    expiry_alert_days: int = 120

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls),
        )


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

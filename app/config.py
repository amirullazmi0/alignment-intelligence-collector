from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse, urlunparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


class CollectorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    collector_topic: str = Field(default="", validation_alias="COLLECTOR_TOPIC")
    collector_keywords: str = Field(default="", validation_alias="COLLECTOR_KEYWORDS")
    collector_exclude_keywords: str = Field(
        default="", validation_alias="COLLECTOR_EXCLUDE_KEYWORDS"
    )
    jdihn_base_url: str = Field(default="https://jdihn.go.id", validation_alias="JDIHN_BASE_URL")
    alignment_api_base_url: str = Field(
        default="https://alignment-intelligence-nest.apps.arulize.com",
        validation_alias="ALIGNMENT_API_BASE_URL",
    )
    alignment_email: str = Field(default="", validation_alias="ALIGNMENT_EMAIL")
    alignment_password: SecretStr = Field(
        default=SecretStr(""), validation_alias="ALIGNMENT_PASSWORD"
    )
    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    download_dir: Path = Field(default=Path("downloads"), validation_alias="DOWNLOAD_DIR")
    request_delay_seconds: float = Field(
        default=2.0, ge=0.5, validation_alias="REQUEST_DELAY_SECONDS"
    )
    http_timeout_seconds: float = Field(default=30.0, gt=0, validation_alias="HTTP_TIMEOUT_SECONDS")
    max_uploads_per_run: int = Field(default=25, gt=0, validation_alias="MAX_UPLOADS_PER_RUN")
    max_pdf_bytes: int = Field(default=50 * 1024 * 1024, gt=0, validation_alias="MAX_PDF_BYTES")
    collector_internal_token: SecretStr = Field(
        default=SecretStr(""), validation_alias="COLLECTOR_INTERNAL_TOKEN"
    )
    collector_service_host: str = Field(
        default="127.0.0.1", validation_alias="COLLECTOR_SERVICE_HOST"
    )
    collector_service_port: int = Field(
        default=5003, gt=0, lt=65536, validation_alias="COLLECTOR_SERVICE_PORT"
    )
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_color: str = Field(default="auto", validation_alias="LOG_COLOR")

    @field_validator("collector_topic")
    @classmethod
    def validate_topic(cls, value: str) -> str:
        # Boleh kosong: deployment mode service memilih kementerian per run, sehingga tidak
        # punya tema tetap. Run tanpa kementerian tetap divalidasi di RunRequest.from_settings.
        return value.strip()

    @field_validator("jdihn_base_url", "alignment_api_base_url")
    @classmethod
    def validate_https_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if "://" not in normalized:
            normalized = f"https://{normalized}"
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL harus berupa HTTP(S) absolut")
        return normalized

    @model_validator(mode="after")
    def validate_source(self) -> CollectorSettings:
        parsed = urlparse(self.jdihn_base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            raise ValueError("JDIHN_BASE_URL wajib menggunakan HTTPS")
        if host != "jdihn.go.id" and not host.endswith(".jdihn.go.id"):
            raise ValueError("JDIHN_BASE_URL harus menggunakan domain jdihn.go.id")
        if host == "jdihn.go.id":
            self.jdihn_base_url = urlunparse(parsed._replace(netloc="www.jdihn.go.id"))
        return self

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("LOG_LEVEL harus DEBUG, INFO, WARNING, atau ERROR")
        return normalized

    @field_validator("log_color")
    @classmethod
    def validate_log_color(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"auto", "always", "never"}:
            raise ValueError("LOG_COLOR harus auto, always, atau never")
        return normalized

    def require_database_url(self) -> str:
        """URL database collector; schema-nya dimiliki Prisma, isinya dibaca asyncpg."""
        url = self.database_url.strip()
        if not url:
            raise ValueError("DATABASE_URL wajib diisi untuk menyimpan state collector")
        return url

    @field_validator("download_dir")
    @classmethod
    def validate_download_dir(cls, value: Path) -> Path:
        if value.is_absolute() or ".." in value.parts:
            raise ValueError("DOWNLOAD_DIR harus berupa path relatif di dalam repo")
        return value

    @property
    def keywords(self) -> tuple[str, ...]:
        return _split_csv(",".join((self.collector_topic, self.collector_keywords)))

    @property
    def exclude_keywords(self) -> tuple[str, ...]:
        return _split_csv(self.collector_exclude_keywords)

    def require_alignment_credentials(self) -> tuple[str, str]:
        email = self.alignment_email.strip()
        password = self.alignment_password.get_secret_value()
        if not email or not password:
            raise ValueError("ALIGNMENT_EMAIL dan ALIGNMENT_PASSWORD wajib untuk upload")
        return email, password

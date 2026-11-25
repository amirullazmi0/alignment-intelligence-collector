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

    # COLLECTOR_TOPIC dan COLLECTOR_KEYWORDS dihapus: sejak setiap run berasal dari
    # jadwal yang membawa kementerian dan keyword-nya sendiri, tema tingkat
    # environment tidak punya tempat lagi dan hanya jadi sumber kebingungan --
    # nilainya diam-diam ikut terbawa ke run yang sebenarnya bertema lain.
    collector_exclude_keywords: str = Field(
        default="", validation_alias="COLLECTOR_EXCLUDE_KEYWORDS"
    )
    jdihn_base_url: str = Field(default="https://jdihn.go.id", validation_alias="JDIHN_BASE_URL")
    # Sumber kedua: API metadata pasal.id untuk discovery, PDF dari situs resmi .go.id.
    # Token dibuat gratis di https://pasal.id/akun; kosong berarti sumber ini tidak bisa
    # dipakai, sementara jadwal JDIHN tetap berjalan.
    pasal_api_base_url: str = Field(
        default="https://pasal.id/api/v1", validation_alias="PASAL_API_BASE_URL"
    )
    pasal_api_token: SecretStr = Field(default=SecretStr(""), validation_alias="PASAL_API_TOKEN")
    # Batas API pasal.id 60 permintaan/menit per route; 1,5 detik menjaga jauh di bawahnya.
    pasal_request_delay_seconds: float = Field(
        default=1.5, ge=1.0, validation_alias="PASAL_REQUEST_DELAY_SECONDS"
    )
    # Sumber ketiga: katalog resmi peraturan.go.id, disusuri per jenis peraturan.
    peraturan_go_id_base_url: str = Field(
        default="https://peraturan.go.id", validation_alias="PERATURAN_GO_ID_BASE_URL"
    )
    peraturan_go_id_request_delay_seconds: float = Field(
        default=2.0, ge=1.0, validation_alias="PERATURAN_GO_ID_REQUEST_DELAY_SECONDS"
    )
    #: Halaman daftar (20 item) yang dibaca per jenis dalam satu run backfill.
    peraturan_go_id_max_pages: int = Field(
        default=10, ge=1, le=200, validation_alias="PERATURAN_GO_ID_MAX_PAGES"
    )
    # Cermin katalog peraturan.go.id yang selalu sinkron (job bawaan scheduler).
    pgo_sync_enabled: bool = Field(default=True, validation_alias="PGO_SYNC_ENABLED")
    #: Detail peraturan yang dicek (ulang) per jam: status, relasi, PDF.
    pgo_detail_per_hour: int = Field(
        default=200, ge=0, le=1500, validation_alias="PGO_DETAIL_PER_HOUR"
    )
    #: Peraturan yang teksnya dikirim ke Alignment per jam (dibatasi kapasitas OCR).
    pgo_text_per_hour: int = Field(default=25, ge=0, le=500, validation_alias="PGO_TEXT_PER_HOUR")
    #: Halaman daftar per jenis yang dibaca sapuan malam (seluruh katalog ±1.100 halaman per jenis).
    pgo_sweep_max_pages: int = Field(
        default=1200, ge=1, le=5000, validation_alias="PGO_SWEEP_MAX_PAGES"
    )
    # Tanpa nilai bawaan: alamat deployment tidak boleh menempel di kode. Default
    # yang menunjuk ke satu lingkungan membuat konfigurasi yang salah lolos diam-diam
    # lalu memanggil server yang keliru.
    alignment_api_base_url: str = Field(default="", validation_alias="ALIGNMENT_API_BASE_URL")
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

    @field_validator("jdihn_base_url", "alignment_api_base_url", "pasal_api_base_url")
    @classmethod
    def validate_https_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        # Kosong dibiarkan lolos di sini; yang mewajibkannya adalah pemakainya lewat
        # require_alignment_api_base_url(), supaya perintah yang tidak memerlukan
        # Alignment tetap bisa dijalankan.
        if not normalized:
            return ""
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
        parsed_pasal = urlparse(self.pasal_api_base_url)
        pasal_host = (parsed_pasal.hostname or "").lower()
        if parsed_pasal.scheme != "https":
            raise ValueError("PASAL_API_BASE_URL wajib menggunakan HTTPS")
        if pasal_host != "pasal.id" and not pasal_host.endswith(".pasal.id"):
            raise ValueError("PASAL_API_BASE_URL harus menggunakan domain pasal.id")
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
    def exclude_keywords(self) -> tuple[str, ...]:
        return _split_csv(self.collector_exclude_keywords)

    def require_alignment_api_base_url(self) -> str:
        """Alamat API Alignment Intelligence, wajib diisi lewat environment."""
        url = self.alignment_api_base_url.strip()
        if not url:
            raise ValueError(
                "ALIGNMENT_API_BASE_URL wajib diisi. Setel di .env untuk lokal, atau di "
                "dashboard CapRover untuk deployment -- berkas .env tidak ikut ke image."
            )
        return url

    def require_alignment_credentials(self) -> tuple[str, str]:
        email = self.alignment_email.strip()
        password = self.alignment_password.get_secret_value()
        if not email or not password:
            raise ValueError("ALIGNMENT_EMAIL dan ALIGNMENT_PASSWORD wajib untuk upload")
        return email, password

    def require_pasal_token(self) -> str:
        """Token API pasal.id, wajib untuk run bersumber pasal.id (juga dry run)."""
        token = self.pasal_api_token.get_secret_value().strip()
        if not token:
            raise ValueError(
                "PASAL_API_TOKEN wajib diisi untuk sumber pasal.id; buat token gratis di "
                "https://pasal.id/akun"
            )
        return token

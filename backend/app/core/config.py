from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (or a local .env file)."""

    # backend\.env is found whichever folder a command is run from (the Windows server keeps its settings there).
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", env_prefix="ERP_",
                                      extra="ignore")

    env: str = "local"  # local | test | staging | production
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/tayal_erp"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "change-me-in-env"  # must be overridden outside local/test
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    login_max_failures: int = 5
    login_lock_minutes: int = 15

    cors_origins: list[str] = ["http://localhost:3000"]

    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_bucket: str = "tayal-erp"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"

    business_timezone: str = "Asia/Kolkata"

    # WhatsApp Business Platform (Meta Cloud API). Secrets live in .env, never in the database.
    whatsapp_token: str | None = None  # permanent System User access token
    whatsapp_phone_number_id: str | None = None  # "Phone number ID" from WhatsApp Manager → API Setup
    whatsapp_api_version: str = "v22.0"
    # Orders sent by dealers to the business number (Meta webhook). verify_token: any secret you choose and also
    # type into Meta's webhook setup; app_secret: Meta app → Settings → Basic → App secret (checks each call is
    # really from Meta). company_code: which company the messages belong to (empty = the only active company).
    whatsapp_verify_token: str | None = None
    whatsapp_app_secret: str | None = None
    whatsapp_company_code: str | None = None

    # Reading WhatsApp order messages with Claude (Anthropic API). Without a key, a simple built-in reader is used.
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-haiku-5-5"

    # Timed jobs (automatic Tally sync, nightly backup) run inside the API process. Off in tests.
    scheduler_enabled: bool = True
    backup_dir: str = "~/TayalERP-backups"  # nightly pg_dump files go here; point it at another disk or a cloud folder
    backup_time: str = "23:30"  # local time; a missed night is caught up the next time the server is running
    backup_keep_days: int = 30
    # Phone app updates: the published APK and its version live here (outside git; kept across updates).
    app_release_dir: str = str(Path(__file__).resolve().parents[3] / "app-releases")
    pg_bin_dir: str | None = None  # folder with pg_dump (Windows: C:\\Program Files\\PostgreSQL\\16\\bin); empty = on the PATH

    def validate_for_runtime(self) -> None:
        if self.env in {"staging", "production"} and self.jwt_secret == "change-me-in-env":
            raise RuntimeError("ERP_JWT_SECRET must be set outside local/test environments")


@lru_cache
def get_settings() -> Settings:
    return Settings()

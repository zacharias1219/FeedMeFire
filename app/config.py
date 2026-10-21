"""Runtime configuration. Everything tunable is an environment variable (PRD §15.4, §21)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"  # dev | test | prod
    database_url: str = "sqlite:///./roastmyfeed.db"
    auto_create_tables: bool = True  # dev/test convenience; production uses Alembic
    app_secret: str = DEV_SECRET  # HMAC key (CSRF, hashes, IP hashing)
    encryption_keys: str = ""  # comma-separated Fernet keys; first encrypts, all decrypt (rotation)
    cookie_secure: bool | None = None  # default: True unless env is dev/test
    trust_proxy: bool = False  # honour X-Forwarded-For for the IP rate limit
    allowed_origins: str = ""  # extra comma-separated allowed Origin values
    public_base_url: str = ""

    # --- AI provider (PRD §9.1) ---
    provider: str = "fake"  # fake | gemini | sarvam
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    sarvam_api_key: str = ""
    sarvam_model: str = "sarvam-105b-conversations"
    sarvam_json_mode: bool = False  # response_format=json_object; off until verified against the live model
    provider_timeout_s: float = 60.0
    max_output_tokens: int = 1800
    max_output_tokens_indic: int = 6000  # Indic scripts tokenise far heavier than English
    temperature: float = 0.9
    pricing_version: str = "unset"
    price_input_per_mtok: float = 0.0  # USD per million input tokens
    price_output_per_mtok: float = 0.0
    safety_judge_enabled: bool = False  # optional second-pass provider moderation call

    # --- limits (PRD §7.3, §15.4) ---
    min_items: int = 10
    max_submitted_items: int = 100
    max_model_items: int = 60
    max_model_chars: int = 12000
    session_ttl_hours: int = 24
    sample_ttl_hours: int = 24
    report_ttl_hours: int = 24
    feedback_ttl_days: int = 30
    log_ttl_days: int = 14
    metrics_ttl_days: int = 90
    gen_per_session_24h: int = 3
    gen_per_ip_hour: int = 10
    global_daily_generation_limit: int = 2000
    global_daily_budget_usd: float = 0.0  # 0 = disabled
    queue_capacity: int = 200

    # --- worker (PRD §11.3, §17) ---
    worker_concurrency: int = 5
    lease_seconds: int = 45
    job_timeout_s: int = 90
    max_attempts: int = 3
    poll_interval_s: float = 1.0
    embedded_worker: bool = False

    # --- kill switches (PRD §21). Never gate deletion. ---
    generation_enabled: bool = True
    regeneration_enabled: bool = True
    disabled_providers: str = ""
    disabled_prompt_versions: str = ""
    youtube_connect_enabled: bool = False  # connected release is gated (PRD §16.5)

    # --- disclosures / versions (PRD §7.5) ---
    policy_version: str = "2026-10-1"
    provider_disclosure_version: str = "2026-10-1"
    support_contact: str = "support@roastmyfeed.example"
    metrics_token: str = ""

    @property
    def secure_cookie(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.env not in ("dev", "test")

    @property
    def provider_display_name(self) -> str:
        if self.provider == "gemini":
            return "Google (Gemini API)"
        if self.provider == "sarvam":
            return "Sarvam AI"
        return "Built-in demo generator (nothing leaves this server)"

    def csv(self, value: str) -> set[str]:
        return {v.strip() for v in value.split(",") if v.strip()}

    def validate_for_runtime(self) -> None:
        if self.youtube_connect_enabled:
            raise RuntimeError("YOUTUBE_CONNECT_ENABLED=true: the connected YouTube release is gated and not "
                               "implemented in this build (PRD §16.5). Leave it false.")
        if self.env == "prod":
            problems = []
            if self.app_secret == DEV_SECRET or len(self.app_secret) < 32:
                problems.append("APP_SECRET must be set to a random value of 32+ characters")
            if not self.encryption_keys:
                problems.append("ENCRYPTION_KEYS must be set (Fernet keys outside the database)")
            if not self.secure_cookie:
                problems.append("COOKIE_SECURE must be true in production")
            if self.provider == "gemini" and not self.gemini_api_key:
                problems.append("GEMINI_API_KEY is required when PROVIDER=gemini")
            if self.provider == "sarvam" and not self.sarvam_api_key:
                problems.append("SARVAM_API_KEY is required when PROVIDER=sarvam")
            if self.provider == "fake":
                problems.append("PROVIDER=fake is not allowed in production")
            if self.database_url.startswith("sqlite"):
                problems.append("DATABASE_URL must be PostgreSQL in production")
            if problems:
                raise RuntimeError("Invalid production configuration: " + "; ".join(problems))


settings = Settings()

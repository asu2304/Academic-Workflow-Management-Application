import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    secret_key: str = os.getenv("SECRET_KEY") or os.getenv("SESSION_SECRET") or secrets.token_hex(32)
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./grademate.db")
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", "./uploads")).resolve()
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "20"))

    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
    # AI_PROVIDER: "anthropic" | "gemini" | "mock" | "auto" (auto = first provider with a key, else mock)
    ai_provider: str = os.getenv("AI_PROVIDER", "auto").strip().lower()
    grader_model: str = os.getenv("GRADER_MODEL", "claude-opus-5-5")
    # Cheapest tier by default (testing budget). For best grading quality use gemini-pro-latest.
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
    gemini_fallback_model: str = os.getenv("GEMINI_FALLBACK_MODEL", "")  # empty = never switch models
    gemini_thinking: str = os.getenv("GEMINI_THINKING", "low").strip().lower()  # low | medium | high | "" (model default)

    def __init__(self) -> None:
        if _flag("AI_MOCK") or self.ai_provider == "mock":
            self.ai_provider = "mock"
        elif self.ai_provider == "auto":
            self.ai_provider = ("anthropic" if self.anthropic_api_key
                                else "gemini" if self.gemini_api_key else "mock")
        elif self.ai_provider == "anthropic" and not self.anthropic_api_key:
            self.ai_provider = "mock"
        elif self.ai_provider == "gemini" and not self.gemini_api_key:
            self.ai_provider = "mock"

    @property
    def ai_mock(self) -> bool:
        return self.ai_provider == "mock"

    @property
    def ai_label(self) -> str:
        return {"anthropic": f"Claude ({self.grader_model})", "gemini": f"Gemini ({self.gemini_model})",
                "mock": "mock grader"}[self.ai_provider]

    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("SMTP_FROM", "GradeMate <no-reply@example.com>")
    smtp_starttls: bool = _flag("SMTP_STARTTLS", "1")


settings = Settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)

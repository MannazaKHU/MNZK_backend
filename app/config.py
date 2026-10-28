"""Environment configuration; AI and maps can be enabled independently."""
import os
from urllib.parse import urlsplit
from dotenv import load_dotenv

load_dotenv()


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.lower().strip()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true/false or 1/0")


class Settings:
    def __init__(self):
        self.APP_ENV = os.getenv("APP_ENV", "development")
        self.HOST = os.getenv("HOST", "127.0.0.1")
        self.PORT = int(os.getenv("PORT", "8000"))
        self.DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.sqlite3")
        legacy_mock = env_bool("MOCK_MODE", True)
        self.AI_PROVIDER = os.getenv("AI_PROVIDER", "mock" if legacy_mock else "chatkhu")
        self.MAPS_PROVIDER = os.getenv("MAPS_PROVIDER", "mock" if legacy_mock else "naver")
        self.CHATKHU_API_KEY = os.getenv("CHATKHU_API_KEY", "")
        self.CHATKHU_BASE_URL = os.getenv("CHATKHU_BASE_URL", "").rstrip("/")
        self.AI_MODEL = os.getenv("AI_MODEL", "")
        self.AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "60"))
        self.AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "4096"))
        self.NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
        self.NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
        self.NAVER_SEARCH_URL = os.getenv("NAVER_SEARCH_URL", "https://naverapihub.apigw.ntruss.com/search/v1/local")
        self.NCP_MAPS_KEY_ID = os.getenv("NCP_MAPS_KEY_ID", "")
        self.NCP_MAPS_KEY = os.getenv("NCP_MAPS_KEY", "")
        self.NCP_MAPS_BASE = os.getenv("NCP_MAPS_BASE", "https://maps.apigw.ntruss.com")
        self.CORS_ORIGINS = [s.strip() for s in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
        ).split(",") if s.strip()]

    @property
    def MOCK_MODE(self):
        return self.AI_PROVIDER == "mock"

    def validate(self):
        if self.APP_ENV not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test or production")
        if self.AI_PROVIDER not in {"mock", "chatkhu"}:
            raise ValueError("AI_PROVIDER must be mock or chatkhu")
        if self.MAPS_PROVIDER not in {"mock", "naver"}:
            raise ValueError("MAPS_PROVIDER must be mock or naver")
        if self.AI_TIMEOUT_SECONDS <= 0 or self.AI_MAX_TOKENS < 1:
            raise ValueError("AI timeout and token limit must be positive")
        if self.AI_PROVIDER == "chatkhu":
            if not all((self.CHATKHU_API_KEY, self.CHATKHU_BASE_URL, self.AI_MODEL)):
                raise ValueError("Set CHATKHU_API_KEY, CHATKHU_BASE_URL and AI_MODEL")
            url = urlsplit(self.CHATKHU_BASE_URL)
            if url.scheme != "https" or not url.netloc or url.username or url.query or url.fragment:
                raise ValueError("CHATKHU_BASE_URL must be an HTTPS gateway base URL")
        if self.MAPS_PROVIDER == "naver" and not all((
            self.NAVER_CLIENT_ID, self.NAVER_CLIENT_SECRET, self.NCP_MAPS_KEY_ID, self.NCP_MAPS_KEY
        )):
            raise ValueError("Set NAVER_CLIENT_ID/SECRET and NCP_MAPS_KEY_ID/KEY")


settings = Settings()

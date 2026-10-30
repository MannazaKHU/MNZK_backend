"""Environment configuration; AI and maps can be enabled independently."""
import os
import math
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
        self.DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
        self.DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "0"))
        self.DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))
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
        # Blog Search shares the existing NAVER API HUB application credentials.
        self.ENABLE_BLOG_ENRICHMENT = env_bool("ENABLE_BLOG_ENRICHMENT", False)
        self.NAVER_BLOG_URL = os.getenv("NAVER_BLOG_URL", "https://naverapihub.apigw.ntruss.com/search/v1/blog")
        self.BLOG_MAX_CANDIDATES = int(os.getenv("BLOG_MAX_CANDIDATES", "5"))
        self.BLOG_QUERIES_PER_CANDIDATE = int(os.getenv("BLOG_QUERIES_PER_CANDIDATE", "2"))
        self.BLOG_RESULTS_PER_QUERY = int(os.getenv("BLOG_RESULTS_PER_QUERY", "5"))
        self.BLOG_MAX_REQUESTS = int(os.getenv("BLOG_MAX_REQUESTS", "10"))
        self.BLOG_CONCURRENCY = int(os.getenv("BLOG_CONCURRENCY", "3"))
        self.BLOG_TIMEOUT_SECONDS = float(os.getenv("BLOG_TIMEOUT_SECONDS", "5"))
        self.BLOG_EXTRACTION_TIMEOUT_SECONDS = float(os.getenv("BLOG_EXTRACTION_TIMEOUT_SECONDS", "20"))
        self.BLOG_MAX_AGE_DAYS = int(os.getenv("BLOG_MAX_AGE_DAYS", "180"))
        self.MAPS_TIMEOUT_SECONDS = float(os.getenv("MAPS_TIMEOUT_SECONDS", "10"))
        self.CORS_ORIGINS = [s.strip() for s in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
        ).split(",") if s.strip()]

    @property
    def MOCK_MODE(self):
        return self.AI_PROVIDER == "mock"

    def validate(self):
        if self.APP_ENV not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test or production")
        if self.APP_ENV == "production" and not self.DATABASE_URL.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            raise ValueError("Production requires a PostgreSQL DATABASE_URL")
        if self.DB_POOL_SIZE < 1 or self.DB_MAX_OVERFLOW < 0 or self.DB_CONNECT_TIMEOUT < 1:
            raise ValueError("Invalid database pool/timeout configuration")
        if self.AI_PROVIDER not in {"mock", "chatkhu"}:
            raise ValueError("AI_PROVIDER must be mock or chatkhu")
        if self.MAPS_PROVIDER not in {"mock", "naver"}:
            raise ValueError("MAPS_PROVIDER must be mock or naver")
        if not 0 < self.MAPS_TIMEOUT_SECONDS < float("inf"):
            raise ValueError("MAPS_TIMEOUT_SECONDS must be positive and finite")
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
        if self.MAPS_PROVIDER == "naver":
            for name in ("NAVER_SEARCH_URL", "NCP_MAPS_BASE"):
                url = urlsplit(getattr(self, name))
                if url.scheme != "https" or not url.netloc or url.username or url.query or url.fragment:
                    raise ValueError(f"{name} must be an HTTPS URL")
        if self.ENABLE_BLOG_ENRICHMENT:
            limits = ((self.BLOG_MAX_CANDIDATES, 0, 20), (self.BLOG_QUERIES_PER_CANDIDATE, 0, 10),
                      (self.BLOG_RESULTS_PER_QUERY, 1, 100), (self.BLOG_MAX_REQUESTS, 0, 100),
                      (self.BLOG_CONCURRENCY, 1, 10))
            if any(not lower <= value <= upper for value, lower, upper in limits) or self.BLOG_MAX_AGE_DAYS < 0:
                raise ValueError("Invalid BLOG_* request/processing limits")
            if any(not math.isfinite(t) or t <= 0 for t in (
                self.BLOG_TIMEOUT_SECONDS, self.BLOG_EXTRACTION_TIMEOUT_SECONDS
            )):
                raise ValueError("Blog timeouts must be positive and finite")
            url = urlsplit(self.NAVER_BLOG_URL)
            if url.scheme != "https" or not url.netloc or url.username or url.query or url.fragment:
                raise ValueError("NAVER_BLOG_URL must be an HTTPS URL")


settings = Settings()

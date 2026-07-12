"""Configuration for TG Video Search Bot"""
import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path, override=True)
else:
    load_dotenv()


class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # Search settings
    MAX_SEARCH_RESULTS: int = int(os.getenv("MAX_SEARCH_RESULTS", "30"))
    MAX_RESULTS_PER_SOURCE: int = int(os.getenv("MAX_RESULTS_PER_SOURCE", "15"))

    # Source URLs
    GUOCHAN_BASE_URL: str = "https://www.9191md.me"
    HANIME_BASE_URL: str = "https://hanime1.me"
    JAV_BASE_URL: str = "https://missav.ws"
    OUMEI_BASE_URL: str = "https://www.xvideos.com"

    # Search timeouts per source
    SEARCH_TIMEOUT_GUOCHAN: float = float(os.getenv("SEARCH_TIMEOUT_GUOCHAN", "10.0"))
    SEARCH_TIMEOUT_HANIME: float = float(os.getenv("SEARCH_TIMEOUT_HANIME", "12.0"))
    SEARCH_TIMEOUT_JAV: float = float(os.getenv("SEARCH_TIMEOUT_JAV", "10.0"))
    SEARCH_TIMEOUT_OUMEI: float = float(os.getenv("SEARCH_TIMEOUT_OUMEI", "10.0"))

    # Proxy settings
    PROXY_ENABLED: bool = os.getenv("PROXY_ENABLED", "true").lower() in ("true", "1", "yes")

    # Webhook
    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
    WEBHOOK_PORT: int = int(os.getenv("PORT", "8000"))

    USER_AGENT: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
    )

    # Cache
    CACHE_TTL: int = int(os.getenv("CACHE_TTL", "300"))
    CACHE_MAX_ENTRIES: int = int(os.getenv("CACHE_MAX_ENTRIES", "500"))

    # Rate limiting
    MAX_SEARCHES_PER_MINUTE: int = int(os.getenv("MAX_SEARCHES_PER_MINUTE", "10"))

    # Admin IDs (comma-separated)
    ADMIN_IDS: set[int] = {
        int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
    }

    # Database path
    DB_PATH: str = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bot.db"))

    @classmethod
    def validate(cls) -> list[str]:
        errors = []
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN not set in .env")
        elif "your-" in cls.BOT_TOKEN.lower() or "placeholder" in cls.BOT_TOKEN.lower():
            errors.append("BOT_TOKEN looks like a placeholder")
        if not cls.ADMIN_IDS:
            errors.append("ADMIN_IDS is empty")
        else:
            admin_str = os.getenv("ADMIN_IDS", "")
            if admin_str in ("123456789", "your_admin_id", "your-id-here"):
                errors.append("ADMIN_IDS looks like a placeholder")
        return errors


config = Config()

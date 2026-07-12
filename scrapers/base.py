"""scrapers/base.py — Abstract base class for all video source scrapers."""
from __future__ import annotations

import abc
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import ClassVar

import httpx

logger = logging.getLogger(__name__)


@dataclass
class VideoResult:
    """Unified video result — all scrapers return this dataclass."""
    title: str
    url: str
    source: str
    source_label: str
    cover: str = ""
    duration: str = ""
    views: str = ""
    date: str = ""
    extra: dict = field(default_factory=dict)


_scraper_registry: dict[str, type["BaseScraper"]] = {}

# Shared httpx client for connection pooling
_httpx_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def _get_shared_client() -> httpx.AsyncClient:
    """Get or create a shared httpx client with connection pooling."""
    global _httpx_client
    async with _client_lock:
        if _httpx_client is None:
            from config import config
            limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
            _httpx_client = httpx.AsyncClient(
                headers={"User-Agent": config.USER_AGENT},
                timeout=httpx.Timeout(10.0),
                limits=limits,
            )
        return _httpx_client


def register_scraper(name: str, cls: type["BaseScraper"]):
    _scraper_registry[name] = cls


def get_scraper(name: str) -> type["BaseScraper"] | None:
    return _scraper_registry.get(name)


def list_scrapers() -> list[str]:
    return list(_scraper_registry.keys())


class BaseScraper(abc.ABC):
    """Abstract base for all scrapers.

    Subclasses auto-register via __init_subclass__.
    Override name, label, base_url, timeout, and search().
    """

    name: ClassVar[str] = ""
    label: ClassVar[str] = ""
    base_url: ClassVar[str] = ""
    timeout: ClassVar[float] = 10.0

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not cls.name:
            cls.name = cls.__module__.rsplit(".", 1)[-1]
        if cls.name and cls.name not in ("base",):
            register_scraper(cls.name, cls)
            logger.debug("Registered scraper: %s (%s)", cls.name, cls.__module__)

    @abc.abstractmethod
    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        ...

    def _get_proxy(self) -> dict | str | None:
        """Get proxy config — uses PROXY_URL first, falls back to proxy pool."""
        from config import config
        if not config.PROXY_ENABLED:
            return None
        if config.PROXY_URL:
            proxy_url = config.PROXY_URL
            return {"http://": proxy_url, "https://": proxy_url}
        # Fall back to proxy pool
        try:
            from proxy_pool import get_random_proxy
            proxy = get_random_proxy()
            if proxy:
                return {"http://": proxy, "https://": proxy}
        except Exception:
            pass
        return None

    def _get_httpx_kwargs(self) -> dict:
        kwargs = {}
        proxy = self._get_proxy()
        if proxy:
            kwargs["proxies"] = proxy
        return kwargs

    async def search_with_retry(
        self, keyword: str, max_results: int = 15,
        max_retries: int = 1, base_delay: float = 0.5,
    ) -> list[VideoResult]:
        last_exc = None
        for attempt in range(1 + max_retries):
            try:
                start = time.monotonic()
                results = await self.search(keyword, max_results)
                elapsed = time.monotonic() - start
                logger.debug(
                    "%s search '%s' returned %d results in %.2fs (attempt %d/%d)",
                    self.name, keyword[:30], len(results), elapsed,
                    attempt + 1, 1 + max_retries,
                )
                return results
            except Exception as e:
                last_exc = e
                logger.warning(
                    "%s search attempt %d failed: %s", self.name, attempt + 1, e,
                )
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
        logger.error("%s search failed after %d retries: %s", self.name, max_retries, last_exc)
        return []

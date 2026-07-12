"""scrapers/base.py — Abstract base class for all video source scrapers.

Pattern source: PaulSonOfLars/tgbot (module auto-loading) +
Selutario/videogram (scraper abstraction).
"""
from __future__ import annotations

import abc
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import ClassVar

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


# Import here to avoid circular imports at module level
_scraper_registry: dict[str, type["BaseScraper"]] = {}


def register_scraper(name: str, cls: type["BaseScraper"]):
    """Register a scraper class (called by __init_subclass__)."""
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
            # Derive name from module filename if not set
            cls.name = cls.__module__.rsplit(".", 1)[-1]
        if cls.name and cls.name not in ("base",):
            register_scraper(cls.name, cls)
            logger.debug("Registered scraper: %s (%s)", cls.name, cls.__module__)

    @abc.abstractmethod
    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        """Search for videos. Must return list of VideoResult."""
        ...

    async def search_with_retry(
        self, keyword: str, max_results: int = 15,
        max_retries: int = 1, base_delay: float = 0.5,
    ) -> list[VideoResult]:
        """Search with automatic retry + exponential backoff."""
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

"""pre_cache.py — Background result pre-caching for instant search responses."""
import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime

from scrapers import search_all, _ensure_built
from config import config

logger = logging.getLogger(__name__)

_search_cache: dict[str, dict] = {}
_cache_lock = asyncio.Lock()
CACHE_TTL = config.CACHE_TTL

keyword_popularity: dict[str, int] = defaultdict(int)
_popularity_lock = asyncio.Lock()
_MAX_POPULARITY_ENTRIES = 200

_pre_cache_task: asyncio.Task | None = None
_refresh_task: asyncio.Task | None = None

_PRE_CACHE_HOT_COUNT = 5
_PRE_CACHE_REFRESH_INTERVAL = 1800


async def track_search(keyword: str):
    async with _popularity_lock:
        keyword_popularity[keyword.lower().strip()] += 1
        if len(keyword_popularity) > _MAX_POPULARITY_ENTRIES:
            sorted_items = sorted(keyword_popularity.items(), key=lambda x: x[1], reverse=True)
            keyword_popularity.clear()
            keyword_popularity.update(sorted_items[:_MAX_POPULARITY_ENTRIES // 2])


async def get_hot_keywords(top_n: int = 5) -> list[str]:
    async with _popularity_lock:
        if not keyword_popularity:
            return []
        sorted_kw = sorted(keyword_popularity.items(), key=lambda x: x[1], reverse=True)
        return [kw for kw, _ in sorted_kw[:top_n]]


async def cache_get(keyword: str) -> list[dict] | None:
    async with _cache_lock:
        entry = _search_cache.get(keyword.lower().strip())
        if entry is None:
            return None
        elapsed = time.time() - entry["cached_at"]
        if elapsed > CACHE_TTL:
            del _search_cache[keyword.lower().strip()]
            return None
        return entry["results"]


async def cache_set(keyword: str, results: list[dict]):
    if not results:
        return
    async with _cache_lock:
        _search_cache[keyword.lower().strip()] = {
            "results": results,
            "cached_at": time.time(),
            "source_count": len(set(r.get("source", "") for r in results)),
        }
        if len(_search_cache) > config.CACHE_MAX_ENTRIES:
            sorted_entries = sorted(_search_cache.items(), key=lambda x: x[1]["cached_at"])
            _search_cache.clear()
            _search_cache.update(sorted_entries[-config.CACHE_MAX_ENTRIES * 2 // 3:])


async def cache_peek_status() -> dict:
    async with _cache_lock:
        now = time.time()
        fresh = sum(1 for e in _search_cache.values() if now - e["cached_at"] < CACHE_TTL)
        return {
            "total": len(_search_cache),
            "fresh": fresh,
            "stale": len(_search_cache) - fresh,
        }


async def _prefetch_keyword(keyword: str, category: str = "all") -> list[dict]:
    try:
        results = await asyncio.wait_for(
            search_all(keyword, category, config.MAX_SEARCH_RESULTS),
            timeout=15.0,
        )
        if results:
            await cache_set(keyword, results)
            logger.info("Pre-cache: cached '%s' (%d results)", keyword, len(results))
        return results or []
    except asyncio.TimeoutError:
        logger.debug("Pre-cache: timeout for '%s'", keyword)
        return []
    except Exception as e:
        logger.debug("Pre-cache: error for '%s': %s", keyword, e)
        return []


async def _refresh_hot_keywords():
    hot = await get_hot_keywords(_PRE_CACHE_HOT_COUNT)
    if not hot:
        hot = ["cosplay", "mahua", "anime"]
    logger.info("Pre-cache: refreshing %d hot keywords: %s", len(hot), hot)
    for kw in hot:
        await _prefetch_keyword(kw)
        await asyncio.sleep(2)


async def _pre_cache_loop():
    await asyncio.sleep(60)
    logger.info("Pre-cache: background loop started (interval=%ds)", _PRE_CACHE_REFRESH_INTERVAL)
    while True:
        try:
            await _refresh_hot_keywords()
        except Exception as e:
            logger.warning("Pre-cache refresh error: %s", e)
        await asyncio.sleep(_PRE_CACHE_REFRESH_INTERVAL)


async def start_pre_cache():
    global _pre_cache_task
    if _pre_cache_task is not None:
        return
    _pre_cache_task = asyncio.create_task(_pre_cache_loop())
    logger.info("Pre-cache started (hot=%d, interval=%ds, ttl=%ds)",
                _PRE_CACHE_HOT_COUNT, _PRE_CACHE_REFRESH_INTERVAL, CACHE_TTL)


async def stop_pre_cache():
    global _pre_cache_task
    if _pre_cache_task:
        _pre_cache_task.cancel()
        try:
            await _pre_cache_task
        except asyncio.CancelledError:
            pass
        _pre_cache_task = None
    logger.info("Pre-cache stopped")


async def search_with_cache(keyword: str, category: str = "all") -> list[dict]:
    asyncio.create_task(track_search(keyword))
    cached = await cache_get(keyword)
    if cached is not None:
        logger.debug("Cache HIT for '%s' (%d results)", keyword, len(cached))
        return cached
    logger.info("Cache MISS for '%s' — scraping live", keyword)
    try:
        results = await asyncio.wait_for(
            search_all(keyword, category, config.MAX_SEARCH_RESULTS),
            timeout=15.0,
        )
        if results:
            asyncio.create_task(cache_set(keyword, results))
        return results or []
    except asyncio.TimeoutError:
        logger.warning("Live search timeout for '%s'", keyword)
        return []
    except Exception as e:
        logger.error("Live search error for '%s': %s", keyword, e)
        return []

"""pre_cache.py — Background result pre-caching for instant search responses.

Pattern source: ai-xrvip/tb (pre_cache.py)
Maintains a pool of cached search results for hot keywords.
Background crawlers periodically refresh the cache.
"""
import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime

from scrapers import search_all, CATEGORIES, _ensure_built
from config import config

logger = logging.getLogger(__name__)

# ── In-memory cache ──
# {keyword_lower: {"results": [...], "cached_at": timestamp, "source_count": int}}
_search_cache: dict[str, dict] = {}
_cache_lock = asyncio.Lock()
CACHE_TTL = config.CACHE_TTL  # seconds (default 300 = 5 min)

# ── Keyword popularity tracking ──
keyword_popularity: dict[str, int] = defaultdict(int)
_popularity_lock = asyncio.Lock()
_MAX_POPULARITY_ENTRIES = 200

# ── Background tasks ──
_pre_cache_task: asyncio.Task | None = None
_refresh_task: asyncio.Task | None = None

# ── Pre-cache hot keywords pool ──
_PRE_CACHE_HOT_COUNT = 5       # number of hot keywords to pre-cache
_PRE_CACHE_REFRESH_INTERVAL = 1800  # 30 min between full refreshes


# ========== Keyword popularity ==========

async def track_search(keyword: str):
    """Record that a keyword was searched (for popularity tracking)."""
    async with _popularity_lock:
        keyword_popularity[keyword.lower().strip()] += 1
        # Trim if over limit
        if len(keyword_popularity) > _MAX_POPULARITY_ENTRIES:
            sorted_items = sorted(
                keyword_popularity.items(), key=lambda x: x[1], reverse=True
            )
            keyword_popularity.clear()
            keyword_popularity.update(sorted_items[:_MAX_POPULARITY_ENTRIES // 2])


async def get_hot_keywords(top_n: int = 5) -> list[str]:
    """Get the most searched keywords."""
    async with _popularity_lock:
        if not keyword_popularity:
            return []
        sorted_kw = sorted(keyword_popularity.items(), key=lambda x: x[1], reverse=True)
        return [kw for kw, _ in sorted_kw[:top_n]]


# ========== Cache operations ==========

async def cache_get(keyword: str) -> list[dict] | None:
    """Get cached results for a keyword. Returns None if miss or expired."""
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
    """Store search results in cache."""
    if not results:
        return
    async with _cache_lock:
        _search_cache[keyword.lower().strip()] = {
            "results": results,
            "cached_at": time.time(),
            "source_count": len(set(r.get("source", "") for r in results)),
        }
        # Trim cache if over limit
        if len(_search_cache) > config.CACHE_MAX_ENTRIES:
            sorted_entries = sorted(
                _search_cache.items(), key=lambda x: x[1]["cached_at"]
            )
            _search_cache.clear()
            _search_cache.update(sorted_entries[-config.CACHE_MAX_ENTRIES * 2 // 3:])


async def cache_peek_status() -> dict:
    """Get cache stats for admin."""
    async with _cache_lock:
        now = time.time()
        fresh = sum(1 for e in _search_cache.values() if now - e["cached_at"] < CACHE_TTL)
        return {
            "total": len(_search_cache),
            "fresh": fresh,
            "stale": len(_search_cache) - fresh,
        }


# ========== Background pre-cache ==========

async def _prefetch_keyword(keyword: str, category: str = "all") -> list[dict]:
    """Fetch and cache results for a single keyword."""
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
    """Refresh pre-cache for hot keywords."""
    hot = await get_hot_keywords(_PRE_CACHE_HOT_COUNT)
    if not hot:
        # Default keywords if no history yet
        hot = ["cosplay", "mahua", "anime"]
    
    logger.info("Pre-cache: refreshing %d hot keywords: %s", len(hot), hot)
    for kw in hot:
        await _prefetch_keyword(kw)
        await asyncio.sleep(2)  # Rate limit between prefetches


async def _pre_cache_loop():
    """Background loop: refresh pre-cache periodically."""
    # Wait for bot to start first
    await asyncio.sleep(60)
    logger.info("Pre-cache: background loop started (interval=%ds)", _PRE_CACHE_REFRESH_INTERVAL)
    
    while True:
        try:
            await _refresh_hot_keywords()
        except Exception as e:
            logger.warning("Pre-cache refresh error: %s", e)
        await asyncio.sleep(_PRE_CACHE_REFRESH_INTERVAL)


# ========== Public API ==========

async def start_pre_cache():
    """Start the background pre-cache system."""
    global _pre_cache_task
    if _pre_cache_task is not None:
        return
    _pre_cache_task = asyncio.create_task(_pre_cache_loop())
    logger.info("Pre-cache started (hot=%d, interval=%ds, ttl=%ds)",
                _PRE_CACHE_HOT_COUNT, _PRE_CACHE_REFRESH_INTERVAL, CACHE_TTL)


async def stop_pre_cache():
    """Stop the background pre-cache system."""
    global _pre_cache_task
    if _pre_cache_task:
        _pre_cache_task.cancel()
        try:
            await _pre_cache_task
        except asyncio.CancelledError:
            pass
        _pre_cache_task = None
    logger.info("Pre-cache stopped")


# ========== Search with cache-first ==========

async def search_with_cache(keyword: str, category: str = "all") -> list[dict]:
    """Search with cache-first: check cache, fall back to live scrape."""
    # Track popularity
    asyncio.create_task(track_search(keyword))
    
    # 1. Try cache first (instant)
    cached = await cache_get(keyword)
    if cached is not None:
        logger.debug("Cache HIT for '%s' (%d results)", keyword, len(cached))
        # Trigger async refresh if cache is getting old (> 50% TTL)
        return cached
    
    # 2. Cache miss — scrape live
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

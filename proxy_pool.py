"""proxy_pool.py — Fast proxy pool using proxyscrape API only."""
import asyncio
import logging
import random
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Use proxyscrape API for reliable proxies (updated hourly)
PROXY_API_URL = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&proxy_format=protocolipport&format=text&timeout=5000"

REFRESH_INTERVAL = 900  # 15 min
PROXY_TIMEOUT = 5.0

_proxy_pool: list[str] = []
_pool_lock = asyncio.Lock()


async def _fetch_proxies() -> list[str]:
    """Fetch proxies from proxyscrape API."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            r = await client.get(PROXY_API_URL)
            if r.status_code == 200:
                lines = [l.strip() for l in r.text.splitlines() if l.strip().startswith("http")]
                logger.info("Fetched %d proxies from API", len(lines))
                return lines
    except Exception as e:
        logger.warning("Failed to fetch proxies: %s", e)
    return []


async def _do_refresh():
    """Refresh proxy pool from API (no validation to save time)."""
    global _proxy_pool
    proxies = await _fetch_proxies()
    if proxies:
        async with _pool_lock:
            _proxy_pool = proxies[:500]  # Keep top 500
        logger.info("Proxy pool: %d proxies loaded", len(proxies[:500]))
    else:
        logger.warning("Failed to refresh proxy pool")


def get_random_proxy() -> Optional[str]:
    """Get a random proxy from the pool."""
    if _proxy_pool:
        return random.choice(_proxy_pool)
    return None


async def _refresh_loop():
    await asyncio.sleep(60)
    while True:
        try:
            await _do_refresh()
        except Exception as e:
            logger.warning("Proxy refresh error: %s", e)
        await asyncio.sleep(REFRESH_INTERVAL)


async def start_proxy_pool():
    asyncio.create_task(_do_refresh())
    asyncio.create_task(_refresh_loop())
    logger.info("Proxy pool started")


async def stop_proxy_pool():
    global _proxy_pool
    async with _pool_lock:
        _proxy_pool = []
    logger.info("Proxy pool stopped")

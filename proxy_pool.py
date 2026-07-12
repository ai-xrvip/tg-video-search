"""proxy_pool.py — Simple proxy pool for accessing geo-blocked sites.
Pattern source: ai-xrvip/tb (proxy_pool.py)
"""
import asyncio
import logging
import random
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Free proxy sources
PROXY_SOURCES = [
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&proxy_format=protocolipport&format=text&timeout=5000",
    "https://www.proxy-list.download/api/v1/get?type=http",
]

REFRESH_INTERVAL = 600  # 10 min
PROXY_TIMEOUT = 5.0
VALIDATE_URL = "https://www.baidu.com"

_proxy_pool: list[str] = []
_pool_lock = asyncio.Lock()
_pool_ready = asyncio.Event()


async def _fetch_proxies() -> list[str]:
    all_proxies = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        for src in PROXY_SOURCES:
            try:
                r = await client.get(src)
                if r.status_code == 200:
                    lines = [l.strip() for l in r.text.splitlines() if l.strip().startswith("http")]
                    all_proxies.extend(lines)
            except Exception as e:
                logger.debug("Proxy fetch failed: %s", e)
    return list(set(all_proxies))


async def _validate_proxy(proxy: str) -> bool:
    try:
        async with httpx.AsyncClient(
            proxies=proxy, timeout=httpx.Timeout(PROXY_TIMEOUT), follow_redirects=True,
        ) as client:
            r = await client.get(VALIDATE_URL)
            return r.status_code == 200
    except Exception:
        return False


async def _validate_pool(proxies: list[str]) -> list[str]:
    sem = asyncio.Semaphore(10)
    async def validate_one(p: str) -> Optional[str]:
        async with sem:
            if await _validate_proxy(p):
                return p
        return None
    tasks = [validate_one(p) for p in proxies]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


async def _do_refresh():
    global _proxy_pool
    proxies = await _fetch_proxies()
    if proxies:
        valid = await _validate_pool(proxies)
        if valid:
            async with _pool_lock:
                _proxy_pool = valid
            _pool_ready.set()
            logger.info("Proxy pool: %d working proxies", len(valid))
        else:
            logger.warning("Proxy pool: no working proxies found")
    else:
        logger.warning("Proxy pool: failed to fetch any proxies")
    _pool_ready.set()


def get_random_proxy() -> Optional[str]:
    if _proxy_pool:
        return random.choice(_proxy_pool)
    return None


async def start_proxy_pool():
    asyncio.create_task(_do_refresh())
    logger.info("Proxy pool started")


async def stop_proxy_pool():
    global _proxy_pool
    async with _pool_lock:
        _proxy_pool = []

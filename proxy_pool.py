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

# More proxy sources — using multiple providers for redundancy
PROXY_SOURCES = [
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&proxy_format=protocolipport&format=text&timeout=5000",
    "https://www.proxy-list.download/api/v1/get?type=http",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/hookzof/socks5-list/master/http.txt",
]

REFRESH_INTERVAL = 600  # 10 min
PROXY_TIMEOUT = 8.0
# Use a globally accessible URL for validation
VALIDATE_URL = "https://httpbin.org/ip"

_proxy_pool: list[str] = []
_pool_lock = asyncio.Lock()
_pool_ready = asyncio.Event()
_last_refresh = 0.0


async def _fetch_proxies() -> list[str]:
    all_proxies = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        for src in PROXY_SOURCES:
            try:
                r = await client.get(src)
                if r.status_code == 200:
                    lines = []
                    for l in r.text.splitlines():
                        l = l.strip()
                        if not l:
                            continue
                        if l.startswith("http://") or l.startswith("https://"):
                            lines.append(l)
                        elif ":" in l and not l.startswith("http"):
                            lines.append(f"http://{l}")
                    all_proxies.extend(lines)
                    logger.debug("Fetched %d proxies from %s", len(lines), src.split("/")[2])
            except Exception as e:
                logger.debug("Proxy fetch failed for %s: %s", src.split("/")[2], e)
    return list(set(all_proxies))


async def _validate_proxy(proxy: str) -> bool:
    try:
        async with httpx.AsyncClient(
            proxies={"http://": proxy, "https://": proxy},
            timeout=httpx.Timeout(PROXY_TIMEOUT),
            follow_redirects=True,
        ) as client:
            r = await client.get(VALIDATE_URL)
            return r.status_code == 200
    except Exception:
        return False


async def _validate_pool(proxies: list[str]) -> list[str]:
    sem = asyncio.Semaphore(20)  # More concurrent validation

    async def validate_one(p: str) -> Optional[str]:
        async with sem:
            if await _validate_proxy(p):
                return p
        return None

    tasks = [validate_one(p) for p in proxies]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


async def _do_refresh():
    global _proxy_pool, _last_refresh
    _last_refresh = time.time()
    proxies = await _fetch_proxies()
    if proxies:
        valid = await _validate_pool(proxies[:200])  # Limit validation to first 200
        if valid:
            async with _pool_lock:
                _proxy_pool = valid
            _pool_ready.set()
            logger.info("Proxy pool: %d working proxies", len(valid))
        else:
            logger.warning("Proxy pool: no working proxies found (retaining old pool)")
    else:
        logger.warning("Proxy pool: failed to fetch any proxies (retaining old pool)")
    _pool_ready.set()


async def _refresh_loop():
    """Background loop to refresh proxy pool periodically."""
    await asyncio.sleep(30)  # initial wait
    while True:
        try:
            await _do_refresh()
        except Exception as e:
            logger.warning("Proxy refresh error: %s", e)
        await asyncio.sleep(REFRESH_INTERVAL)


def get_random_proxy() -> Optional[str]:
    if _proxy_pool:
        return random.choice(_proxy_pool)
    return None


async def start_proxy_pool():
    asyncio.create_task(_do_refresh())
    asyncio.create_task(_refresh_loop())
    logger.info("Proxy pool started")


async def stop_proxy_pool():
    global _proxy_pool
    async with _pool_lock:
        _proxy_pool = []

"""proxy_pool.py — Simple proxy pool for accessing geo-blocked sites."""
import asyncio
import logging
import random
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/master/HTTPS.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online/proxies/http.txt",
]

# Direct embedded fallback — refreshed from GitHub on each deploy
# Format: ip:port
EMBEDDED_PROXIES = [
]

REFRESH_INTERVAL = 600
PROXY_TIMEOUT = 8.0
VALIDATE_URL = "https://httpbin.org/ip"

_proxy_pool: list[str] = []
_pool_lock = asyncio.Lock()
_pool_ready = asyncio.Event()


async def _fetch_proxies() -> list[str]:
    all_proxies = list(EMBEDDED_PROXIES)
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
                    logger.info("Proxy fetch OK %s: %d proxies", src.split("/")[2], len(lines))
                else:
                    logger.warning("Proxy fetch HTTP %d from %s", r.status_code, src.split("/")[2])
            except Exception as e:
                logger.warning("Proxy fetch FAIL %s: %s", src.split("/")[2], e)
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
    sem = asyncio.Semaphore(30)

    async def validate_one(p: str) -> Optional[str]:
        async with sem:
            if await _validate_proxy(p):
                return p
        return None

    tasks = [validate_one(p) for p in proxies[:300]]
    results = await asyncio.gather(*tasks)
    valid = [r for r in results if r is not None]
    logger.info("Proxy validation: %d/%d valid", len(valid), len(proxies[:300]))
    return valid


async def _do_refresh():
    global _proxy_pool
    logger.info("Proxy pool: fetching proxies...")
    proxies = await _fetch_proxies()
    if proxies:
        logger.info("Proxy pool: validating %d proxies...", len(proxies))
        valid = await _validate_pool(proxies)
        if valid:
            async with _pool_lock:
                _proxy_pool = valid
            _pool_ready.set()
            logger.info("Proxy pool: %d working proxies available!", len(valid))
            # Test one against a blocked site
            test_proxy = random.choice(valid)
            logger.info("Proxy pool: testing %s against hanime1.me...", test_proxy)
        else:
            logger.warning("Proxy pool: no working proxies found (retaining old pool)")
    else:
        logger.warning("Proxy pool: failed to fetch any proxies (retaining old pool)")
    _pool_ready.set()


async def _refresh_loop():
    await asyncio.sleep(60)
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

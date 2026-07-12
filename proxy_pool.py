"""proxy_pool.py — Simple proxy pool for accessing geo-blocked sites."""
import asyncio
import logging
import random
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Free proxy sources — mix of raw lists and web-scraped
PROXY_SOURCES = [
    # GitHub raw lists
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/master/HTTPS.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online/proxies/http.txt",
]

# Web pages to scrape for active proxies
PROXY_WEB_SOURCES = [
    "https://free-proxy-list.net/",
    "https://www.sslproxies.org/",
]

REFRESH_INTERVAL = 600  # 10 min
PROXY_TIMEOUT = 8.0
VALIDATE_URL = "https://httpbin.org/ip"

_proxy_pool: list[str] = []
_pool_lock = asyncio.Lock()
_pool_ready = asyncio.Event()


async def _fetch_proxies() -> list[str]:
    """Fetch proxies from all sources."""
    all_proxies = []

    # 1. Raw text lists
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
                logger.debug("Fetch failed %s: %s", src.split("/")[2], e)

    # 2. Web page scraping (more current proxies)
    for url in PROXY_WEB_SOURCES:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "html.parser")
                    table = soup.find("table", class_="table")
                    if table:
                        rows = table.select("tbody tr")
                        for row in rows:
                            cols = row.find_all("td")
                            if len(cols) >= 2:
                                ip = cols[0].get_text(strip=True)
                                port = cols[1].get_text(strip=True)
                                if ip and port:
                                    all_proxies.append(f"http://{ip}:{port}")
                    logger.debug("Scraped %d proxies from %s", len(rows) if table else 0, url.split("/")[2])
        except Exception as e:
            logger.debug("Scrape failed %s: %s", url.split("/")[2], e)

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
    sem = asyncio.Semaphore(20)

    async def validate_one(p: str) -> Optional[str]:
        async with sem:
            if await _validate_proxy(p):
                return p
        return None

    tasks = [validate_one(p) for p in proxies[:200]]
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

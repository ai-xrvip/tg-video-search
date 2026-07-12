"""scrapers/jav.py — Scraper for missav.ws (日韩AV) using cloudscraper"""
import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin

import cloudscraper
from bs4 import BeautifulSoup

from config import config
from scrapers.base import BaseScraper, VideoResult

logger = logging.getLogger(__name__)

_cs = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False},
    delay=3,
)


def _search_jav_sync(keyword: str, max_results: int, base_url: str, timeout: float) -> list[dict]:
    results = []
    try:
        search_url = f"{base_url}/dm334/search/{keyword}"
        headers = {"Referer": base_url}
        resp = _cs.get(search_url, headers=headers, timeout=timeout)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Try multiple selectors for missav
        items = soup.select("div.thumbnail.group, div.item, article, div[class*=video]")
        if not items:
            items = soup.find_all("a", href=re.compile(r"/\w+/\d+"))
        if not items:
            items = soup.find_all("a", href=True)

        seen_urls = set()
        for item in items:
            if len(results) >= max_results:
                break
            try:
                a_tag = item if item.name == "a" and item.get("href") else item.find("a", href=True)
                if not a_tag:
                    continue
                href = a_tag.get("href", "")
                full_url = urljoin(base_url, href) if href.startswith("/") else href

                if full_url in seen_urls or not full_url.startswith("http"):
                    continue
                # Filter out non-video links
                if any(x in full_url for x in ["/dm334", "/tag", "/genre", "javascript"]):
                    continue
                seen_urls.add(full_url)

                img_tag = item.find("img")
                cover = ""
                if img_tag:
                    cover = img_tag.get("data-src") or img_tag.get("src") or ""
                    if cover.startswith("//"):
                        cover = "https:" + cover

                title = (a_tag.get("title", "") or
                         (img_tag.get("alt", "").strip() if img_tag else "") or
                         a_tag.get_text(strip=True))

                if title and full_url:
                    results.append({
                        "title": title.strip()[:200],
                        "url": full_url,
                        "cover": cover,
                        "source": "jav",
                        "source_label": "\U0001f1f0\U0001f1f2 日韩",
                    })
            except Exception as e:
                logger.debug("jav item parse error: %s", e)
                continue

    except Exception as e:
        logger.warning("jav cloudscraper error: %s", e)
    return results


class JavScraper(BaseScraper):
    name = "jav"
    label = "\U0001f1f0\U0001f1f2 日韩"
    base_url = config.JAV_BASE_URL
    timeout = config.SEARCH_TIMEOUT_JAV

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None, _search_jav_sync, keyword, max_results, self.base_url, self.timeout,
        )
        return [VideoResult(**r) for r in raw]


async def get_video_detail(url: str) -> Optional[dict]:
    """Extract video URL from a missav.ws page."""
    try:
        headers = {"Referer": config.JAV_BASE_URL}
        resp = _cs.get(url, headers=headers, timeout=config.SEARCH_TIMEOUT_JAV)
        html = resp.text
        m = re.search(r'dvd_id["\']?\s*:\s*["\']([^"\']+)["\']', html)
        if m:
            dvd_id = m.group(1)
            return {"mp4": f"https://fourhoi.com/{dvd_id}/preview.mp4"}
    except Exception as e:
        logger.error("jav detail error: %s", e)
    return None

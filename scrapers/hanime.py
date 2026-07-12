"""scrapers/hanime.py — Scraper for hanime1.me (里番/动漫) using cloudscraper"""
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

# Reusable cloudscraper instance (synchronous, run in executor)
_cs = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False},
    delay=3,
)


def _search_sync(keyword: str, max_results: int, base_url: str, timeout: float) -> list[dict]:
    """Synchronous search using cloudscraper (runs in thread pool)."""
    results = []
    try:
        params = {"query": keyword, "genre": ""}
        headers = {"Referer": base_url}
        resp = _cs.get(f"{base_url}/search", params=params, headers=headers, timeout=timeout)
        soup = BeautifulSoup(resp.text, "html.parser")

        video_links = soup.select('a[href*="/watch?v="]')
        seen_urls = set()

        for a_tag in video_links:
            if len(results) >= max_results:
                break
            href = a_tag.get("href", "")
            if not href:
                continue
            full_url = urljoin(base_url, href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            img_tag = a_tag.find("img")
            cover = ""
            if img_tag:
                cover = img_tag.get("data-src") or img_tag.get("src") or ""
                if cover.startswith("//"):
                    cover = "https:" + cover

            title = (
                a_tag.get("title", "")
                or (img_tag.get("alt", "") if img_tag else "")
                or a_tag.get_text(strip=True)
            )

            anchor_text = a_tag.get_text()
            duration = ""
            dm = re.search(r"(\d+:\d+(?::\d+)?)", anchor_text)
            if dm:
                duration = dm.group(1)

            if title and full_url:
                results.append({
                    "title": title.strip()[:200],
                    "url": full_url,
                    "cover": cover,
                    "source": "hanime",
                    "source_label": "\U0001f3b9 里番",
                    "duration": duration,
                })
    except Exception as e:
        logger.warning("hanime cloudscraper error: %s", e)
    return results


class HanimeScraper(BaseScraper):
    name = "hanime"
    label = "\U0001f3b9 里番"
    base_url = config.HANIME_BASE_URL
    timeout = config.SEARCH_TIMEOUT_HANIME

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None, _search_sync, keyword, max_results, self.base_url, self.timeout,
        )
        return [VideoResult(**r) for r in raw]


async def get_video_detail(url: str) -> Optional[dict]:
    """Extract video source URLs from a hanime1.me watch page."""
    try:
        headers = {"Referer": config.HANIME_BASE_URL}
        resp = _cs.get(url, headers=headers, timeout=config.SEARCH_TIMEOUT_HANIME)
        html = resp.text
        urls = {}
        for m in re.finditer(r'(https?://[^"\\\'<>]+\.(?:mp4|m3u8))', html):
            vid_url = m.group(1)
            vid_url = re.sub(r'[?].*', '', vid_url)
            for q in ['1080p', '720p', '480p', '360p']:
                if q in vid_url:
                    urls[q] = vid_url
                    break
            if 'm3u8' in vid_url and 'hls' not in urls:
                urls['hls'] = vid_url
        return urls if urls else None
    except Exception as e:
        logger.error("hanime detail error: %s", e)
    return None

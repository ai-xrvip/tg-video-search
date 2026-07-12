"""scrapers/guochan.py — Scraper for 9191md.me (国产) using cloudscraper"""
import asyncio
import logging
import re
from typing import Optional

import cloudscraper
from bs4 import BeautifulSoup

from config import config
from scrapers.base import BaseScraper, VideoResult

logger = logging.getLogger(__name__)

_cs = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False},
    delay=3,
)


def _search_guochan_sync(keyword: str, max_results: int, base_url: str, search_url: str, timeout: float) -> list[dict]:
    results = []
    try:
        headers = {"Referer": base_url}
        resp = _cs.post(search_url, data={"wd": keyword}, headers=headers, timeout=timeout)
        soup = BeautifulSoup(resp.text, "html.parser")

        items = soup.select("div.detail_right_div ul li, li.vod-item")
        if not items:
            items = soup.find_all("li")
            items = [li for li in items if li.find("img") and li.find("a", href=True)]

        for item in items:
            if len(results) >= max_results:
                break
            try:
                a_tag = item.find("a", href=True)
                if not a_tag:
                    continue
                href = a_tag.get("href", "")
                if href.startswith("/"):
                    href = base_url + href
                if not href or "/vod/" not in href:
                    continue

                img_tag = item.find("img")
                cover = (img_tag.get("data-original") or img_tag.get("src") or "") if img_tag else ""

                title = (a_tag.get("title", "") or
                         (img_tag.get("alt", "") if img_tag else "") or
                         a_tag.get_text(strip=True))

                date_text = views = ""
                for p_tag in item.find_all("p"):
                    pt = p_tag.get_text(strip=True)
                    if re.match(r"\d{2}-\d{2}", pt):
                        date_text = pt
                    if "\u89c2\u770b" in pt or "\u64ad\u653e" in pt:
                        views = pt

                if title and href:
                    results.append({
                        "title": title.strip()[:200],
                        "url": href, "cover": cover,
                        "source": "guochan",
                        "source_label": "\U0001f1e8\U0001f1f3 国产",
                        "views": views, "date": date_text,
                    })
            except Exception as e:
                logger.debug("guochan item error: %s", e)
    except Exception as e:
        logger.warning("guochan cloudscraper error: %s", e)
    return results


class GuochanScraper(BaseScraper):
    name = "guochan"
    label = "\U0001f1e8\U0001f1f3 国产"
    base_url = config.GUOCHAN_BASE_URL
    timeout = config.SEARCH_TIMEOUT_GUOCHAN
    SEARCH_URL = f"{config.GUOCHAN_BASE_URL}/index.php/vod/search.html"

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None, _search_guochan_sync, keyword, max_results,
            self.base_url, self.SEARCH_URL, self.timeout,
        )
        return [VideoResult(**r) for r in raw]


async def get_video_detail(url: str) -> Optional[str]:
    try:
        headers = {"Referer": config.GUOCHAN_BASE_URL}
        resp = _cs.get(url, headers=headers, timeout=config.SEARCH_TIMEOUT_GUOCHAN)
        html = resp.text
        for pat in [r'"url"\s*:\s*"([^"]+\.m3u8)"', r'https?://[^"\\\'<>]+\.m3u8', r'video[_]?url\s*[:=]\s*["\']([^"\']+)["\']']:
            m = re.search(pat, html)
            if m:
                return m.group(1).replace("\\/", "/")
    except Exception as e:
        logger.error("guochan detail error: %s", e)
    return None

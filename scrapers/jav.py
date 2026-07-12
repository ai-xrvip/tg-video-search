"""scrapers/jav.py — Scraper for missav.ws (日韩AV) with Cloudflare bypass"""
import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import config
from scrapers.base import BaseScraper, VideoResult

logger = logging.getLogger(__name__)


class JavScraper(BaseScraper):
    name = "jav"
    label = "\U0001f1f0\U0001f1f2 日韩"
    base_url = config.JAV_BASE_URL
    timeout = config.SEARCH_TIMEOUT_JAV

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        results = []
        try:
            from curl_cffi.requests import AsyncSession

            search_url = f"{self.base_url}/dm334/search/{keyword}"
            headers = {
                "User-Agent": config.USER_AGENT,
                "Referer": self.base_url,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,zh-CN;q=0.9,en;q=0.8",
            }

            proxy = self._get_proxy()

            async with AsyncSession(headers=headers, timeout=self.timeout, impersonate="chrome124", proxies=proxy) as client:
                resp = await client.get(search_url)

                # Don't raise on 403 — Cloudflare sometimes returns content
                if resp.status_code != 403:
                    resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "html.parser")

                # Try multiple selectors
                items = soup.select("div.thumbnail.group, div.item, div.video-item")
                seen_urls = set()

                for item in items:
                    if len(results) >= max_results:
                        break
                    try:
                        a_tag = item.find("a", href=True)
                        if not a_tag:
                            continue
                        href = a_tag.get("href", "")
                        if not href or href.startswith("#"):
                            continue
                        full_url = urljoin(self.base_url, href)
                        if full_url in seen_urls:
                            continue
                        seen_urls.add(full_url)

                        img_tag = item.find("img")
                        cover = ""
                        if img_tag:
                            cover = img_tag.get("data-src") or img_tag.get("src") or ""
                            if cover.startswith("//"):
                                cover = "https:" + cover

                        title = (
                            a_tag.get("title", "")
                            or (img_tag.get("alt", "").strip() if img_tag else "")
                            or a_tag.get_text(strip=True)
                        )

                        if title and full_url:
                            results.append(VideoResult(
                                title=title.strip()[:200],
                                url=full_url,
                                cover=cover,
                                source="jav",
                                source_label="\U0001f1f0\U0001f1f2 日韩",
                            ))

                    except Exception as e:
                        logger.debug("jav item parse error: %s", e)
                        continue

        except ImportError:
            logger.error("jav: curl_cffi not installed")
        except Exception as e:
            logger.debug("jav search error: %s", e)

        return results


async def get_video_detail(url: str) -> Optional[dict]:
    """Extract video URL from a missav.ws page."""
    try:
        from curl_cffi.requests import AsyncSession
        from scrapers.base import get_scraper
        scraper_cls = get_scraper("jav")
        proxy = scraper_cls()._get_proxy() if scraper_cls else None

        headers = {"User-Agent": config.USER_AGENT, "Referer": config.JAV_BASE_URL}
        async with AsyncSession(headers=headers, timeout=config.SEARCH_TIMEOUT_JAV, impersonate="chrome124", proxies=proxy) as client:
            resp = await client.get(url)
            html = resp.text
            m = re.search(r'dvd_id["\']?\s*:\s*["\']([^"\']+)["\']', html)
            if m:
                dvd_id = m.group(1)
                return {"mp4": f"https://fourhoi.com/{dvd_id}/preview.mp4"}
    except Exception as e:
        logger.error("jav detail error: %s", e)
    return None

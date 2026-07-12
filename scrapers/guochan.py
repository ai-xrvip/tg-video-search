"""scrapers/guochan.py — Scraper for 9191md.me (国产) using curl_cffi for Cloudflare bypass"""
import asyncio
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from config import config
from scrapers.base import BaseScraper, VideoResult

logger = logging.getLogger(__name__)


class GuochanScraper(BaseScraper):
    name = "guochan"
    label = "\U0001f1e8\U0001f1f3 国产"
    base_url = config.GUOCHAN_BASE_URL
    timeout = config.SEARCH_TIMEOUT_GUOCHAN
    SEARCH_URL = f"{config.GUOCHAN_BASE_URL}/index.php/vod/search.html"

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        results = []
        try:
            from curl_cffi.requests import AsyncSession

            headers = {
                "User-Agent": config.USER_AGENT,
                "Referer": self.base_url,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }

            async with AsyncSession(
                headers=headers,
                timeout=self.timeout,
                impersonate="chrome124",
                proxies=self._get_proxy(),
            ) as client:
                resp = await client.post(self.SEARCH_URL, data={"wd": keyword})
                html = resp.text
                soup = BeautifulSoup(html, "html.parser")

                # Try multiple selectors for video items
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
                            href = self.base_url + href
                        if not href or "/vod/" not in href:
                            continue

                        img_tag = item.find("img")
                        cover = ""
                        if img_tag:
                            cover = img_tag.get("data-original") or img_tag.get("src") or ""

                        title = (a_tag.get("title", "") or
                                 (img_tag.get("alt", "") if img_tag else "") or
                                 a_tag.get_text(strip=True))

                        date_text = ""
                        views = ""
                        info_ps = item.find_all("p")
                        for p_tag in info_ps:
                            p_text = p_tag.get_text(strip=True)
                            if re.match(r"\d{2}-\d{2}", p_text):
                                date_text = p_text
                            if "\u89c2\u770b" in p_text or "\u64ad\u653e" in p_text:
                                views = p_text

                        if title and href:
                            results.append(VideoResult(
                                title=title.strip()[:200],
                                url=href,
                                cover=cover,
                                source="guochan",
                                source_label="\U0001f1e8\U0001f1f3 国产",
                                views=views,
                                date=date_text,
                            ))
                    except Exception as e:
                        logger.debug("guochan item parse error: %s", e)
                        continue

        except ImportError:
            logger.error("guochan: curl_cffi not installed")
        except Exception as e:
            logger.error("guochan search error: %s", e)

        return results


async def get_video_detail(url: str) -> Optional[str]:
    """Extract the video m3u8 URL from a 9191md.me video page."""
    try:
        from curl_cffi.requests import AsyncSession
        from scrapers.base import get_scraper
        scraper_cls = get_scraper("guochan")
        proxy = scraper_cls()._get_proxy() if scraper_cls else None

        headers = {"User-Agent": config.USER_AGENT, "Referer": config.GUOCHAN_BASE_URL}
        async with AsyncSession(headers=headers, timeout=config.SEARCH_TIMEOUT_GUOCHAN, impersonate="chrome124", proxies=proxy) as client:
            resp = await client.get(url)
            html = resp.text
            m = re.search(r'"url"\s*:\s*"([^"]+\.m3u8)"', html)
            if m:
                return m.group(1).replace("\\/", "/")
            m = re.search(r'https?://[^"\\\'<>]+\.m3u8', html)
            if m:
                return m.group(0)
            m = re.search(r'video[_]?url\s*[:=]\s*["\']([^"\']+)["\']', html)
            if m:
                return m.group(1)
    except Exception as e:
        logger.error("guochan detail error: %s", e)
    return None

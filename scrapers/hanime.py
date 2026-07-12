"""scrapers/hanime.py — Scraper for hanime1.me (里番/动漫) with proxy fallback"""
import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import config
from scrapers.base import BaseScraper, VideoResult

logger = logging.getLogger(__name__)


class HanimeScraper(BaseScraper):
    name = "hanime"
    label = "\U0001f3b9 里番"
    base_url = config.HANIME_BASE_URL
    timeout = config.SEARCH_TIMEOUT_HANIME

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        results = []
        try:
            from curl_cffi.requests import AsyncSession

            params = {"query": keyword, "genre": ""}
            headers = {
                "User-Agent": config.USER_AGENT,
                "Referer": self.base_url,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            }

            proxy = self._get_proxy()

            async with AsyncSession(headers=headers, timeout=self.timeout, impersonate="chrome124", proxies=proxy) as client:
                resp = await client.get(f"{self.base_url}/search", params=params)

                # Even on 403, try to parse (some sites return content with 403)
                if resp.status_code != 200 and resp.status_code != 403:
                    resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "html.parser")

                video_links = soup.select('a[href*="/watch?v="]')
                seen_urls = set()

                for a_tag in video_links:
                    if len(results) >= max_results:
                        break
                    href = a_tag.get("href", "")
                    if not href:
                        continue
                    full_url = urljoin(self.base_url, href)
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
                    views = ""
                    dm = re.search(r"(\d+:\d+(?::\d+)?)", anchor_text)
                    if dm:
                        duration = dm.group(1)

                    if title and full_url:
                        results.append(VideoResult(
                            title=title.strip()[:200],
                            url=full_url,
                            cover=cover,
                            source="hanime",
                            source_label="\U0001f3b9 里番",
                            duration=duration,
                            views=views,
                        ))

        except ImportError:
            logger.error("hanime: curl_cffi not installed")
        except Exception as e:
            if "timeout" in str(e).lower():
                logger.warning("hanime search timed out")
            else:
                logger.debug("hanime search error: %s", e)

        return results


async def get_video_detail(url: str) -> Optional[dict]:
    """Extract video source URLs from a hanime1.me watch page."""
    try:
        from curl_cffi.requests import AsyncSession
        from scrapers.base import get_scraper
        scraper_cls = get_scraper("hanime")
        proxy = scraper_cls()._get_proxy() if scraper_cls else None

        headers = {"User-Agent": config.USER_AGENT, "Referer": config.HANIME_BASE_URL}
        async with AsyncSession(headers=headers, timeout=config.SEARCH_TIMEOUT_HANIME, impersonate="chrome124", proxies=proxy) as client:
            resp = await client.get(url)
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

"""scrapers/pornhub.py — Scraper for pornhub.com"""
import asyncio
import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import config
from scrapers.base import BaseScraper, VideoResult

logger = logging.getLogger(__name__)


class PornhubScraper(BaseScraper):
    name = "pornhub"
    label = "\U0001f31f PornHub"
    base_url = "https://www.pornhub.com"
    timeout = 10.0

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        results = []
        try:
            from curl_cffi.requests import AsyncSession

            headers = {
                "User-Agent": config.USER_AGENT,
                "Referer": self.base_url,
                "Accept-Language": "en-US,en;q=0.9",
            }

            async with AsyncSession(headers=headers, timeout=self.timeout, impersonate="chrome124") as client:
                resp = await client.get(f"{self.base_url}/video/search?search={keyword}")
                soup = BeautifulSoup(resp.text, "html.parser")

                items = soup.select("li.pcVideoListItem, div.videoWrapper, div.thumbnail-info-wrapper")
                if not items:
                    items = soup.find_all("li", class_=re.compile(r"video"))
                if not items:
                    items = soup.select("div[id^=video_]")

                seen_urls = set()
                for item in items:
                    if len(results) >= max_results:
                        break
                    try:
                        a_tag = item.find("a", href=re.compile(r"/view_video\.php"))
                        if not a_tag:
                            a_tag = item.find("a", href=re.compile(r"/video"))
                        if not a_tag:
                            continue
                        href = a_tag.get("href", "")
                        if href.startswith("/"):
                            full_url = urljoin(self.base_url, href)
                        else:
                            full_url = href
                        if full_url in seen_urls or "javascript" in href:
                            continue
                        seen_urls.add(full_url)

                        title = a_tag.get("title", "") or a_tag.get_text(strip=True)

                        img_tag = item.find("img")
                        cover = ""
                        if img_tag:
                            cover = img_tag.get("data-src") or img_tag.get("src") or ""
                            if cover.startswith("//"):
                                cover = "https:" + cover

                        duration = ""
                        dur_tag = item.select_one("span.duration, div.duration, var.duration")
                        if dur_tag:
                            duration = dur_tag.get_text(strip=True)

                        if title and full_url:
                            results.append(VideoResult(
                                title=title.strip()[:200],
                                url=full_url,
                                cover=cover,
                                source="pornhub",
                                source_label="\U0001f31f PornHub",
                                duration=duration,
                            ))
                    except Exception as e:
                        logger.debug("pornhub item error: %s", e)
                        continue

        except ImportError:
            logger.error("pornhub: curl_cffi not installed")
        except Exception as e:
            logger.debug("pornhub search error: %s", e)

        return results

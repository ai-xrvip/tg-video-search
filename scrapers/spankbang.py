"""scrapers/spankbang.py — Scraper for spankbang.com (国际/欧美)"""
import asyncio
import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import config
from scrapers.base import BaseScraper, VideoResult

logger = logging.getLogger(__name__)


class SpankbangScraper(BaseScraper):
    name = "spankbang"
    label = "\U0001f30d SpankBang"
    base_url = "https://spankbang.com"
    timeout = 10.0

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        results = []
        try:
            from curl_cffi.requests import AsyncSession

            search_url = f"{self.base_url}/s/{keyword.replace(' ', '+')}"
            headers = {
                "User-Agent": config.USER_AGENT,
                "Referer": self.base_url,
                "Accept-Language": "en-US,en;q=0.9",
            }

            async with AsyncSession(headers=headers, timeout=self.timeout, impersonate="chrome124") as client:
                resp = await client.get(search_url, follow_redirects=True)
                soup = BeautifulSoup(resp.text, "html.parser")

                items = soup.select("div.video-item, div.videobox, a[href*=\"/video/\"]")
                seen_urls = set()
                for item in items:
                    if len(results) >= max_results:
                        break
                    try:
                        a_tag = item if item.name == "a" and "/video/" in item.get("href", "") else item.find("a", href=re.compile(r"/video/"))
                        if not a_tag:
                            continue
                        href = a_tag.get("href", "")
                        full_url = urljoin(self.base_url, href) if not href.startswith("http") else href
                        if full_url in seen_urls or not full_url.startswith("http"):
                            continue
                        seen_urls.add(full_url)

                        title = a_tag.get("title", "") or a_tag.get("data-title", "")
                        if not title:
                            title_tag = item.select_one("span.title, div.title, .name")
                            title = title_tag.get_text(strip=True) if title_tag else ""

                        img_tag = item.find("img")
                        cover = img_tag.get("data-src") or img_tag.get("src") or "" if img_tag else ""
                        if cover.startswith("//"):
                            cover = "https:" + cover

                        duration = ""
                        dur_tag = item.select_one("span.duration, div.duration, .length")
                        if dur_tag:
                            duration = dur_tag.get_text(strip=True)

                        if title and full_url:
                            title = re.sub(r"\s+", " ", title).strip()[:200]
                            results.append(VideoResult(
                                title=title, url=full_url, cover=cover,
                                source="spankbang", source_label="\U0001f30d SpankBang",
                                duration=duration,
                            ))
                    except Exception as e:
                        logger.debug("spankbang item error: %s", e)
                        continue

        except ImportError:
            logger.error("spankbang: curl_cffi not installed")
        except Exception as e:
            logger.debug("spankbang search error: %s", e)

        return results

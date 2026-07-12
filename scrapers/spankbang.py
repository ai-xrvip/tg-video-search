"""scrapers/spankbang.py — Scraper for spankbang.com (国际/欧美)"""
import asyncio
import logging
import re
from urllib.parse import urljoin, urlencode

from bs4 import BeautifulSoup

from config import config
from scrapers.base import BaseScraper, VideoResult, _get_shared_client

logger = logging.getLogger(__name__)


class SpankbangScraper(BaseScraper):
    name = "spankbang"
    label = "\U0001f30d SpankBang"
    base_url = "https://spankbang.com"
    timeout = 10.0

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        results = []
        try:
            search_url = f"{self.base_url}/s/{keyword.replace(' ', '+')}"
            headers = {
                "User-Agent": config.USER_AGENT,
                "Referer": self.base_url,
                "Accept-Language": "en-US,en;q=0.9",
            }

            client = await _get_shared_client()
            resp = await client.get(search_url, headers=headers, follow_redirects=True)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Find video items
            items = soup.select("div.video-item, div.videobox, div[class*=\"video\"] a[href*=\"/video/\"]")
            if not items:
                items = soup.select("a[href*=\"/video/\"]")

            seen_urls = set()
            for item in items:
                if len(results) >= max_results:
                    break
                try:
                    a_tag = item if item.name == "a" and item.get("href", "").startswith("/video/") else item.find("a", href=re.compile(r"/video/"))
                    if not a_tag:
                        continue
                    href = a_tag.get("href", "")
                    if not href.startswith("http"):
                        full_url = urljoin(self.base_url, href)
                    else:
                        full_url = href

                    if full_url in seen_urls or not full_url.startswith("http"):
                        continue
                    seen_urls.add(full_url)

                    # Extract title
                    title = a_tag.get("title", "") or a_tag.get("data-title", "")
                    if not title:
                        title_tag = item.select_one("span.title, div.title, .name")
                        title = title_tag.get_text(strip=True) if title_tag else ""

                    # Cover image
                    img_tag = item.find("img")
                    cover = ""
                    if img_tag:
                        cover = img_tag.get("data-src") or img_tag.get("src") or ""
                        if cover.startswith("//"):
                            cover = "https:" + cover

                    # Duration
                    duration = ""
                    dur_tag = item.select_one("span.duration, div.duration, .length")
                    if dur_tag:
                        duration = dur_tag.get_text(strip=True)

                    if title and full_url:
                        title = re.sub(r'\s+', ' ', title).strip()[:200]
                        results.append(VideoResult(
                            title=title,
                            url=full_url,
                            cover=cover,
                            source="spankbang",
                            source_label="\U0001f30d SpankBang",
                            duration=duration,
                        ))
                except Exception as e:
                    logger.debug("spankbang item parse error: %s", e)
                    continue

        except Exception as e:
            if "timeout" in str(e).lower():
                logger.warning("spankbang search timed out")
            else:
                logger.debug("spankbang search error: %s", e)

        return results

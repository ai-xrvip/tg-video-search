"""scrapers/xchina.py — Scraper for xchina.co (国产) using curl_cffi"""
import asyncio
import logging
import re
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from config import config
from scrapers.base import BaseScraper, VideoResult

logger = logging.getLogger(__name__)


class XChinaScraper(BaseScraper):
    name = "xchina"
    label = "国产"
    base_url = config.XCHINA_BASE_URL
    timeout = config.SEARCH_TIMEOUT_XCHINA

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        results = []
        try:
            from curl_cffi.requests import AsyncSession

            headers = {
                "User-Agent": config.USER_AGENT,
                "Referer": self.base_url,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }

            async with AsyncSession(
                headers=headers,
                timeout=self.timeout,
                impersonate="chrome124",
            ) as client:
                quoted = quote(keyword)
                page = 1
                seen_urls = set()

                while len(results) < max_results and page <= 3:
                    search_url = f"{self.base_url}/photos/search?q={quoted}&page={page}"
                    resp = await client.get(search_url)
                    if resp.status_code != 200:
                        break

                    soup = BeautifulSoup(resp.text, "html.parser")
                    items = soup.select("div.col-md-4, div.col-sm-6, div.item, div.photo-item, a.photo-link")
                    if not items:
                        items = soup.find_all("a", href=re.compile(r"/photos/\d+"))

                    if not items:
                        break

                    found = 0
                    for item in items:
                        if len(results) >= max_results:
                            break

                        a_tag = item if item.name == "a" and item.get("href") else item.find("a", href=re.compile(r"/photos/\d+"))
                        if not a_tag:
                            continue
                        href = a_tag.get("href", "")
                        if not href or "/photos/" not in href:
                            continue
                        full_url = href if href.startswith("http") else f"{self.base_url}{href}"
                        if full_url in seen_urls:
                            continue
                        seen_urls.add(full_url)
                        found += 1

                        title = a_tag.get("title", "") or a_tag.get_text(strip=True)
                        img_tag = item.find("img")
                        cover = ""
                        if img_tag:
                            cover = img_tag.get("data-src") or img_tag.get("src") or ""
                            if cover and cover.startswith("//"):
                                cover = "https:" + cover

                        if title:
                            title = re.sub(r"\s+", " ", title).strip()
                            results.append(VideoResult(
                                title=title[:200],
                                url=full_url,
                                cover=cover,
                                source="xchina",
                                source_label="国产",
                            ))

                    if found == 0:
                        break
                    page += 1
                    await asyncio.sleep(0.3)

        except ImportError:
            logger.error("xchina: curl_cffi not installed")
        except Exception as e:
            logger.debug("xchina search error: %s", e)

        return results


async def get_video_detail(url: str) -> Optional[str]:
    """For xchina photo galleries, just return the URL as-is."""
    return url

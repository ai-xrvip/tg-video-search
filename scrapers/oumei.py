"""scrapers/oumei.py — Scraper for xvideos.com (欧美)"""
import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import config
from scrapers.base import BaseScraper, VideoResult

logger = logging.getLogger(__name__)


class OumeiScraper(BaseScraper):
    name = "oumei"
    label = "\U0001f30f \u6b27\u7f8e"
    base_url = config.OUMEI_BASE_URL
    timeout = config.SEARCH_TIMEOUT_OUMEI

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        results = []
        try:
            from curl_cffi.requests import AsyncSession

            params = {"k": keyword}
            headers = {
                "User-Agent": config.USER_AGENT,
                "Referer": self.base_url,
                "Accept-Language": "en-US,en;q=0.9",
            }
            async with AsyncSession(
                headers=headers,
                timeout=self.timeout,
                impersonate="chrome124",
            ) as client:
                resp = await client.get(self.base_url, params=params)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")

                items = soup.select("div.thumb, div.thumb-block, div.mozaique div.thumb")
                seen_urls = set()

                for item in items:
                    if len(results) >= max_results:
                        break
                    try:
                        a_tag = item.find("a", href=True)
                        if not a_tag:
                            continue
                        href = a_tag.get("href", "")
                        if not href or "/video" not in href:
                            continue
                        full_url = urljoin(self.base_url, href)
                        if full_url in seen_urls:
                            continue
                        seen_urls.add(full_url)

                        img_tag = item.find("img")
                        cover = ""
                        if img_tag:
                            cover = img_tag.get("data-src") or img_tag.get("src") or ""

                        title = (a_tag.get("title", "") or
                                 (img_tag.get("alt", "") if img_tag else "") or
                                 a_tag.get_text(strip=True))

                        duration = ""
                        dur_tag = item.find(class_=re.compile(r"duration", re.I))
                        if dur_tag:
                            duration = dur_tag.get_text(strip=True)

                        if title and full_url:
                            results.append(VideoResult(
                                title=title.strip()[:200],
                                url=full_url,
                                cover=cover,
                                source="oumei",
                                source_label="\U0001f30f \u6b27\u7f8e",
                                duration=duration,
                            ))
                    except Exception as e:
                        logger.debug("oumei item parse error: %s", e)
                        continue

        except ImportError:
            logger.error("oumei: curl_cffi not installed")
        except Exception as e:
            if "timeout" in str(e).lower():
                logger.warning("oumei search timed out")
            else:
                logger.error("oumei search error: %s", e)

        return results


async def get_video_detail(url: str) -> Optional[dict]:
    """Extract video URLs from an xvideos.com page."""
    try:
        from curl_cffi.requests import AsyncSession
        headers = {"User-Agent": config.USER_AGENT, "Referer": config.OUMEI_BASE_URL}
        async with AsyncSession(headers=headers, timeout=config.SEARCH_TIMEOUT_OUMEI, impersonate="chrome124") as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
            urls = {}
            # Pattern: html5player.setVideoUrlHigh('https://...')
            pat = re.compile(r"""html5player\.setVideoUrl(?:High|Low)?\s*\(\s*[\"'""]([^\"'""]+)[\"'""]\s*\)""")
            for m in pat.finditer(html):
                vid_url = m.group(1)
                if vid_url.startswith("//"):
                    vid_url = "https:" + vid_url
                for q in ["1080p", "720p", "480p", "360p", "240p"]:
                    if q in vid_url:
                        urls[q] = vid_url
                        break
                if not urls:
                    urls["mp4"] = vid_url
            return urls if urls else None
    except ImportError:
        logger.error("oumei: curl_cffi not installed")
    except Exception as e:
        logger.error("oumei detail error: %s", e)
    return None

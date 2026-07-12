"""scrapers/jav_id.py — Scraper for JAV ID / 番号 search via jav321.com"""
import asyncio
import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from config import config
from scrapers.base import BaseScraper, VideoResult

logger = logging.getLogger(__name__)


class JavIdScraper(BaseScraper):
    name = "jav_id"
    label = "\U0001f4d7 番号"
    base_url = "https://www.jav321.com"
    timeout = 15.0
    SEARCH_URL = "https://www.jav321.com/search"

    async def search(self, keyword: str, max_results: int = 15) -> list[VideoResult]:
        results = []
        # Only activate for keyword-like patterns: letters-digits
        if not re.search(r'[A-Za-z]{2,}[-]\d{2,}', keyword):
            return results

        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": config.USER_AGENT, "Referer": "https://www.jav321.com/"},
                timeout=self.timeout,
                follow_redirects=True,
                **self._get_httpx_kwargs(),
            ) as client:
                resp = await client.post(self.SEARCH_URL, data={"sn": keyword})
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

                panels = soup.select("div.panel.panel-default")
                for panel in panels:
                    if len(results) >= max_results:
                        break
                    try:
                        title_tag = panel.select_one("div.panel-heading strong a")
                        if not title_tag:
                            continue
                        href = title_tag.get("href", "")
                        if href.startswith("/"):
                            href = "https://www.jav321.com" + href
                        title = title_tag.get_text(strip=True)

                        img_tag = panel.select_one("div.panel-body img")
                        cover = ""
                        if img_tag:
                            cover = img_tag.get("src") or ""

                        detail_div = panel.select_one("div.panel-body div")
                        detail_text = detail_div.get_text("\n", strip=True) if detail_div else ""

                        date = ""
                        duration = ""
                        dm = re.search(r'(\d{4}-\d{2}-\d{2})', detail_text)
                        if dm:
                            date = dm.group(1)
                        dur_m = re.search(r'(\d{2,3})\s*分', detail_text)
                        if dur_m:
                            duration = f"{dur_m.group(1)} min"

                        if title and href:
                            results.append(VideoResult(
                                title=title.strip()[:200],
                                url=href,
                                cover=cover,
                                source="jav_id",
                                source_label="\U0001f4d7 番号",
                                duration=duration,
                                date=date,
                            ))
                    except Exception as e:
                        logger.debug("jav_id item parse error: %s", e)
                        continue

        except asyncio.TimeoutError:
            logger.warning("jav_id search timed out")
        except httpx.HTTPStatusError as e:
            logger.warning("jav_id HTTP error: %s", e)
        except Exception as e:
            logger.error("jav_id search error: %s", e)

        return results


async def get_video_detail(url: str) -> Optional[dict]:
    """Extract video info from jav321.com detail page."""
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": config.USER_AGENT, "Referer": "https://www.jav321.com/"},
            timeout=15.0,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            magnets = []
            for a_tag in soup.select("a[href^='magnet:']"):
                magnet = a_tag.get("href", "")
                if magnet:
                    magnets.append(magnet)
            imgs = []
            for img in soup.select("div#sample-waterfall img"):
                src = img.get("src") or img.get("data-src") or ""
                if src:
                    imgs.append(src)
            result = {}
            if magnets:
                result["magnets"] = magnets
            if imgs:
                result["images"] = imgs
            return result if result else None
    except Exception as e:
        logger.error("jav_id detail error: %s", e)
    return None

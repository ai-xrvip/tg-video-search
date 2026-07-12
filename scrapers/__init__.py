"""scrapers/__init__.py — Unified search interface with parallel source execution."""
from __future__ import annotations

import asyncio
import glob
import logging
import os

from scrapers.base import BaseScraper, VideoResult, register_scraper, get_scraper, list_scrapers

logger = logging.getLogger(__name__)

CATEGORIES: dict[str, dict] = {}
CATEGORY_LABELS: dict[str, str] = {}
_built = False


def _discover_scrapers():
    """Discover all scraper modules by importing them."""
    mod_paths = glob.glob(os.path.join(os.path.dirname(__file__), "*.py"))
    modules = [
        os.path.basename(f)[:-3] for f in mod_paths
        if os.path.isfile(f)
        and f.endswith(".py")
        and not f.endswith("__init__.py")
        and not f.endswith("base.py")
    ]
    for mod_name in modules:
        try:
            __import__(f"scrapers.{mod_name}", fromlist=[""])
            logger.debug("Discovered scraper module: %s", mod_name)
        except ImportError as e:
            logger.warning("Failed to load scraper %s (missing dep): %s", mod_name, e)
        except Exception as e:
            logger.warning("Failed to load scraper %s: %s", mod_name, e)


def _build_categories():
    """Auto-build CATEGORIES dict from registered scrapers."""
    global CATEGORIES, CATEGORY_LABELS
    sources = list_scrapers()
    CATEGORIES = {
        "all": {"label": "\U0001f52a 全部", "sources": list(sources)},
    }
    for name in sources:
        cls = get_scraper(name)
        if cls:
            CATEGORIES[name] = {"label": cls.label, "sources": [name]}

    CATEGORY_LABELS = {k: v["label"] for k, v in CATEGORIES.items()}


def _ensure_built():
    global _built
    if not _built:
        _discover_scrapers()
        _build_categories()
        _built = True


async def search_all(keyword: str, category: str = "all", max_results: int = 30) -> list[dict]:
    """Search across all video sources in parallel.

    Runs all sources concurrently, collects results as they complete,
    and returns deduplicated, sorted results.
    """
    _ensure_built()

    cat_config = CATEGORIES.get(category, CATEGORIES.get("all", {}))
    source_names = cat_config.get("sources", [])
    if not source_names:
        return []

    results_per_source = max(max_results // max(len(source_names), 1), 5)
    tasks: dict[str, asyncio.Task] = {}

    for src_name in source_names:
        cls = get_scraper(src_name)
        if cls is None:
            logger.warning("No scraper registered for %s, skipping", src_name)
            continue
        scraper = cls()
        tasks[src_name] = asyncio.create_task(
            scraper.search_with_retry(keyword, results_per_source)
        )

    if not tasks:
        return []

    all_results: list[VideoResult] = []
    source_order = {s: i for i, s in enumerate(source_names)}

    # Wait for all tasks with a hard timeout (15s)
    done, pending = await asyncio.wait(
        tasks.values(),
        timeout=15.0,
        return_when=asyncio.ALL_COMPLETED,
    )

    # Cancel remaining
    for t in pending:
        t.cancel()

    # Collect results from completed tasks
    for task in done:
        src_name = next(n for n, t in tasks.items() if t is task)
        try:
            result = task.result()
            if result:
                all_results.extend(result)
                logger.debug("%s returned %d results", src_name, len(result))
        except asyncio.CancelledError:
            logger.debug("%s search was cancelled", src_name)
        except Exception as e:
            logger.debug("%s search failed: %s", src_name, e)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique: list[VideoResult] = []
    for r in all_results:
        if r.url and r.url not in seen_urls:
            seen_urls.add(r.url)
            unique.append(r)

    # Sort by source order
    unique.sort(key=lambda r: source_order.get(r.source, 99))

    return [r.__dict__ for r in unique[:max_results]]


async def search_category(keyword: str, category: str, max_results: int = 15) -> list[dict]:
    """Search within a single category/source."""
    return await search_all(keyword, category, max_results)

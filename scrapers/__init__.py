"""scrapers/__init__.py ? Unified search interface with module auto-discovery.

Pattern source:
- PaulSonOfLars/tgbot: module auto-loading via glob + __all__
- Selutario/videogram: scraper abstraction layer
"""
from __future__ import annotations

import asyncio
import glob
import logging
import os

from scrapers.base import BaseScraper, VideoResult, register_scraper, get_scraper, list_scrapers

logger = logging.getLogger(__name__)

# Registry is managed by base.py via register_scraper()/get_scraper()/list_scrapers()
# Subclasses auto-register via BaseScraper.__init_subclass__

# ===== Module auto-discovery (PaulSonOfLars pattern) =====
# Scrapers are auto-discovered; just drop a .py in scrapers/ and it loads.

CATEGORIES: dict[str, dict] = {}
CATEGORY_LABELS: dict[str, str] = {}
_built = False


def _discover_scrapers():
    """Discover all scraper modules by importing them.
    Subclasses register themselves via BaseScraper.__init_subclass__ -> register_scraper().
    """
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
        except Exception as e:
            logger.warning("Failed to load scraper %s: %s", mod_name, e)


def _build_categories():
    """Auto-build CATEGORIES dict from registered scrapers."""
    global CATEGORIES, CATEGORY_LABELS
    sources = list_scrapers()
    CATEGORIES = {
        "all": {"label": "\U0001f52a \u5168\u90e8", "sources": list(sources)},
    }
    for name in sources:
        cls = get_scraper(name)
        if cls:
            CATEGORIES[name] = {"label": cls.label, "sources": [name]}

    CATEGORY_LABELS = {k: v["label"] for k, v in CATEGORIES.items()}
    # Ensure jav_id is always available
    if "jav_id" not in CATEGORIES:
        CATEGORIES["jav_id"] = {"label": "\U0001f4d7 \u756a\u53f7", "sources": ["jav_id"]}
        CATEGORY_LABELS["jav_id"] = "\U0001f4d7 \u756a\u53f7"


def _ensure_built():
    global _built
    if not _built:
        _discover_scrapers()
        _build_categories()
        _built = True


async def search_all(keyword: str, category: str = "all", max_results: int = 30) -> list[dict]:
    """Search across all (or selected category) video sources.

    Args:
        keyword: Search term
        category: 'all', 'guochan', 'hanime', 'jav', 'oumei', 'jav_id'
        max_results: Maximum total results across all sources

    Returns:
        List of result dicts (compatible with existing inline/command handlers)
    """
    _ensure_built()

    cat_config = CATEGORIES.get(category, CATEGORIES.get("all", {}))
    source_names = cat_config.get("sources", [])
    if not source_names:
        return []

    results_per_source = max(max_results // len(source_names), 5)
    tasks = []

    for src_name in source_names:
        cls = get_scraper(src_name)
        if cls is None:
            logger.warning("No scraper registered for %s, skipping", src_name)
            continue
        scraper = cls()
        tasks.append(scraper.search_with_retry(keyword, results_per_source))

    all_results: list[VideoResult] = []
    done = await asyncio.gather(*tasks, return_exceptions=True)

    for src_name, result in zip(
        [s for s in source_names if get_scraper(s) is not None], done
    ):
        if isinstance(result, Exception):
            logger.error("%s search failed: %s", src_name, result)
            continue
        if result:
            all_results.extend(result)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique: list[VideoResult] = []
    for r in all_results:
        if r.url and r.url not in seen_urls:
            seen_urls.add(r.url)
            unique.append(r)

    # Sort: source order preserved
    source_order = {s: i for i, s in enumerate(source_names)}
    unique.sort(key=lambda r: source_order.get(r.source, 99))

    # Limit total with balanced interleave
    if len(unique) > max_results:
        by_source: dict[str, list[VideoResult]] = {}
        for r in unique:
            by_source.setdefault(r.source, []).append(r)

        balanced: list[VideoResult] = []
        max_per = max(len(v) for v in by_source.values()) if by_source else 0
        for i in range(max_per):
            for src in source_names:
                items = by_source.get(src, [])
                if i < len(items):
                    balanced.append(items[i])
                    if len(balanced) >= max_results:
                        break
            if len(balanced) >= max_results:
                break
        unique = balanced[:max_results]

    return [r.__dict__ for r in unique]


async def search_category(keyword: str, category: str, max_results: int = 15) -> list[dict]:
    """Search within a single category/source."""
    return await search_all(keyword, category, max_results)

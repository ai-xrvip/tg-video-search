"""scrapers/__init__.py — Unified search interface with module auto-discovery."""
from __future__ import annotations

import asyncio
import glob
import logging
import os

from scrapers.base import BaseScraper, VideoResult, register_scraper, get_scraper, list_scrapers

logger = logging.getLogger(__name__)

# ===== Module auto-discovery =====
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
        except ImportError as e:
            logger.warning("Failed to load scraper %s (missing dep): %s", mod_name, e)
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
    """Search across all video sources with race-based early return.

    Returns results as soon as sources complete, with a hard overall timeout.
    This ensures the bot responds quickly even if some sources are slow.
    """
    _ensure_built()

    cat_config = CATEGORIES.get(category, CATEGORIES.get("all", {}))
    source_names = cat_config.get("sources", [])
    if not source_names:
        return []

    results_per_source = max(max_results // max(len(source_names), 1), 5)
    tasks: list[tuple[str, asyncio.Task]] = []

    for src_name in source_names:
        cls = get_scraper(src_name)
        if cls is None:
            logger.warning("No scraper registered for %s, skipping", src_name)
            continue
        scraper = cls()
        task = asyncio.create_task(scraper.search_with_retry(keyword, results_per_source))
        tasks.append((src_name, task))

    if not tasks:
        return []

    all_results: list[VideoResult] = []
    source_order = {s: i for i, s in enumerate(source_names)}

    # Phase 1: Wait up to 4s for ANY results
    pending = {name: task for name, task in tasks}
    
    if pending:
        done_set, pending_set = await asyncio.wait(
            list(pending.values()),
            timeout=5.0,
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in done_set:
            task_name = next(n for n, t in pending.items() if t is task)
            try:
                result = task.result()
                if result:
                    all_results.extend(result)
            except Exception as e:
                logger.debug("%s search failed: %s", task_name, e)
            del pending[task_name]

        for t in pending_set:
            t.cancel()

    # Phase 2: Quick collect - wait 2s more for remaining fast sources
    if pending:
        await asyncio.sleep(2.0)
        still_pending = dict(pending)
        for src_name, task in still_pending.items():
            if task.done():
                try:
                    result = task.result()
                    if result:
                        all_results.extend(result)
                except Exception:
                    pass
                del pending[src_name]

    # Cancel anything still running
    for _, task in pending.items():
        task.cancel()

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

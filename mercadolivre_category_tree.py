# -*- coding: utf-8 -*-
"""
Mercado Livre category tree: load, cache (memory + DB) and fuzzy search.

Loads the full MLB category tree on app startup using BFS with a thread pool.
Persists to DB for 60-day cache across deploys. Fuzzy search via rapidfuzz.
"""

import asyncio
import logging
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from rapidfuzz import fuzz, process

logger = logging.getLogger("ads_generator.category_tree")

# ── In-memory cache ─────────────────────────────────────────────────────────

_tree_cache = None  # type: Optional[Dict[str, Any]]
_tree_status = "unavailable"  # "unavailable" | "loading" | "ready"

CACHE_TTL_DAYS = 60
MAX_WORKERS = 8


def get_tree_status():
    # type: () -> str
    return _tree_status


def get_tree():
    # type: () -> Optional[Dict[str, Any]]
    return _tree_cache


def set_tree(tree):
    # type: (Dict[str, Any]) -> None
    global _tree_cache, _tree_status
    _tree_cache = tree
    _tree_status = "ready"


def set_tree_loading():
    global _tree_status
    _tree_status = "loading"


# ── Text normalisation ──────────────────────────────────────────────────────

def _normalize(text):
    # type: (str) -> str
    """Lowercase, strip accents."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.category(c).startswith("M")).lower()


# ── Fuzzy search ─────────────────────────────────────────────────────────────

def search_categories(query, limit=20):
    # type: (str, int) -> Dict[str, Any]
    """Search the in-memory tree with fuzzy matching.

    Returns dict with keys: results, total_found, showing, has_more.
    """
    tree = _tree_cache
    if not tree:
        return {"results": [], "total_found": 0, "showing": 0, "has_more": False}

    query_norm = _normalize(query)
    if not query_norm.strip():
        return {"results": [], "total_found": 0, "showing": 0, "has_more": False}

    # Build choices dict: node_id -> searchable text (name + path)
    choices = {}
    for node_id, node in tree.items():
        choices[node_id] = _normalize(node["name"]) + " " + _normalize(node["path"])

    # rapidfuzz process.extract returns list of (match_str, score, key)
    # Use limit=0 (or None) to get all results above threshold
    all_matches = process.extract(
        query_norm,
        choices,
        scorer=fuzz.WRatio,
        limit=len(choices),
        score_cutoff=50,
    )

    total_found = len(all_matches)

    if limit > 0:
        matches = all_matches[:limit]
    else:
        matches = all_matches

    results = [
        {
            "id": node_id,
            "name": tree[node_id]["name"],
            "path": tree[node_id]["path"],
        }
        for _, _score, node_id in matches
    ]

    return {
        "results": results,
        "total_found": total_found,
        "showing": len(results),
        "has_more": total_found > len(results),
    }


# ── API loading ──────────────────────────────────────────────────────────────

def _fetch_category_sync(category_id):
    # type: (str) -> Optional[Dict[str, Any]]
    """Fetch a single category (synchronous, for thread pool). Public endpoint."""
    try:
        resp = httpx.get(
            "https://api.mercadolibre.com/categories/{}".format(category_id),
            timeout=15.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch category %s: %s", category_id, exc)
    return None


def _build_path(path_from_root):
    # type: (List[Dict]) -> str
    return " > ".join(p.get("name", "") for p in path_from_root)


async def load_tree_from_api(access_token):
    # type: (str) -> Dict[str, Any]
    """Load the full MLB category tree via BFS with thread pool.

    1. GET /sites/MLB -> root categories (requires token)
    2. BFS: GET /categories/{id} for each node (public, no token needed)

    Returns flat dict: {category_id: {id, name, path, children, leaf}}
    """
    set_tree_loading()
    logger.info("Starting full category tree load from ML API...")

    # Step 1: get root categories
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.mercadolibre.com/sites/MLB",
            headers={"Authorization": "Bearer {}".format(access_token)},
            timeout=15.0,
        )
    if resp.status_code != 200:
        raise RuntimeError("Failed to fetch /sites/MLB: status {}".format(resp.status_code))

    site_data = resp.json()
    root_categories = site_data.get("categories") or []
    if not root_categories:
        raise RuntimeError("No root categories found in /sites/MLB response")

    logger.info("Found %d root categories. Starting BFS...", len(root_categories))

    tree = {}  # type: Dict[str, Any]
    loop = asyncio.get_event_loop()

    # Seed BFS with root IDs
    queue = [cat["id"] for cat in root_categories]
    visited = set()
    level = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while queue:
            level += 1
            # Filter already visited
            to_fetch = [cid for cid in queue if cid not in visited]
            visited.update(to_fetch)

            if not to_fetch:
                break

            logger.info("BFS level %d: fetching %d categories...", level, len(to_fetch))

            # Fetch all categories in this level in parallel
            futures = [
                loop.run_in_executor(executor, _fetch_category_sync, cid)
                for cid in to_fetch
            ]
            results = await asyncio.gather(*futures)

            next_queue = []
            for cat_data in results:
                if cat_data is None:
                    continue

                cat_id = cat_data.get("id", "")
                children = cat_data.get("children_categories") or []
                path_from_root = cat_data.get("path_from_root") or []

                tree[cat_id] = {
                    "id": cat_id,
                    "name": cat_data.get("name", ""),
                    "path": _build_path(path_from_root),
                    "children": [c["id"] for c in children],
                    "leaf": len(children) == 0,
                }

                # Enqueue children for next BFS level
                for child in children:
                    child_id = child["id"]
                    if child_id not in visited:
                        next_queue.append(child_id)

            queue = next_queue

    logger.info("Category tree loaded: %d nodes total.", len(tree))
    return tree


# ── DB cache helpers ─────────────────────────────────────────────────────────

def load_tree_from_db(db_session):
    """Load tree from DB if cache is still valid. Returns tree dict or None."""
    # Import here to avoid circular imports — model is defined in app.py
    from app import MercadoLivreCategoryTreeCache

    row = (
        db_session.query(MercadoLivreCategoryTreeCache)
        .filter(
            MercadoLivreCategoryTreeCache.site_id == "MLB",
            MercadoLivreCategoryTreeCache.expires_at > datetime.utcnow(),
        )
        .first()
    )
    if row and row.tree_data:
        logger.info("Loaded category tree from DB cache (%d nodes, expires %s).", row.node_count, row.expires_at)
        return row.tree_data
    return None


def save_tree_to_db(db_session, tree):
    """Persist tree to DB with 60-day expiry."""
    from app import MercadoLivreCategoryTreeCache

    now = datetime.utcnow()
    row = (
        db_session.query(MercadoLivreCategoryTreeCache)
        .filter(MercadoLivreCategoryTreeCache.site_id == "MLB")
        .first()
    )
    if row:
        row.tree_data = tree
        row.node_count = len(tree)
        row.loaded_at = now
        row.expires_at = now + timedelta(days=CACHE_TTL_DAYS)
    else:
        row = MercadoLivreCategoryTreeCache(
            site_id="MLB",
            tree_data=tree,
            node_count=len(tree),
            loaded_at=now,
            expires_at=now + timedelta(days=CACHE_TTL_DAYS),
        )
        db_session.add(row)
    db_session.commit()
    logger.info("Saved category tree to DB cache (%d nodes, expires %s).", len(tree), row.expires_at)


# ── Startup initialisation ──────────────────────────────────────────────────

def _get_any_ml_token_from_db(db_session):
    # type: (...) -> Optional[Dict[str, Any]]
    """Return the first ML account found in any user's config, or None."""
    from app import UserConfig

    configs = db_session.query(UserConfig).all()
    for cfg in configs:
        data = cfg.data or {}
        accounts = data.get("ml_accounts") or []
        for acc in accounts:
            if acc.get("access_token"):
                return acc
    return None


async def initialise_category_tree(session_factory):
    """Called on app startup. Tries DB cache first, then API."""
    db = session_factory()
    try:
        # Try DB cache
        cached = load_tree_from_db(db)
        if cached:
            set_tree(cached)
            return

        # Need to load from API — get a token
        account = _get_any_ml_token_from_db(db)
        if not account:
            logger.warning("No ML account with token found. Category tree will load on first search.")
            return

        # Refresh token if needed
        try:
            from config import settings
            import mercadolivre_service
            access_token, updated = await mercadolivre_service.get_valid_access_token(
                account=account,
                client_id=settings.ml_client_id,
                client_secret=settings.ml_client_secret,
            )
        except Exception as exc:
            logger.warning("Failed to get ML access token for tree loading: %s", exc)
            return

        tree = await load_tree_from_api(access_token)
        save_tree_to_db(db, tree)
        set_tree(tree)
    except Exception as exc:
        logger.error("Failed to initialise category tree: %s", exc, exc_info=True)
    finally:
        db.close()


async def ensure_tree_loaded(session_factory, access_token):
    """On-demand loading: if tree is not ready, load it now."""
    if _tree_cache is not None:
        return

    db = session_factory()
    try:
        cached = load_tree_from_db(db)
        if cached:
            set_tree(cached)
            return

        tree = await load_tree_from_api(access_token)
        save_tree_to_db(db, tree)
        set_tree(tree)
    finally:
        db.close()


# ── Path migration for old category mappings ─────────────────────────────────

async def migrate_category_paths(category_ids):
    # type: (List[str]) -> Dict[str, str]
    """Fetch path_from_root for a list of category IDs.

    Returns dict: {category_id: "Root > Sub > Leaf"}.
    Uses the in-memory tree first, falls back to individual API calls.
    """
    result = {}
    to_fetch = []

    # Check in-memory tree first
    tree = _tree_cache
    for cat_id in category_ids:
        if tree and cat_id in tree:
            result[cat_id] = tree[cat_id]["path"]
        else:
            to_fetch.append(cat_id)

    # Fetch remaining from API
    if to_fetch:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                loop.run_in_executor(executor, _fetch_category_sync, cid)
                for cid in to_fetch
            ]
            responses = await asyncio.gather(*futures)
            for cat_data in responses:
                if cat_data:
                    cat_id = cat_data.get("id", "")
                    path_from_root = cat_data.get("path_from_root") or []
                    result[cat_id] = _build_path(path_from_root)

    return result

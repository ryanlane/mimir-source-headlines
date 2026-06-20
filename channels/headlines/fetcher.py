"""NewsAPI fetcher for Mimir Headlines channel.

All functions are synchronous — wrap with asyncio.to_thread() in channel.py.
Free tier supports up to 100 requests/day; with a 60-minute cache that's
well within limits for any number of screens.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

_BASE = "https://newsapi.org/v2"
_TIMEOUT = 10

logger = logging.getLogger("mimir.channels.headlines.fetcher")


def validate_api_key(api_key: str) -> Dict[str, Any]:
    """Test the API key with a minimal request."""
    try:
        r = requests.get(
            f"{_BASE}/top-headlines",
            params={"apiKey": api_key, "country": "us", "pageSize": 1},
            timeout=_TIMEOUT,
        )
        data = r.json()
        if r.status_code == 200 and data.get("status") == "ok":
            return {"valid": True}
        return {"valid": False, "error": data.get("message", f"HTTP {r.status_code}")}
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


def fetch_headlines(
    api_key: str,
    category: str = "general",
    query: str = "",
    country: str = "us",
    language: str = "en",
    sort_by: str = "publishedAt",
    page_size: int = 10,
) -> List[Dict[str, Any]]:
    """Fetch articles from NewsAPI.

    Uses /everything for keyword queries (supports sort/language), and
    /top-headlines for category browsing (supports country).
    """
    if query.strip():
        endpoint = f"{_BASE}/everything"
        params: Dict[str, Any] = {
            "apiKey": api_key,
            "q": query.strip(),
            "sortBy": sort_by,
            "language": language,
            "pageSize": page_size,
        }
    else:
        endpoint = f"{_BASE}/top-headlines"
        params = {
            "apiKey": api_key,
            "country": country,
            "pageSize": page_size,
        }
        if category and category != "general":
            params["category"] = category

    try:
        r = requests.get(endpoint, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        # Filter out removed/deleted articles
        return [a for a in articles if a.get("title") and a["title"] != "[Removed]"]
    except Exception as exc:
        logger.error("[Headlines] Fetch failed: %s", exc)
        raise


def fetch_image(url: str) -> Optional[bytes]:
    """Download an article image. Returns raw bytes or None on failure."""
    if not url or not url.startswith("http"):
        return None
    try:
        r = requests.get(
            url,
            timeout=8,
            stream=True,
            headers={"User-Agent": "Mozilla/5.0 MimirDisplay/1.0"},
        )
        r.raise_for_status()
        data = r.content
        if len(data) > 200:
            return data
    except Exception as exc:
        logger.debug("[Headlines] Image fetch failed %s: %s", url, exc)
    return None

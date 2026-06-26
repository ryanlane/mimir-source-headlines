from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .mimir_utils import JsonCache, JsonStore, SettingsMixin


@dataclass
class HeadlinesFeed:
    """A single configured headlines feed (sub-channel)."""
    id: str
    name: str
    # Feed source
    category: str = "general"     # general | business | entertainment | health | science | sports | technology
    query: str = ""                # optional keyword search (overrides category)
    country: str = "us"           # ISO 3166-1 alpha-2 (top-headlines only, ignored when query is set)
    language: str = "en"          # ISO 639-1 language code
    sort_by: str = "publishedAt"  # publishedAt | popularity | relevancy
    article_index: int = 0        # which article in results to display (0 = top/latest)
    # Display
    layout: str = "auto"          # auto | landscape | portrait | square
    theme: str = "dark"           # dark | light | hc-dark | hc-light
    body_size: str = "md"         # sm | md | lg
    # Content toggles
    excerpt_field: str = "description"  # description | content
    show_image: bool = True
    show_excerpt: bool = True
    show_author: bool = True
    show_source: bool = True
    show_time: bool = True
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HeadlinesFeed":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def create(cls, data: Dict[str, Any]) -> "HeadlinesFeed":
        data = dict(data)
        if not data.get("id"):
            data["id"] = str(uuid.uuid4())
        if not data.get("created_at"):
            data["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return cls.from_dict(data)


@dataclass
class Settings(SettingsMixin):
    api_key: str = ""
    cache_minutes: int = 60


class FeedStore(JsonStore[HeadlinesFeed]):
    def _from_dict(self, d: Dict[str, Any]) -> HeadlinesFeed:
        return HeadlinesFeed.from_dict(d)

    def _to_dict(self, item: HeadlinesFeed) -> Dict[str, Any]:
        return item.to_dict()

    def _new_item(self, data: Dict[str, Any]) -> HeadlinesFeed:
        return HeadlinesFeed.create(data)


class HeadlineCache(JsonCache):
    """Caches NewsAPI responses per feed parameters to avoid excessive API calls."""

    def _make_key(self, feed: "HeadlinesFeed") -> str:
        q = feed.query.strip().lower()
        if q:
            return f"q:{q}:{feed.language}:{feed.sort_by}"
        return f"cat:{feed.category}:{feed.country}:{feed.language}"

    def get(self, feed: "HeadlinesFeed") -> Optional[Dict[str, Any]]:
        return self._data.get(self._make_key(feed))

    def needs_refresh(self, feed: "HeadlinesFeed", ttl_minutes: int) -> bool:
        entry = self.get(feed)
        if not entry:
            return True
        return time.time() - entry.get("fetched_at", 0) > ttl_minutes * 60

    def set(self, feed: "HeadlinesFeed", articles: List[Dict]) -> None:
        self._data[self._make_key(feed)] = {
            "articles": articles,
            "fetched_at": time.time(),
        }
        self._save()

    def get_articles(self, feed: "HeadlinesFeed") -> List[Dict]:
        entry = self.get(feed)
        return entry.get("articles", []) if entry else []

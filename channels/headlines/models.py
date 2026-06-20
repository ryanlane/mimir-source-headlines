from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


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
class Settings:
    api_key: str = ""
    cache_minutes: int = 60

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_public_dict(self) -> Dict[str, Any]:
        d = self.to_dict()
        if d.get("api_key"):
            d["api_key"] = "••••••••" + d["api_key"][-4:]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Settings":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


class FeedStore:
    """CRUD persistence for HeadlinesFeed list."""

    def __init__(self, path: Path):
        self._path = path
        self._feeds: List[HeadlinesFeed] = self._load()

    def _load(self) -> List[HeadlinesFeed]:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                feeds = []
                dirty = False
                for d in raw:
                    if not d.get("id"):
                        d["id"] = str(uuid.uuid4())
                        dirty = True
                    feeds.append(HeadlinesFeed.from_dict(d))
                if dirty:
                    self._path.write_text(json.dumps([f.to_dict() for f in feeds], indent=2))
                return feeds
            except Exception:
                pass
        return []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps([f.to_dict() for f in self._feeds], indent=2))

    def all(self) -> List[HeadlinesFeed]:
        return list(self._feeds)

    def get(self, feed_id: str) -> Optional[HeadlinesFeed]:
        return next((f for f in self._feeds if f.id == feed_id), None)

    def create(self, data: Dict[str, Any]) -> HeadlinesFeed:
        feed = HeadlinesFeed.create(data)
        self._feeds.append(feed)
        self._save()
        return feed

    def update(self, feed_id: str, data: Dict[str, Any]) -> Optional[HeadlinesFeed]:
        feed = self.get(feed_id)
        if not feed:
            return None
        data["id"] = feed_id
        data["created_at"] = feed.created_at
        updated = HeadlinesFeed.from_dict(data)
        self._feeds = [updated if f.id == feed_id else f for f in self._feeds]
        self._save()
        return updated

    def delete(self, feed_id: str) -> bool:
        before = len(self._feeds)
        self._feeds = [f for f in self._feeds if f.id != feed_id]
        if len(self._feeds) < before:
            self._save()
            return True
        return False


class HeadlineCache:
    """Caches NewsAPI responses per feed parameters to avoid excessive API calls."""

    def __init__(self, path: Path):
        self._path = path
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, default=str))

    def _key(self, feed: HeadlinesFeed) -> str:
        q = feed.query.strip().lower()
        if q:
            return f"q:{q}:{feed.language}:{feed.sort_by}"
        return f"cat:{feed.category}:{feed.country}:{feed.language}"

    def get(self, feed: HeadlinesFeed) -> Optional[Dict[str, Any]]:
        return self._data.get(self._key(feed))

    def needs_refresh(self, feed: HeadlinesFeed, ttl_minutes: int) -> bool:
        entry = self.get(feed)
        if not entry:
            return True
        return time.time() - entry.get("fetched_at", 0) > ttl_minutes * 60

    def set(self, feed: HeadlinesFeed, articles: List[Dict]) -> None:
        self._data[self._key(feed)] = {
            "articles": articles,
            "fetched_at": time.time(),
        }
        self._save()

    def get_articles(self, feed: HeadlinesFeed) -> List[Dict]:
        entry = self.get(feed)
        return entry.get("articles", []) if entry else []

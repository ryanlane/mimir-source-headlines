"""Tests for headlines channel models — verifies mimir_utils migration."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from channels.headlines.models import FeedStore, HeadlineCache, HeadlinesFeed, Settings


_FEED_DATA = {
    "name": "Tech News",
    "category": "technology",
    "country": "us",
    "language": "en",
}


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.api_key == ""
        assert s.cache_minutes == 60

    def test_to_public_dict_masks_key(self):
        s = Settings(api_key="newsapi-abc1234567890")
        pub = s.to_public_dict()
        assert pub["api_key"].startswith("••••••••")
        assert "newsapi" not in pub["api_key"]

    def test_from_dict_ignores_unknown(self):
        s = Settings.from_dict({"api_key": "x", "unknown": True, "cache_minutes": 30})
        assert s.api_key == "x"
        assert s.cache_minutes == 30

    def test_from_dict_partial(self):
        s = Settings.from_dict({"api_key": "only"})
        assert s.cache_minutes == 60


class TestFeedStore:
    @pytest.fixture
    def store(self, tmp_path):
        return FeedStore(tmp_path / "feeds.json")

    def test_empty_on_new_file(self, store):
        assert store.all() == []

    def test_create_assigns_uuid_and_created_at(self, store):
        f = store.create(_FEED_DATA)
        assert f.id
        assert f.created_at
        assert f.name == "Tech News"

    def test_get_by_id(self, store):
        f = store.create(_FEED_DATA)
        assert store.get(f.id).id == f.id

    def test_update_preserves_created_at(self, store):
        f = store.create(_FEED_DATA)
        updated = store.update(f.id, {**_FEED_DATA, "name": "Science News"})
        assert updated.name == "Science News"
        assert updated.created_at == f.created_at

    def test_delete(self, store):
        f = store.create(_FEED_DATA)
        assert store.delete(f.id) is True
        assert store.count() == 0

    def test_reload_from_disk(self, tmp_path):
        s1 = FeedStore(tmp_path / "feeds.json")
        f = s1.create(_FEED_DATA)
        s2 = FeedStore(tmp_path / "feeds.json")
        assert s2.get(f.id).name == "Tech News"

    def test_legacy_uuid_migration(self, tmp_path):
        p = tmp_path / "feeds.json"
        p.write_text(json.dumps([{"name": "Old Feed", "category": "general"}]))
        store = FeedStore(p)
        assert store.all()[0].id


class TestHeadlineCache:
    @pytest.fixture
    def cache(self, tmp_path):
        from channels.headlines.models import HeadlineCache
        return HeadlineCache(tmp_path / "cache.json")

    def _feed(self, **kwargs):
        data = {"id": "test", "name": "Test", **kwargs}
        return HeadlinesFeed.from_dict(data)

    def test_needs_refresh_when_empty(self, cache):
        feed = self._feed(category="technology", country="us", language="en", query="")
        assert cache.needs_refresh(feed, 60) is True

    def test_set_then_no_refresh(self, cache):
        feed = self._feed(category="technology", country="us", language="en", query="")
        cache.set(feed, [{"title": "Article 1"}])
        assert cache.needs_refresh(feed, 60) is False

    def test_get_articles(self, cache):
        feed = self._feed(category="science", country="us", language="en", query="")
        cache.set(feed, [{"title": "Science Story"}])
        articles = cache.get_articles(feed)
        assert len(articles) == 1
        assert articles[0]["title"] == "Science Story"

    def test_query_key_differs_from_category_key(self, cache):
        f_cat = self._feed(category="tech", country="us", language="en", query="")
        f_qry = self._feed(category="tech", country="us", language="en", query="python")
        cache.set(f_cat, [{"title": "Cat"}])
        assert cache.get_articles(f_qry) == []

    def test_ttl_expiry(self, cache):
        feed = self._feed(category="general", country="us", language="en", query="")
        cache.set(feed, [{"title": "Old"}])
        key = cache._make_key(feed)
        cache._data[key]["fetched_at"] = time.time() - 7200
        assert cache.needs_refresh(feed, 60) is True

    def test_persists_to_disk(self, cache, tmp_path):
        feed = self._feed(category="general", country="us", language="en", query="")
        cache.set(feed, [{"title": "Saved"}])
        from channels.headlines.models import HeadlineCache
        c2 = HeadlineCache(tmp_path / "cache.json")
        assert c2.get_articles(feed)[0]["title"] == "Saved"

    def test_corrupt_file_starts_empty(self, tmp_path):
        p = tmp_path / "cache.json"
        p.write_text("{{not json}}")
        from channels.headlines.models import HeadlineCache
        c = HeadlineCache(p)
        feed = self._feed(category="general", country="us", language="en", query="")
        assert c.needs_refresh(feed, 60) is True

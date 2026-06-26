"""mimir_utils.py — Shared utilities for Mimir content source channels.

VENDORING
---------
This file is designed to be copied directly into your plugin directory:

    cp mimir_utils.py my-channel/channels/my_channel/

Then import from it directly:

    from mimir_utils import JsonCache, JsonStore, SettingsMixin, http_session, safe_fetch

No pip install required. This file has no dependencies beyond Python stdlib + requests.

PIP INSTALL
-----------
If you're using the full mimir-source-sdk package:

    from mimir_source_sdk import JsonCache, JsonStore, SettingsMixin, http_session, safe_fetch

Both import styles expose the same classes and functions.

CONTENTS
--------
  SettingsMixin   — dataclass mixin: to_dict(), to_public_dict(), from_dict()
  JsonCache       — JSON-file-backed key/value cache with TTL support
  JsonStore       — JSON-file-backed CRUD list with auto-UUID
  http_session()  — requests.Session factory with Mimir User-Agent
  safe_fetch()    — GET wrapper returning (data, error) instead of raising

VERSION
-------
Keep this in sync with mimir-source-sdk version.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_USER_AGENT = "Mimir-Channel/1.0 (https://github.com/ryanlane/mimir-source-sdk)"


# ---------------------------------------------------------------------------
# SettingsMixin
# ---------------------------------------------------------------------------

class SettingsMixin:
    """Mixin for dataclass-based Settings classes.

    Provides ``to_dict()``, ``to_public_dict()`` (masks secret fields), and
    ``from_dict()`` (ignores unknown keys, safe for forward-compat).

    Usage::

        from dataclasses import dataclass

        @dataclass
        class Settings(SettingsMixin):
            api_key: str = ""
            cache_minutes: int = 30
            city: str = "New York"

        s = Settings.from_dict({"api_key": "abc123", "city": "Boston", "unknown": True})
        s.to_public_dict()
        # {"api_key": "••••••••3", "cache_minutes": 30, "city": "Boston"}

    Override ``_secret_fields`` on the class to mask additional fields::

        @dataclass
        class Settings(SettingsMixin):
            token: str = ""
            _secret_fields: ClassVar[set[str]] = {"token"}
    """

    _secret_fields: ClassVar[set[str]] = {"api_key", "token", "secret", "password", "access_token"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[arg-type]

    def to_public_dict(self) -> dict[str, Any]:
        """Return settings dict with secret fields masked."""
        d = self.to_dict()
        for field in self._secret_fields:
            v = d.get(field)
            if v and isinstance(v, str) and v.strip():
                d[field] = "••••••••" + v[-4:] if len(v) > 4 else "••••••••"
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SettingsMixin":
        """Construct from a dict, silently ignoring unknown keys."""
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# JsonCache
# ---------------------------------------------------------------------------

class JsonCache:
    """JSON-file-backed key/value cache with TTL support.

    Subclass and override ``_make_key()`` to derive cache keys from your
    domain arguments::

        class WeatherCache(JsonCache):
            def _make_key(self, lat, lon, units):
                return f"{lat:.2f}_{lon:.2f}_{units}"

        cache = WeatherCache(data_dir / "weather_cache.json")

        if cache.needs_refresh(lat, lon, units, ttl_minutes=30):
            data = fetch_from_api(lat, lon, units)
            cache.set(data, lat, lon, units)

        entry = cache.get(lat, lon, units)

    The cache file is written atomically on every ``set()`` call. Corrupt
    files are silently discarded and the cache starts empty.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = self._load()

    # ------------------------------------------------------------------
    # Override

    def _make_key(self, *args: Any, **kwargs: Any) -> str:
        """Derive a string cache key from your domain arguments.

        The default joins positional args with ``_``. Override for richer keys.
        """
        return "_".join(str(a) for a in args)

    # ------------------------------------------------------------------
    # Public API

    def get(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Return the cached entry, or None if missing."""
        return self._data.get(self._make_key(*args, **kwargs))

    def set(self, value: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        """Store *value* under the key derived from *args/kwargs*."""
        key = self._make_key(*args, **kwargs)
        self._data[key] = {**value, "_cached_at": time.time()}
        self._save()

    def needs_refresh(self, *args: Any, ttl_minutes: int = 60, **kwargs: Any) -> bool:
        """Return True if the entry is missing or older than *ttl_minutes*."""
        entry = self.get(*args, **kwargs)
        if not entry:
            return True
        return time.time() - entry.get("_cached_at", 0) > ttl_minutes * 60

    def invalidate(self, *args: Any, **kwargs: Any) -> None:
        """Remove a specific entry."""
        key = self._make_key(*args, **kwargs)
        if key in self._data:
            del self._data[key]
            self._save()

    def clear(self) -> None:
        """Wipe the entire cache."""
        self._data = {}
        self._save()

    # ------------------------------------------------------------------
    # Override (optional)

    def _empty_state(self) -> dict[str, Any]:
        """Return the initial empty state when no cache file exists.

        Override when your cache uses a nested structure rather than a flat dict::

            class PosterCache(JsonCache):
                def _empty_state(self):
                    return {"sources": {}}
        """
        return {}

    # ------------------------------------------------------------------
    # Internal

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                logger.warning("JsonCache: corrupt cache file %s — starting empty", self._path)
        return self._empty_state()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, default=str))


# ---------------------------------------------------------------------------
# JsonStore
# ---------------------------------------------------------------------------

class JsonStore(Generic[T]):
    """JSON-file-backed CRUD store for a typed list of items.

    Subclass and implement ``_from_dict()``, ``_to_dict()``, and optionally
    ``_new_item()``::

        @dataclass
        class City:
            id: str
            name: str
            lat: float
            lon: float

        class CityStore(JsonStore[City]):
            def _from_dict(self, d):
                return City(**d)

            def _to_dict(self, item):
                return asdict(item)

            def _new_item(self, data):
                return City(id=str(uuid.uuid4()), **data)

        store = CityStore(data_dir / "cities.json")
        city  = store.create({"name": "Boston", "lat": 42.36, "lon": -71.06})
        store.update(city.id, {"name": "Boston, MA"})
        store.delete(city.id)

    Auto-assigns UUIDs to any item missing an ``id`` field on load (forward-
    compatibility for data written before IDs were added).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._items: list[T] = self._load()

    # ------------------------------------------------------------------
    # Override these

    def _from_dict(self, d: dict[str, Any]) -> T:
        """Deserialize a dict into your item type."""
        raise NotImplementedError

    def _to_dict(self, item: T) -> dict[str, Any]:
        """Serialize your item type to a dict."""
        raise NotImplementedError

    def _new_item(self, data: dict[str, Any]) -> T:
        """Create a brand-new item from user-supplied data.

        The default injects a UUID ``id`` and delegates to ``_from_dict``.
        Override if your item has required fields or different construction.
        """
        return self._from_dict({"id": str(uuid.uuid4()), **data})

    def _get_id(self, item: T) -> str:
        """Return the item's ID. Override if the field isn't named ``id``."""
        return item.id  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # CRUD

    def all(self) -> list[T]:
        return list(self._items)

    def get(self, item_id: str) -> T | None:
        return next((x for x in self._items if self._get_id(x) == item_id), None)

    def create(self, data: dict[str, Any]) -> T:
        item = self._new_item(data)
        self._items.append(item)
        self._save()
        return item

    def update(self, item_id: str, data: dict[str, Any]) -> T | None:
        existing = self.get(item_id)
        if not existing:
            return None
        merged = {**self._to_dict(existing), **data, "id": item_id}
        updated = self._from_dict(merged)
        self._items = [updated if self._get_id(x) == item_id else x for x in self._items]
        self._save()
        return updated

    def delete(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [x for x in self._items if self._get_id(x) != item_id]
        if len(self._items) < before:
            self._save()
            return True
        return False

    def count(self) -> int:
        return len(self._items)

    # ------------------------------------------------------------------
    # Internal

    def _load(self) -> list[T]:
        if not self._path.exists():
            return []
        try:
            raw: list[dict] = json.loads(self._path.read_text())
            items = []
            dirty = False
            for d in raw:
                if not d.get("id"):
                    d["id"] = str(uuid.uuid4())
                    dirty = True
                items.append(self._from_dict(d))
            if dirty:
                self._path.write_text(json.dumps([self._to_dict(x) for x in items], indent=2))
            return items
        except Exception:
            logger.warning("JsonStore: could not load %s — starting empty", self._path)
            return []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps([self._to_dict(x) for x in self._items], indent=2))


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def http_session(user_agent: str = _DEFAULT_USER_AGENT) -> "requests.Session":
    """Return a ``requests.Session`` with the Mimir User-Agent set.

    Usage::

        session = http_session()
        resp = session.get(url, params=params, timeout=10)
        resp.raise_for_status()
    """
    if not _REQUESTS_AVAILABLE:
        raise ImportError("requests is required for http_session(). pip install requests")
    import requests as _req
    s = _req.Session()
    s.headers["User-Agent"] = user_agent
    return s


def safe_fetch(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
    session: Any = None,
) -> tuple[Any, str | None]:
    """GET *url* and return ``(parsed_json, None)`` on success or ``(None, error_str)`` on failure.

    Never raises. Errors are returned as human-readable strings suitable for
    surfacing in the Mimir UI or logging.

    Usage::

        data, err = safe_fetch(url, params={"q": city, "appid": key})
        if err:
            logger.error("Fetch failed: %s", err)
            return None
        process(data)
    """
    if not _REQUESTS_AVAILABLE:
        return None, "requests library is not installed"

    import requests as _req

    try:
        requester = session if session is not None else _req
        resp = requester.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json(), None
    except _req.exceptions.ConnectionError:
        host = url.split("/")[2] if "//" in url else url
        return None, f"Could not connect to {host} — check network"
    except _req.exceptions.Timeout:
        return None, f"Request timed out after {timeout}s"
    except _req.exceptions.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        return None, f"HTTP {code}: {exc.response.reason if exc.response is not None else str(exc)}"
    except Exception as exc:
        return None, str(exc)


def validate_key_nonempty(key: str, field_name: str = "API key") -> dict[str, Any]:
    """Return ``{valid: True}`` or ``{valid: False, error: str}`` for a basic non-empty check.

    Channels that need real API validation (e.g. a test HTTP call) should
    implement their own ``validate_api_key()`` on top of this.
    """
    if not key or not key.strip():
        return {"valid": False, "error": f"{field_name} is empty"}
    return {"valid": True}

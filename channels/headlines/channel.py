"""Headlines channel for Mimir Platform.

Fetches news from NewsAPI and renders a single headline image. Each
configured feed targets a category, keyword, or country — assign different
feeds to different screens.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .models import FeedStore, HeadlineCache, HeadlinesFeed, Settings
from .web_renderer import HeadlinesHtmlRenderer
from . import fetcher as _fetcher

_PLUGIN_DIR = Path(__file__).parent
logger = logging.getLogger("mimir.channels.headlines")

_PREVIEW_SIZES = {
    "landscape": (800, 480),
    "portrait":  (480, 800),
    "square":    (600, 600),
    "auto":      (800, 480),
}


class HeadlinesChannel:
    def __init__(self, channel_dir: str):
        self.channel_dir = Path(channel_dir)
        self.data_dir = self.channel_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        plugin_json = self.channel_dir / "plugin.json"
        self._meta: Dict[str, Any] = {}
        if plugin_json.exists():
            try:
                self._meta = json.loads(plugin_json.read_text())
            except Exception:
                pass

        self.settings = self._load_settings()
        self.store = FeedStore(self.data_dir / "feeds.json")
        self.cache = HeadlineCache(self.data_dir / "headline_cache.json")
        self.renderer = HeadlinesHtmlRenderer()
        self.last_error: Optional[str] = None
        self._rotation: Dict[str, int] = self._load_rotation()

        logger.info("[Headlines] Initialized at %s, %d feeds", self.channel_dir, len(self.store.all()))

    @property
    def id(self) -> str:
        return self._meta.get("id", "com.mimir.headlines")

    # ------------------------------------------------------------------
    # Rotation state

    def _rotation_path(self) -> Path:
        return self.data_dir / "rotation_state.json"

    def _load_rotation(self) -> Dict[str, int]:
        p = self._rotation_path()
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
        return {}

    def _save_rotation(self) -> None:
        self._rotation_path().write_text(json.dumps(self._rotation, indent=2))

    # ------------------------------------------------------------------
    # Settings

    def _settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def _load_settings(self) -> Settings:
        p = self._settings_path()
        if p.exists():
            try:
                return Settings.from_dict(json.loads(p.read_text()))
            except Exception as exc:
                logger.warning("[Headlines] Settings load failed: %s", exc)
        return Settings()

    def _save_settings(self) -> None:
        self._settings_path().write_text(json.dumps(self.settings.to_dict(), indent=2))

    # ------------------------------------------------------------------
    # Data fetching (with cache)

    async def _get_articles(self, feed: HeadlinesFeed) -> List[Dict[str, Any]]:
        """Returns cached or freshly-fetched article list."""
        if not self.settings.api_key:
            raise ValueError("NewsAPI key not configured")

        if not self.cache.needs_refresh(feed, self.settings.cache_minutes):
            return self.cache.get_articles(feed)

        try:
            articles = await asyncio.to_thread(
                _fetcher.fetch_headlines,
                self.settings.api_key,
                feed.category,
                feed.query,
                feed.country,
                feed.language,
                feed.sort_by,
            )
            self.cache.set(feed, articles)
            self.last_error = None
            return articles
        except Exception as exc:
            self.last_error = str(exc)
            stale = self.cache.get_articles(feed)
            if stale:
                logger.warning("[Headlines] Fetch failed, using stale cache: %s", exc)
                return stale
            raise

    async def _get_image(self, article: Dict[str, Any], feed: HeadlinesFeed) -> Optional[bytes]:
        """Download the article image if enabled; silently returns None on failure."""
        if not feed.show_image:
            return None
        url = article.get("urlToImage") or ""
        if not url:
            return None
        try:
            return await asyncio.to_thread(_fetcher.fetch_image, url)
        except Exception as exc:
            logger.debug("[Headlines] Image fetch error: %s", exc)
            return None

    async def _render(
        self,
        feed: HeadlinesFeed,
        width: int,
        height: int,
    ) -> bytes:
        articles = await self._get_articles(feed)
        if not articles:
            raise ValueError("No articles returned for this feed")

        current = self._rotation.get(feed.id, 0) % len(articles)
        self._rotation[feed.id] = (current + 1) % len(articles)
        self._save_rotation()

        article = articles[current]

        image_bytes = await self._get_image(article, feed)
        return await self.renderer.render(article, feed, width, height, image_bytes)

    # ------------------------------------------------------------------
    # Mimir channel protocol

    def get_manifest(self) -> Dict[str, Any]:
        feeds = self.store.all()
        return {
            "id": self.id,
            "name": self._meta.get("name", "Headlines"),
            "version": self._meta.get("version", "1.0.0"),
            "description": self._meta.get("description", ""),
            "icon": self._meta.get("icon", "newspaper"),
            "capabilities": {
                "supports_upload": False,
                "supports_subchannels": True,
            },
            "ui": {
                "components": {"manager": f"/api/channels/{self.id}/ui/manage.esm.js"},
                "elements":   {"manager": "x-headlines-manager"},
            },
            "healthy": bool(self.settings.api_key) and self.last_error is None,
            "setup_required": not bool(self.settings.api_key),
            "display_count": len(feeds),
        }

    def supports_subchannels(self) -> bool:
        return True

    def get_subchannels(self) -> List[Dict[str, Any]]:
        return [
            {
                "id":       f.id,
                "name":     f.name,
                "image_count": 1,
                "type":     "subchannel",
                "category": f.category,
                "query":    f.query,
                "country":  f.country,
                "layout":   f.layout,
                "theme":    f.theme,
            }
            for f in self.store.all()
        ]

    def get_subchannel(self, subchannel_id: str) -> Optional[Dict[str, Any]]:
        f = self.store.get(subchannel_id)
        return f.to_dict() if f else None

    # ------------------------------------------------------------------
    # Image request

    async def request_image(self, request_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.settings.api_key:
            return {"success": False, "error": "NewsAPI key not configured — open the channel manager"}

        data = request_data or {}
        feed_id = (
            data.get("subchannel_id")
            or data.get("gallery_id")
            or (data.get("settings") or {}).get("subChannelId")
        )

        feed = self.store.get(feed_id) if feed_id else (self.store.all() or [None])[0]
        if not feed:
            return {"success": False, "error": "No feed configured — add one in the channel manager"}

        resolution = (data.get("settings") or {}).get("resolution") or data.get("resolution")
        width, height = 800, 480
        if resolution and len(resolution) == 2:
            try:
                width, height = int(resolution[0]), int(resolution[1])
            except (TypeError, ValueError):
                pass

        try:
            img_bytes = await self._render(feed, width, height)
            return {
                "success": True,
                "bytes": img_bytes,
                "content_type": "image/jpeg",
                "preferred_transport": "bytes",
            }
        except Exception as exc:
            logger.error("[Headlines] Render failed: %s", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Router

    def get_router(self) -> APIRouter:
        router = APIRouter()
        _ui_dir = _PLUGIN_DIR / "ui"

        @router.get("/ui/{filename:path}")
        async def serve_ui(filename: str):
            file_path = (_ui_dir / filename).resolve()
            try:
                file_path.relative_to(_ui_dir.resolve())
            except ValueError:
                raise HTTPException(403, "Forbidden")
            if not file_path.exists():
                raise HTTPException(404, f"Not found: {filename}")
            ctype = "application/javascript" if filename.endswith(".js") else "text/css"
            return Response(content=file_path.read_bytes(), media_type=ctype,
                            headers={"Cache-Control": "no-cache"})

        @router.get("/manifest")
        async def get_manifest():
            return JSONResponse(self.get_manifest())

        @router.get("/subchannels")
        async def list_subchannels():
            return JSONResponse(self.get_subchannels())

        @router.post("/subchannels")
        async def create_feed(request: Request):
            body = await request.json()
            feed = self.store.create(body)
            return JSONResponse(feed.to_dict(), status_code=201)

        @router.get("/subchannels/{feed_id}")
        async def get_feed(feed_id: str):
            f = self.store.get(feed_id)
            if not f:
                raise HTTPException(404, "Feed not found")
            return JSONResponse(f.to_dict())

        @router.put("/subchannels/{feed_id}")
        async def update_feed(feed_id: str, request: Request):
            body = await request.json()
            f = self.store.update(feed_id, body)
            if not f:
                raise HTTPException(404, "Feed not found")
            return JSONResponse(f.to_dict())

        @router.delete("/subchannels/{feed_id}")
        async def delete_feed(feed_id: str):
            if not self.store.delete(feed_id):
                raise HTTPException(404, "Feed not found")
            return JSONResponse({"success": True})

        @router.get("/subchannels/{feed_id}/preview")
        async def preview_feed(feed_id: str, w: int = 0, h: int = 0):
            f = self.store.get(feed_id)
            if not f:
                raise HTTPException(404, "Feed not found")
            pw, ph = (w, h) if w and h else _PREVIEW_SIZES.get(f.layout, _PREVIEW_SIZES["auto"])
            if not self.settings.api_key:
                raise HTTPException(400, "API key not configured")
            try:
                img = await self._render(f, pw, ph)
                return Response(content=img, media_type="image/jpeg",
                                headers={"Cache-Control": "no-store"})
            except Exception as exc:
                raise HTTPException(500, str(exc))

        @router.post("/preview")
        async def preview_config(request: Request):
            """Render a preview from an unsaved config (used during add/edit flow)."""
            body = await request.json()
            config_data = body.get("config", body)
            pw = int(body.get("w", 800))
            ph = int(body.get("h", 480))

            if not self.settings.api_key:
                raise HTTPException(400, "API key not configured")

            try:
                feed = HeadlinesFeed.from_dict(config_data)
            except Exception as exc:
                raise HTTPException(422, f"Invalid config: {exc}")

            try:
                img = await self._render(feed, pw, ph)
                return Response(content=img, media_type="image/jpeg",
                                headers={"Cache-Control": "no-store"})
            except Exception as exc:
                raise HTTPException(500, str(exc))

        @router.get("/settings")
        async def get_settings():
            return JSONResponse(self.settings.to_public_dict())

        @router.put("/settings")
        async def update_settings(request: Request):
            body = await request.json()
            if "api_key" in body and not str(body["api_key"]).startswith("••••"):
                self.settings.api_key = body["api_key"]
            if "cache_minutes" in body:
                self.settings.cache_minutes = int(body["cache_minutes"])
            self._save_settings()
            return JSONResponse({"success": True, "settings": self.settings.to_public_dict()})

        @router.post("/validate-key")
        async def validate_key(request: Request):
            body = await request.json()
            key = body.get("api_key", "").strip()
            result = await asyncio.to_thread(_fetcher.validate_api_key, key)
            if result["valid"]:
                self.settings.api_key = key
                self._save_settings()
            return JSONResponse(result)

        @router.get("/status")
        async def get_status():
            try:
                from app.services.html_renderer import html_renderer_service
                html_available = html_renderer_service.available
            except Exception:
                html_available = False
            return JSONResponse({
                "feeds": self.get_subchannels(),
                "last_error": self.last_error,
                "html_renderer_available": html_available,
                "setup_required": not bool(self.settings.api_key),
                "settings": self.settings.to_public_dict(),
            })

        @router.post("/request-image")
        async def request_image_binary(request: Request):
            body: Dict[str, Any] = {}
            try:
                body = await request.json()
            except Exception:
                pass
            result = await self.request_image(body)
            if not result.get("success"):
                raise HTTPException(500, result.get("error", "render failed"))
            img_bytes = result.get("bytes")
            if not img_bytes:
                raise HTTPException(500, "No image produced")
            fingerprint = hashlib.sha256(img_bytes).hexdigest()[:32]
            return Response(
                content=img_bytes,
                media_type="image/jpeg",
                headers={"X-Content-Fingerprint": fingerprint, "Cache-Control": "no-store"},
            )

        logger.info("[Headlines] Router registered, %d feeds", len(self.store.all()))
        return router


ChannelClass = HeadlinesChannel

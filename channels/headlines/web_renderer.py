"""HTML-based headlines renderer.

Builds a Jinja2 template from article data and renders it to JPEG bytes
via the server's shared html_renderer_service (Playwright/Chromium).
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("mimir.channels.headlines.web_renderer")

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_CATEGORY_LABELS = {
    "general":       "News",
    "business":      "Business",
    "entertainment": "Entertainment",
    "health":        "Health",
    "science":       "Science",
    "sports":        "Sports",
    "technology":    "Technology",
}


def _time_ago(published_at: str) -> str:
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        seconds = int((datetime.now(timezone.utc) - dt).total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            m = seconds // 60
            return f"{m} min ago"
        if seconds < 86400:
            h = seconds // 3600
            return f"{h}h ago"
        d = seconds // 86400
        return f"{d}d ago"
    except Exception:
        return ""


def _format_date(published_at: str) -> str:
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        return dt.strftime("%A, %B %-d, %Y")
    except Exception:
        return ""


class HeadlinesHtmlRenderer:
    def __init__(self) -> None:
        self._jinja = self._make_jinja()

    def _make_jinja(self):
        try:
            from jinja2 import Environment, FileSystemLoader, select_autoescape
            env = Environment(
                loader=FileSystemLoader(str(_TEMPLATE_DIR)),
                autoescape=select_autoescape(["html"]),
            )
            return env
        except ImportError:
            logger.warning("[headlines-renderer] jinja2 not installed — HTML rendering unavailable")
            return None

    def _image_uri(self, image_bytes: Optional[bytes]) -> str:
        if not image_bytes:
            return ""
        b64 = base64.b64encode(image_bytes).decode()
        return f"data:image/jpeg;base64,{b64}"

    def _layout_for(self, cfg, width: int, height: int) -> str:
        layout = cfg.layout
        if layout == "auto":
            aspect = width / height
            layout = "landscape" if aspect >= 1.2 else ("portrait" if aspect <= 0.85 else "square")
        return layout

    def prepare_context(
        self,
        article: Dict[str, Any],
        cfg,
        width: int,
        height: int,
        image_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        published_at = article.get("publishedAt", "")

        category_label = _CATEGORY_LABELS.get(cfg.category, cfg.category.title())
        if cfg.query.strip():
            # Show the search query as the category label
            q = cfg.query.strip()
            category_label = q[:30] + ("…" if len(q) > 30 else "")

        raw_author = article.get("author") or ""
        # NewsAPI sometimes returns a comma-separated list of authors; take first one
        author = raw_author.split(",")[0].strip() if raw_author else ""
        # Truncate very long author strings
        if len(author) > 40:
            author = author[:40] + "…"

        return {
            "headline":  article.get("title", ""),
            "excerpt":   article.get("description", "") if cfg.show_excerpt else "",
            "author":    author if cfg.show_author else "",
            "source":    (article.get("source") or {}).get("name", "") if cfg.show_source else "",
            "time_ago":  _time_ago(published_at) if cfg.show_time else "",
            "date":      _format_date(published_at),
            "category":  category_label,
            "image_uri": self._image_uri(image_bytes) if cfg.show_image else "",
            "theme":     cfg.theme,
            "layout":    self._layout_for(cfg, width, height),
            "width":     width,
            "height":    height,
        }

    async def render(
        self,
        article: Dict[str, Any],
        cfg,
        width: int,
        height: int,
        image_bytes: Optional[bytes] = None,
    ) -> bytes:
        if self._jinja is None:
            raise RuntimeError("jinja2 not installed")

        try:
            from app.services.html_renderer import html_renderer_service, HtmlRendererUnavailableError
        except ImportError as exc:
            raise RuntimeError("html_renderer_service not available") from exc

        if not html_renderer_service.available:
            raise RuntimeError("Chromium not running")

        template = self._jinja.get_template("headlines.html")
        ctx = self.prepare_context(article, cfg, width, height, image_bytes)
        html = template.render(**ctx)

        # networkidle lets any web fonts finish loading before the screenshot
        return await html_renderer_service.render(html, width, height, wait_until="networkidle")

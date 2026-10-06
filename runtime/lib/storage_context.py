"""Explicit storage roots for production, QA, and unit-test reader state.

The reader used to treat ``Path.home()/Library/Caches`` as an implicit global.
That made an otherwise isolated controller or translation test write into the
installed application's cache.  A StorageContext is created before any
controller-owned service and is passed through every cache consumer instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from file_access import application_home


DEFAULT_CACHE_BUDGET = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class StorageContext:
    cache_root: Path
    thumbnail_root: Path
    settings_scope: str
    purpose: str = "production"
    cache_budget: int = DEFAULT_CACHE_BUDGET

    def __post_init__(self):
        cache = Path(self.cache_root).expanduser().resolve()
        thumbnails = Path(self.thumbnail_root).expanduser().resolve()
        purpose = str(self.purpose or "").strip().lower()
        if purpose not in {"production", "test", "qa", "probe"}:
            raise ValueError("reader storage purpose is invalid")
        if cache == thumbnails or cache in thumbnails.parents or thumbnails in cache.parents:
            raise ValueError("EPUB cache and cover thumbnails need independent roots")
        if int(self.cache_budget) < 16 * 1024 * 1024:
            raise ValueError("reader cache budget is too small")
        object.__setattr__(self, "cache_root", cache)
        object.__setattr__(self, "thumbnail_root", thumbnails)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "cache_budget", int(self.cache_budget))

    @classmethod
    def production(cls) -> "StorageContext":
        base = application_home() / "Library/Caches/WhaleRead"
        return cls(
            cache_root=base / "epub-v1",
            thumbnail_root=base / "covers-v1",
            settings_scope="Sindy/HyNovelTranslator",
            purpose="production",
        )

    @classmethod
    def isolated(cls, root, *, purpose="test", settings_scope="") -> "StorageContext":
        base = Path(root).expanduser().resolve()
        return cls(
            cache_root=base / "epub-cache",
            thumbnail_root=base / "cover-cache",
            settings_scope=str(settings_scope or base),
            purpose=purpose,
        )

    def assert_test_safe(self) -> None:
        """Fail closed if a non-production context points at production data."""
        if self.purpose == "production":
            return
        production = self.production()
        for candidate in (self.cache_root, self.thumbnail_root):
            if candidate in {production.cache_root, production.thumbnail_root}:
                raise RuntimeError("test/QA storage points at the production reader cache")

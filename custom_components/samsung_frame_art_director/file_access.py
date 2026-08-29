"""Trusted local-file helpers for artwork exposed through Home Assistant."""

from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class UnsafeLocalPathError(ValueError):
    """Raised when a local artwork path leaves the configured trust boundary."""


def _canonical(path: str | os.PathLike[str]) -> Path:
    """Return a normalized path with existing symlinks resolved."""
    return Path(path).expanduser().resolve(strict=False)


def allowed_local_roots(hass: HomeAssistant) -> tuple[Path, ...]:
    """Return the canonical roots from which artwork may be read or deleted."""
    roots = {
        _canonical(hass.config.path()),
        _canonical("/media"),
    }

    media_dirs = getattr(hass.config, "media_dirs", {}) or {}
    roots.update(_canonical(path) for path in media_dirs.values())
    return tuple(roots)


def ensure_allowed_local_path(
    hass: HomeAssistant,
    path: str | os.PathLike[str],
) -> Path:
    """Resolve a path and reject traversal, prefix collisions, and symlink escapes."""
    candidate = _canonical(path)
    if not any(candidate.is_relative_to(root) for root in allowed_local_roots(hass)):
        raise UnsafeLocalPathError("Artwork path is outside the allowed local directories")
    return candidate


def resolve_upload_source(hass: HomeAssistant, source: str) -> Path:
    """Resolve the documented local upload aliases inside the trusted roots."""
    expanded = os.path.expanduser(source)
    if expanded == "/config":
        candidate = Path(hass.config.path())
    elif expanded.startswith("/config/"):
        candidate = Path(hass.config.path(expanded.removeprefix("/config/")))
    elif expanded == "/media" or expanded.startswith("/media/"):
        candidate = Path(expanded)
    elif Path(expanded).is_absolute():
        candidate = Path(expanded)
    else:
        candidate = Path("/media/frame/library") / expanded
    return ensure_allowed_local_path(hass, candidate)


def media_identifier(path: str | os.PathLike[str]) -> str:
    """Create a stable opaque identifier without exposing the filesystem path."""
    normalized = os.path.normcase(str(_canonical(path))).encode("utf-8")
    return f"local-{hashlib.sha256(normalized).hexdigest()}"


def is_local_media_identifier(value: str) -> bool:
    """Return whether a string has the exact opaque local-art ID format."""
    prefix = "local-"
    digest = value.removeprefix(prefix)
    return (
        value.startswith(prefix)
        and len(digest) == hashlib.sha256().digest_size * 2
        and all(character in "0123456789abcdef" for character in digest)
    )


def image_content_type(path: str | os.PathLike[str]) -> str:
    """Return an image response type derived from the trusted file extension."""
    guessed, _encoding = mimetypes.guess_type(str(path))
    if guessed in {"image/jpeg", "image/png", "image/webp"}:
        return guessed
    return "application/octet-stream"

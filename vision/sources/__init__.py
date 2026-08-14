"""Видео көздері (source-agnostic қабат)."""

from __future__ import annotations

from ..config import Config
from .base import Frame, VideoSource
from .phone import PhoneIPWebcamSource
from .rtsp import RtspSource
from .videofile import VideoFileSource

__all__ = [
    "Frame",
    "VideoSource",
    "PhoneIPWebcamSource",
    "VideoFileSource",
    "RtspSource",
    "create_source",
]


def create_source(cfg: Config) -> VideoSource:
    """Баптауға қарай видео көзін жасау."""
    kind = (cfg.source or "phone").lower()
    if kind == "phone":
        return PhoneIPWebcamSource(cfg)
    if kind == "file":
        return VideoFileSource(cfg)
    if kind == "rtsp":
        return RtspSource(cfg)
    raise ValueError(f"Белгісіз видео көзі: {kind!r} (phone | file | rtsp)")

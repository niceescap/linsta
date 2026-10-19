"""Provider-independent, immutable metadata contracts. No network or persistence.

Provider fields are optional: a YouTube channel is not assumed to be an artist.
Publication dates retain provider precision. Duration is the source duration in
seconds, never proof of the duration or validity of a separately uploaded MP3.
Artwork URLs are data, not authorization to fetch arbitrary remote resources.
"""
from dataclasses import dataclass
from typing import Literal, Optional


Platform = Literal['youtube', 'spotify']
DatePrecision = Literal['year', 'month', 'day', 'second']


@dataclass(frozen=True)
class NormalizedSource:
    platform: Platform
    source_id: str
    source_url: str
    canonical_url: str


@dataclass(frozen=True)
class TrackMetadata:
    # Construct source with normalize_source_url(), not directly from user data.
    source: NormalizedSource
    title: Optional[str] = None
    artist: Optional[str] = None
    channel: Optional[str] = None
    duration_seconds: Optional[float] = None
    artwork_url: Optional[str] = None
    album: Optional[str] = None
    publication_date: Optional[str] = None
    publication_date_precision: Optional[DatePrecision] = None
    youtube_view_count: Optional[int] = None
    description: Optional[str] = None

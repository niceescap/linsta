"""Source URL validation and metadata contracts; no API calls or side effects."""
from .errors import MetadataRetrievalError, MetadataURLValidationError
from .models import NormalizedSource, TrackMetadata
from .urls import normalize_source_url
from .youtube import fetch_youtube_metadata
from .spotify import SpotifyMetadataClient

__all__ = ['MetadataURLValidationError', 'MetadataRetrievalError', 'NormalizedSource',
           'TrackMetadata', 'normalize_source_url', 'fetch_youtube_metadata', 'SpotifyMetadataClient']

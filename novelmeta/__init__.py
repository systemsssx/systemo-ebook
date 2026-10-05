# -*- coding: utf-8 -*-
"""novelmeta — resolve a web-novel title to its author and cover image."""

from .cover import CoverError, CoverResult, download_cover
from .model import Candidate, norm_title, title_similarity
from .resolver import Resolution, parse_ref, resolve, resolve_ref
from .sources import get_source, source_names

__version__ = "1.0.0"

__all__ = [
    "resolve",
    "resolve_ref",
    "parse_ref",
    "Resolution",
    "Candidate",
    "download_cover",
    "CoverResult",
    "CoverError",
    "source_names",
    "get_source",
    "norm_title",
    "title_similarity",
    "__version__",
]

"""Word-level alignment backends (Phase 13D).

Backends produce measured word timestamps when a real aligner/runtime is available. They must fail
closed when unavailable; they must never fabricate word boundaries.
"""

from .base import AlignerResult, WordAlignment, get_aligner

__all__ = ["AlignerResult", "WordAlignment", "get_aligner"]

"""Release packaging support (Phase 10): version checks, source manifest, local builds.

Nothing here uploads or publishes anything. All build output stays on local disk.
"""

from creator_core.release import build, checker

__all__ = ["build", "checker"]
